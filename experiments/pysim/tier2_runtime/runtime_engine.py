"""
experiments/pysim/tier2_runtime/runtime_engine.py
Integrated WASM Tiered Tracing Runtime Engine for pysim.
Coordinates the Tier 2 interpreter, vSoC execution, and an injected Tier 3
JIT runtime service through a narrow execution contract. The card-marking,
history, hotspot queue, and cache state are owned by Tier 3.
mirroring docs/components/tier2_runtime/runtime_vsoc.md and
docs/components/tier3_executer/jit_compiler.md.
Execution model:
  Interpreter execution:
    -> at basic-block head PCs: record card index into HistoryRing
    -> 2-bit card state: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)
    -> on yield/idle: drain HistoryRing, promote HOT cards, push trace heads to LIFO compile queue
    -> async/batch JIT compilation into Active cache bank
  JIT trace execution:
    -> lookup in 3-bank cache (Active, Warm, Oldest)
    -> Oldest bank hit triggers immediate Promotion to Active bank
    -> trace chaining with inbound-source unlinking on bank eviction
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Generator, Sequence
from typing import Protocol, TextIO

from config import (
    RUNTIME_DEBUG_REPORT_LINE_CAPACITY,
)
from interop_abi import NativeValueStack
from recovery import Result
from system_containers import StaticVector
from tier3_executer.interpreter.interpreter import (
    RETURN_SENTINEL_IP,
    CallFrame,
    Interpreter,
    InterpreterCall,
    WasmNumber,
)
from virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
)
from wasm_module import BasicBlock, Module

try:
    import tier3_executer.jit.native_trace_call as _native_trace_call
except ImportError:
    # Optional accelerator; the trace descriptor remains the portable fallback.
    _native_trace_call = None


class _Debugger(Protocol):
    halted: bool
    stop_signal: int

    def has_breakpoint(self, pc: int) -> bool: ...

    def sample_pc(self, pc: int) -> None: ...

    def verify_assertions(self, memory: bytearray) -> None: ...


class _RescheduleObserver(Protocol):
    """Tier-neutral callback supplied by the owning COOS scheduler."""

    def observe_reschedule_generation(self) -> bool: ...


class JITTrace(Protocol):
    """Minimal trace view required by the Tier 2 execution loop."""

    head_pc: int
    chain_next: int | None
    has_return_val: bool
    loops_to: int | None
    native_loop_safe: bool
    next_pc: int | None
    raw_addr: int | None
    result_words: int
    stack_words: int
    exec_count: int

    def execute(
        self,
        ctx: ctypes.c_void_p,
        sp: ctypes.c_void_p,
        local_base: ctypes.c_void_p,
        tos: int,
    ) -> None: ...


class JITRuntime(Protocol):
    """Tier 2 contract implemented by a Tier 3 JIT runtime plugin."""

    def register_module(self, module: Module) -> None: ...

    def get_block(self, pc: int) -> BasicBlock | None: ...

    def is_trackable(self, pc: int) -> bool: ...

    def record_block_head(self, pc: int) -> bool: ...

    def on_yield(self) -> None: ...

    def idle_hook(self, budget: int = 4) -> int: ...

    def drain_compile_queue(self) -> int: ...

    def age_step(self) -> int: ...

    def lookup(self, pc: int) -> JITTrace | None: ...

    def find_trace(self, pc: int) -> JITTrace | None: ...

    def chain_length(self, trace: JITTrace) -> int: ...

    def terminal_trace(self, trace: JITTrace) -> JITTrace: ...

    def max_chain_stack_words(self, trace: JITTrace) -> int: ...

    def reset_stats(self) -> None: ...

    def flush_all(self) -> None: ...


__all__ = (
    "JITRuntime",
    "JITTrace",
    "RuntimeEngine",
)


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    __slots__ = (
        "_virq",
        "_virq_interp",
        "debug",
        "jit_runtime",
        "module",
        "reschedule_observer",
        "stat_chain_hits",
        "stat_interp_steps",
        "stat_jit_invocations",
        "stat_native_loop_calls",
        "stat_trace_exits_to_interp",
    )

    def __init__(
        self,
        jit_runtime: JITRuntime | None = None,
        debug: bool = False,
        reschedule_observer: _RescheduleObserver | None = None,
    ):
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env == "1" or debug_env == "true" or debug_env == "yes"
        self.jit_runtime = jit_runtime
        self.stat_interp_steps: int = 0
        self.stat_jit_invocations: int = 0
        self.stat_native_loop_calls: int = 0
        self.stat_chain_hits: int = 0
        self.stat_trace_exits_to_interp: int = 0
        self.reschedule_observer = reschedule_observer
        self.module: Module | None = None
        self._virq: VirqDispatcher | None = None
        self._virq_interp: Interpreter | None = None

    def set_reschedule_observer(self, observer: _RescheduleObserver | None) -> None:
        """Attach the scheduler-owned generation observer without a Tier import."""
        self.reschedule_observer = observer

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        if self.jit_runtime is not None:
            return self.jit_runtime.get_block(pc)
        return self.module.get_block(pc) if self.module is not None else None

    def register_module_blocks(self, module: Module) -> None:
        """Binds the loader-owned immutable block index."""
        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        self._virq = VirqDispatcher(module, self._invoke_virq)
        if self.jit_runtime is not None:
            self.jit_runtime.register_module(module)

    def record_block_head(self, pc: int) -> bool:
        """
        Called by `run()` at each basic-block head that has no compiled
        trace yet. The injected Tier 3 manager decides at module registration
        whether a block is trackable, so the minimum trace length and static
        score are not re-derived on this hot path. Terminal blocks remain
        eligible because a compiled return exits through RETURN_SENTINEL_IP.
        Returns True if exec_counter reached yield_threshold and triggered on_yield.
        """
        if self.jit_runtime is None:
            return False
        return self.jit_runtime.record_block_head(pc)

    def register_virq_dispatcher(
        self, node_id: int, function_index: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Validate a static vIRQ registration for the next safepoint."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.register_dispatcher(node_id, function_index)

    def unregister_virq_dispatcher(
        self, node_id: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Stage removal of a vIRQ registration for the next safepoint."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.unregister_dispatcher(node_id)

    def commit_virq_safepoint(self) -> None:
        """Publish validated vIRQ registrations at the execution boundary."""
        if self._virq is not None:
            self._virq.commit_safepoint()

    def dispatch_interrupt_event(self, event: InterruptEvent) -> DispatchResult:
        """Dispatch one COOS event through the vIRQ hierarchy."""
        if self._virq is None:
            return DispatchResult(VirqDispatchResult.REJECT, "VIRQ_UNAVAILABLE")
        return self._virq.dispatch_interrupt_event(event)

    def _invoke_virq(
        self,
        function_index: int,
        vector_id: int,
        source_id: int,
        cause_code: int,
        payload0: int,
        payload1: int,
    ) -> int:
        if self._virq_interp is None:
            return int(VirqDispatchResult.REJECT)
        results = self._virq_interp.call(
            function_index,
            (vector_id, source_id, cause_code, payload0, payload1),
        )
        if not results:
            return int(VirqDispatchResult.REJECT)
        return results[0] & 0xFFFF_FFFF

    def on_yield(self) -> None:
        """Delegate hotspot promotion to the injected Tier 3 manager."""
        if self.jit_runtime is not None:
            self.jit_runtime.on_yield()

    def age_step(self) -> int:
        """Delegate one cache-rotation aging step to Tier 3."""
        return self.jit_runtime.age_step() if self.jit_runtime is not None else 0

    def idle_hook(self, budget: int = 4) -> int:
        """
        Drains the LIFO compile queue during COOS idle_hook. {JIT_ReverseCompilationOrder}
                Compiling in reverse order increases immediate chaining probability.
        """

        return self.jit_runtime.idle_hook(budget) if self.jit_runtime is not None else 0

    def drain_compile_queue(self) -> int:
        return self.jit_runtime.drain_compile_queue() if self.jit_runtime is not None else 0

    def reset_stats(self) -> None:
        """Resets execution statistics counters."""
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_chain_hits = 0
        self.stat_trace_exits_to_interp = 0
        if self.jit_runtime is not None:
            self.jit_runtime.reset_stats()

    def dump_internal_state(self, file: TextIO | None = None) -> str:
        """Dump execution counters without owning Tier 3 cache diagnostics."""
        lines: StaticVector[str] = StaticVector(capacity=RUNTIME_DEBUG_REPORT_LINE_CAPACITY)
        lines.append("=" * 80)
        lines.append("                  RuntimeEngine Execution Dump                  ")
        lines.append("=" * 80)

        total_blocks = self.stat_interp_steps + self.stat_jit_invocations
        jit_pct = (self.stat_jit_invocations / total_blocks * 100.0) if total_blocks > 0 else 0.0
        chain_pct = (
            (self.stat_chain_hits / self.stat_jit_invocations * 100.0)
            if self.stat_jit_invocations > 0
            else 0.0
        )

        lines.append("[1. Execution Summary]")
        lines.append(f"  * Total Block Executions:    {total_blocks:,}")
        lines.append(
            f"    - Interpreter Steps:       {self.stat_interp_steps:,} ({(100.0 - jit_pct):.1f}%)"
        )
        lines.append(
            f"    - JIT Invocations:         {self.stat_jit_invocations:,} ({jit_pct:.1f}%)"
        )
        lines.append("  * JIT Chaining Performance:")
        lines.append(
            f"    - Chained Invocations:     {self.stat_chain_hits:,} ({chain_pct:.1f}% of JIT runs)"
        )
        lines.append(f"    - Exits to Interpreter:    {self.stat_trace_exits_to_interp:,}")
        lines.append(
            "  * Tier 3 JIT manager:         "
            + ("attached" if self.jit_runtime is not None else "disabled")
        )
        lines.append("=" * 80)

        output_str = "\n".join(lines) + "\n"
        target_file = file if file is not None else sys.stderr
        target_file.write(output_str)
        target_file.flush()
        return output_str

    def run(
        self,
        interp: Interpreter,
        func_index: int,
        args: Sequence[int],
        idle_budget: int = 4,
    ) -> StaticVector[WasmNumber]:
        """Execute a function through the configured interpreter/JIT boundary."""
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp
        return self._drive_call(interp, interp.start(func_index, args), idle_budget)

    def _drive_call(
        self, interp: Interpreter, call_state: InterpreterCall, idle_budget: int
    ) -> StaticVector[WasmNumber]:
        """Template execution driver shared by RuntimeEngine and JITInterpreter."""
        while not call_state.finished:
            assert call_state._frame is not None
            current_ip = call_state._ip
            current_frame = call_state._frame
            assert current_frame is not None
            if current_ip == RETURN_SENTINEL_IP:
                call_state = interp.step(call_state)
                continue
            pc = call_state.current_pc()
            block_here = self.get_block(pc)
            frame_here = call_state._frame
            assert frame_here is not None
            if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                frame_here.frames.truncate(block_here.frame_depth)
            frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
            frame_here.boundary_loops_to = block_here.loops_to if block_here is not None else None
            frame_here.native.boundary_next_pc = (
                block_here.next_pc
                if block_here is not None and block_here.next_pc is not None
                else 0xFFFF_FFFF
            )
            frame_here.native.boundary_loops_to = (
                block_here.loops_to
                if block_here is not None and block_here.loops_to is not None
                else 0xFFFF_FFFF
            )

            trace = self.jit_runtime.lookup(pc) if self.jit_runtime is not None else None
            if trace is not None and not self._trace_fits_operand_stack(
                call_state._frame.values, trace
            ):
                trace = None

            if trace is not None:
                assert self.jit_runtime is not None
                loop_plan = self._native_loop_cycle(trace, frame_here, block_here)
                if loop_plan is not None:
                    body_trace, continue_when_nonzero, body_chain_count = loop_plan
                    assert _native_trace_call is not None
                    assert trace.raw_addr is not None and body_trace.raw_addr is not None
                    result_slot = len(frame_here.values)
                    locals_ptr = frame_here.context.local_stack.value_ptr(frame_here.frame_offset)
                    result_ptr = frame_here.values.value_ptr(result_slot)
                    iterations = _native_trace_call.run_loop_cycle(
                        trace.raw_addr,
                        body_trace.raw_addr,
                        frame_here.context_ptr.value,
                        result_ptr.value,
                        locals_ptr.value,
                        0,
                        continue_when_nonzero,
                    )
                    self.stat_native_loop_calls += 1
                    self.stat_jit_invocations += 1 + iterations * body_chain_count
                    self.stat_chain_hits += iterations * (body_chain_count - 1)
                    call_state = self._resume_trace(call_state, trace)
                else:
                    # Ordinary native chaining follows linked bodies without
                    # returning to Python between successors.
                    chain_count = self.jit_runtime.chain_length(trace)
                    chain_trace = self.jit_runtime.terminal_trace(trace)
                    self.stat_jit_invocations += chain_count
                    self.stat_chain_hits += chain_count - 1
                    if self.debug:
                        chain_trace.exec_count += 1
                        if chain_count > 1:
                            trace.exec_count += 1
                    call_state = self._invoke_trace(
                        interp, call_state, trace, terminal_trace=chain_trace
                    )
                if not call_state.finished:
                    self.stat_trace_exits_to_interp += 1
            else:
                self.stat_interp_steps += 1
                # Only a real block head may enter the hot-block history: a resume point
                # inside a block (e.g. after a call returns) can share a 4-byte card with a
                # trackable head, but it has no block to compile.
                if (
                    self.jit_runtime is not None
                    and block_here is not None
                    and self.jit_runtime.is_trackable(pc)
                ):
                    if self.record_block_head(pc):
                        self.idle_hook(budget=idle_budget)
                call_state = interp.step(call_state)

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()

        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def run_cooperative(
        self,
        interp: Interpreter,
        func_index: int,
        args: Sequence[int],
        idle_budget: int = 4,
    ) -> Generator[None, None, StaticVector[WasmNumber]]:
        """Run a resumable vSoC slice, yielding at trace boundaries on request.

        The scheduler callback is intentionally injected as a protocol so Tier 2
        does not import Tier 1. A yielded ``None`` is the handoff point at which
        the caller's coroutine returns to COOS and later resumes this generator.
        """
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

        call_state = interp.start(func_index, args)
        while not call_state.finished:
            if (
                self.reschedule_observer is not None
                and self.reschedule_observer.observe_reschedule_generation()
            ):
                self.on_yield()
                yield None
                continue
            assert call_state._frame is not None
            current_ip = call_state._ip
            if current_ip == RETURN_SENTINEL_IP:
                call_state = interp.step(call_state)
                continue
            pc = call_state.current_pc()
            block_here = self.get_block(pc)
            frame_here = call_state._frame
            assert frame_here is not None
            if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                frame_here.frames.truncate(block_here.frame_depth)
            frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
            frame_here.boundary_loops_to = block_here.loops_to if block_here is not None else None
            frame_here.native.boundary_next_pc = (
                block_here.next_pc
                if block_here is not None and block_here.next_pc is not None
                else 0xFFFF_FFFF
            )
            frame_here.native.boundary_loops_to = (
                block_here.loops_to
                if block_here is not None and block_here.loops_to is not None
                else 0xFFFF_FFFF
            )

            trace = self.jit_runtime.lookup(pc) if self.jit_runtime is not None else None
            if trace is not None and not self._trace_fits_operand_stack(
                call_state._frame.values, trace
            ):
                trace = None

            if trace is not None:
                assert self.jit_runtime is not None
                chain_count = self.jit_runtime.chain_length(trace)
                chain_trace = self.jit_runtime.terminal_trace(trace)
                self.stat_jit_invocations += chain_count
                self.stat_chain_hits += chain_count - 1
                if self.debug:
                    chain_trace.exec_count += 1
                    if chain_count > 1:
                        trace.exec_count += 1
                call_state = self._invoke_trace(interp, call_state, trace)
            else:
                self.stat_interp_steps += 1
                # Only a real block head may enter the hot-block history: a resume point
                # inside a block (e.g. after a call returns) can share a 4-byte card with a
                # trackable head, but it has no block to compile.
                if (
                    self.jit_runtime is not None
                    and block_here is not None
                    and self.jit_runtime.is_trackable(pc)
                ):
                    if self.record_block_head(pc):
                        self.idle_hook(budget=idle_budget)
                        yield None
                call_state = interp.step(call_state)

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _trace_fits_operand_stack(self, values: NativeValueStack, trace: JITTrace) -> bool:
        """Check the shared operand stack against the Tier 3 chain requirement."""
        if self.jit_runtime is None:
            return False
        return len(values) + self.jit_runtime.max_chain_stack_words(trace) <= values.capacity

    def _native_loop_cycle(
        self, trace: JITTrace, frame: CallFrame, current_block: BasicBlock | None
    ) -> tuple[JITTrace, bool, int] | None:
        """Find a pure native branch/body chain that returns to its branch head."""
        if (
            self.debug
            or _native_trace_call is None
            or getattr(_native_trace_call, "run_loop_cycle", None) is None
            or not trace.native_loop_safe
            or trace.raw_addr is None
            or not trace.has_return_val
            or trace.result_words != 1
            or trace.next_pc is None
            or trace.loops_to is None
            or trace.chain_next is not None
            or self.jit_runtime is None
            or current_block is None
            or len(frame.frames) != current_block.frame_depth
        ):
            return None

        selected: tuple[JITTrace, bool, int] | None = None
        for continuation_pc, continue_when_nonzero in (
            (trace.loops_to, True),
            (trace.next_pc, False),
        ):
            body_trace = self.jit_runtime.find_trace(continuation_pc)
            if (
                body_trace is None
                or body_trace.raw_addr is None
                or not body_trace.native_loop_safe
                or not self._trace_fits_operand_stack(frame.values, body_trace)
            ):
                continue

            chain_count = self.jit_runtime.chain_length(body_trace)
            if self.jit_runtime.terminal_trace(body_trace) is not trace:
                continue

            current = body_trace
            chain_safe = True
            for chain_index in range(chain_count):
                block = self.jit_runtime.get_block(current.head_pc)
                if (
                    not current.native_loop_safe
                    or current.raw_addr is None
                    or block is None
                    or block.frame_depth != current_block.frame_depth
                ):
                    chain_safe = False
                    break
                if chain_index + 1 == chain_count:
                    chain_safe = current is trace
                    break
                if current.chain_next is None:
                    chain_safe = False
                    break
                successor = self.jit_runtime.find_trace(current.chain_next)
                if successor is None:
                    chain_safe = False
                    break
                current = successor
            if not chain_safe:
                continue
            if selected is not None:
                return None
            selected = (body_trace, continue_when_nonzero, chain_count)
        return selected

    def _resume_frame_depth(self, frame: CallFrame, function_index: int, ip: int) -> int:
        """Control-frame count the interpreter must hold when it resumes at `ip`.

        A block head records it.  Any other position is enclosed by every structured
        opener that starts before it and whose matching `end` has not yet executed.
        """
        block = self.get_block((function_index << 16) | ip)
        if block is not None:
            return block.frame_depth
        depth = 0
        for start, control in frame.control_map.blocks.view().entries:
            if start >= ip:
                break
            if ip <= control[0]:
                depth += 1
        return depth

    def _invoke_trace(
        self,
        interp: Interpreter,
        call_state: InterpreterCall,
        trace: JITTrace,
        terminal_trace: JITTrace | None = None,
    ) -> InterpreterCall:
        """
        Executes one compiled native x64 JIT trace and advances `call_state`
        past it. The trace receives the interpreter's Native operand and
        local stacks directly; no JIT-only ctypes buffers or typed copy-back
        path is allowed. A residual value is written by native code into the
        next raw operand-stack slot passed as `sp` and then committed by
        advancing that same stack's pointer.
        """
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        # Every local owns one fixed 8-byte slot regardless of its type, so a trace
        # addresses locals by index in any frame.  The compiler rejects blocks that
        # touch an i64/f64 local, so a resident trace only reads and writes i32 locals.
        result_slot = len(frame.values)
        locals_ptr = frame.context.local_stack.value_ptr(frame.frame_offset)
        result_ptr = frame.values.value_ptr(result_slot)
        if _native_trace_call is not None and trace.raw_addr is not None:
            _native_trace_call.invoke_trace(
                trace.raw_addr,
                frame.context_ptr.value,
                result_ptr.value,
                locals_ptr.value,
                0,
            )
        else:
            trace.execute(frame.context_ptr, result_ptr, locals_ptr, 0)
        # The native entry may have traversed several successor bodies before
        # returning through the common epilogue.  Resolve the same resident
        # chain in metadata so result width and the final WASM continuation
        # belong to the body that actually returned.
        assert self.jit_runtime is not None
        if terminal_trace is None:
            terminal_trace = self.jit_runtime.terminal_trace(trace)

        return self._resume_trace(call_state, terminal_trace)

    def _resume_trace(
        self, call_state: InterpreterCall, terminal_trace: JITTrace
    ) -> InterpreterCall:
        """Apply a native trace's terminal stack and WASM continuation state."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        result_slot = len(frame.values)

        res = frame.values.raw_at(result_slot) if terminal_trace.has_return_val else 0
        if terminal_trace.has_return_val and terminal_trace.loops_to is None:
            frame.values.set_size(result_slot + terminal_trace.result_words)

        if terminal_trace.loops_to is not None:
            # Terminator was BR_IF against a loop backedge: the trace's
            # residual value is the branch condition, consumed here -- it
            # never reaches the WASM operand stack. This is the same
            # continuation rule used by the interpreter boundary.
            cond = res if res is not None else 0
            next_unified = terminal_trace.loops_to if cond != 0 else terminal_trace.next_pc
        else:
            next_unified = terminal_trace.next_pc

        # A terminal trace stops before its WASM boundary opcode. Resume at
        # that raw bytecode position so Interpreter's return/branch/end handler
        # owns sentinel publication and the corresponding frame transition.
        if next_unified is None:
            terminal_block = self.get_block(terminal_trace.head_pc)
            assert terminal_block is not None
            next_ip = (terminal_trace.head_pc & 0xFFFF) + terminal_block.byte_span
            assert next_ip < len(frame.code)
        else:
            next_ip = next_unified & 0xFFFF
            if next_ip >= len(frame.code):
                # A loader-resolved fallthrough past the function body is the
                # same implicit return boundary as Interpreter.step() reaches
                # after executing the final END opcode.
                next_ip = RETURN_SENTINEL_IP
        # A trace never pops the control frames of the `end`/`br` it skips, so the stack
        # may be deeper than the resume point requires.  Drop the stale innermost frames
        # here: a resume point that is not a block head gets no other truncation.
        if next_ip != RETURN_SENTINEL_IP:
            depth = self._resume_frame_depth(frame, call_state.func_index, next_ip)
            if depth < len(frame.frames):
                frame.frames.truncate(depth)
        call_state._ip = next_ip
        call_state._frame = frame
        call_state._locals = locals_arr
        call_state._tos = frame.values.raw_top() if frame.values else 0
        return call_state
