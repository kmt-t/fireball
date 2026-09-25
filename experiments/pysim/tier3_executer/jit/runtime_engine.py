"""Tier 3 integration driver for Interpreter and the injected JIT contract.

The driver owns execution dispatch and continuation handling. The Tier 2 JIT
contract is declared separately; the selected Tier 3 manager owns hotspot and
cache state.
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

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import TextIO

from config import (
    FB_CONF_RUNTIME_PROFILE_STATS,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    RUNTIME_DEBUG_REPORT_LINE_CAPACITY,
)
from control_flow import OpcodeAttribute, opcode_has_attribute
from interop_abi import NativeValueStack
from jit_runtime_contract import JITRuntime, JITTrace
from recovery import Result
from system_containers import StaticVector
from tier3_executer.interpreter.interpreter import (
    NATIVE_DISPATCH_OLDEST_TRACE,
    NATIVE_DISPATCH_YIELD,
    NATIVE_JIT_HOTSPOT_PROFILING_ENABLED,
    NATIVE_RUNTIME_PROFILE_STATS_ENABLED,
    RETURN_SENTINEL_IP,
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


class RuntimeDriveMode(IntEnum):
    """Select which layer owns advancing the guest between native dispatch boundaries."""

    SYNCHRONOUS = 1
    COOS = 2


@dataclass(frozen=True, slots=True)
class RuntimeBoundaryResult:
    """State returned after yield, fallback, trap, or completion from native dispatch."""

    call_state: InterpreterCall
    yield_requested: bool = False


__all__ = ("RuntimeBoundaryResult", "RuntimeDriveMode", "RuntimeEngine")


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    __slots__ = (
        "_boundary_runner",
        "_virq",
        "_virq_interp",
        "collect_runtime_stats",
        "debug",
        "drive_mode",
        "jit_runtime",
        "module",
        "stat_interp_steps",
        "stat_jit_invocations",
        "stat_native_control_handlers",
        "stat_native_dispatch_trace_transitions",
        "stat_trace_exits_to_interp",
        "yield_threshold",
    )

    def __init__(
        self,
        jit_runtime: JITRuntime | None = None,
        debug: bool = False,
        drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        collect_runtime_stats: bool = FB_CONF_RUNTIME_PROFILE_STATS,
    ):
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env == "1" or debug_env == "true" or debug_env == "yes"
        assert not collect_runtime_stats or NATIVE_RUNTIME_PROFILE_STATS_ENABLED, (
            "runtime profile stats were compiled out; rebuild with "
            "FB_CONF_RUNTIME_PROFILE_STATS=True"
        )
        assert (
            jit_runtime is None
            or not jit_runtime.hotspot_profiling_enabled
            or NATIVE_JIT_HOTSPOT_PROFILING_ENABLED
        ), "JIT hotspot profiling was compiled out; rebuild with FB_CONF_JIT_HOTSPOT_PROFILING=True"
        self.collect_runtime_stats = collect_runtime_stats
        self.jit_runtime = jit_runtime
        self.stat_interp_steps: int = 0
        self.stat_jit_invocations: int = 0
        self.stat_native_control_handlers: int = 0
        self.stat_native_dispatch_trace_transitions: int = 0
        self.stat_trace_exits_to_interp: int = 0
        assert 1 <= yield_threshold <= 0xFFFF_FFFF
        self.yield_threshold = (
            jit_runtime.yield_threshold if jit_runtime is not None else yield_threshold
        )
        self.drive_mode = drive_mode
        self.module: Module | None = None
        self._virq: VirqDispatcher | None = None
        self._virq_interp: Interpreter | None = None
        self._boundary_runner: Callable[
            [Interpreter, InterpreterCall, int], RuntimeBoundaryResult
        ] = self._run_interpreter_boundary if jit_runtime is None else self._run_jit_boundary

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
        """Validate a static vIRQ registration for the next COOS yield boundary."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.register_dispatcher(node_id, function_index)

    def unregister_virq_dispatcher(
        self, node_id: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Stage removal of a vIRQ registration for the next COOS yield boundary."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.unregister_dispatcher(node_id)

    def commit_virq_registrations(self) -> None:
        """Publish validated vIRQ registrations at the execution boundary."""
        if self._virq is not None:
            self._virq.commit_pending_registrations()

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
                Compiling successors first lets a later-installed predecessor link
                through the common-code chain dispatcher as soon as it is resident.
        """

        return self.jit_runtime.idle_hook(budget) if self.jit_runtime is not None else 0

    def reset_stats(self) -> None:
        """Resets execution statistics counters."""
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_native_control_handlers = 0
        self.stat_native_dispatch_trace_transitions = 0
        self.stat_trace_exits_to_interp = 0
        if self.jit_runtime is not None:
            self.jit_runtime.reset_stats()

    def dump_internal_state(self, file: TextIO | None = None) -> str:
        """Dump execution counters without owning Tier 3 cache diagnostics."""
        lines: StaticVector[str] = StaticVector(capacity=RUNTIME_DEBUG_REPORT_LINE_CAPACITY)
        lines.append("=" * 80)
        lines.append("                  RuntimeEngine Execution Dump                  ")
        lines.append("=" * 80)
        lines.append(
            "  * Runtime profile stats:      "
            + ("enabled" if self.collect_runtime_stats else "disabled by configuration")
        )

        total_blocks = self.stat_interp_steps + self.stat_jit_invocations
        jit_pct = (self.stat_jit_invocations / total_blocks * 100.0) if total_blocks > 0 else 0.0
        lines.append("[1. Execution Summary]")
        lines.append(f"  * Total Block Executions:    {total_blocks:,}")
        lines.append(
            f"    - Interpreter Steps:       {self.stat_interp_steps:,} ({(100.0 - jit_pct):.1f}%)"
        )
        lines.append(
            f"    - JIT Invocations:         {self.stat_jit_invocations:,} ({jit_pct:.1f}%)"
        )
        lines.append("  * Native Dispatch Transitions:")
        lines.append(
            f"    - C++ handler to JIT trace: {self.stat_native_dispatch_trace_transitions:,}"
        )
        lines.append(f"    - Exits to Interpreter:    {self.stat_trace_exits_to_interp:,}")
        lines.append(f"    - Native control handlers:{self.stat_native_control_handlers:>11,}")
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
        call_state: InterpreterCall,
        idle_budget: int = 4,
    ) -> RuntimeBoundaryResult:
        """Run native dispatch to a yield, fallback, trap, or completion boundary."""
        self._bind_interpreter(interp)
        assert not call_state.finished, "cannot advance a completed interpreter call"
        return self._boundary_runner(interp, call_state, idle_budget)

    def call(
        self,
        interp: Interpreter,
        func_index: int,
        args: Sequence[WasmNumber],
        idle_budget: int = 4,
    ) -> StaticVector[WasmNumber]:
        """Complete one guest call in the caller-owned, non-COOS runtime mode."""
        assert self.drive_mode == RuntimeDriveMode.SYNCHRONOUS, (
            "COOS runtime calls must be advanced by System at each trace boundary"
        )
        self._bind_interpreter(interp)
        return self.complete_call(interp, interp.start(func_index, args), idle_budget)

    def complete_call(
        self, interp: Interpreter, call_state: InterpreterCall, idle_budget: int = 4
    ) -> StaticVector[WasmNumber]:
        """Finish a call by repeatedly using the same native-dispatch ``run`` path."""
        self._bind_interpreter(interp)
        if self.drive_mode == RuntimeDriveMode.COOS:
            assert call_state.finished, (
                "COOS runtime calls must be advanced by System at each trace boundary"
            )
        while not call_state.finished:
            call_state = self.run(interp, call_state, idle_budget).call_state

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _bind_interpreter(self, interp: Interpreter) -> None:
        """Bind module-owned metadata and the current interpreter activation."""
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

    def _run_interpreter_boundary(
        self, interp: Interpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Run C++ interpreter handlers through one count-based yield boundary."""
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step(call_state))
        assert call_state._frame is not None
        frame = call_state._frame
        (
            native_status,
            _trace_count,
            _body_count,
            _dispatcher_trace_transitions,
            control_count,
            _eligible_block_visits,
            interpreted_block_count,
            _visits,
        ) = interp.run_native_dispatch(
            call_state,
            (),
            (),
            self.yield_threshold,
            0,
            collect_stats=self.collect_runtime_stats,
        )
        if native_status == 0:
            call_state = interp.step(call_state)
        elif native_status == NATIVE_DISPATCH_YIELD:
            frame.context.native_context.loop_jump_count = 0
        if self.collect_runtime_stats:
            self.stat_interp_steps += interpreted_block_count
            self.stat_native_control_handlers += control_count
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
        )
        if not valid_native_status:
            assert False, f"unexpected native interpreter dispatch status: {native_status}"
        if self.collect_runtime_stats and not call_state.finished:
            self.stat_trace_exits_to_interp += 1
        return RuntimeBoundaryResult(call_state, native_status == NATIVE_DISPATCH_YIELD)

    def _run_jit_boundary(
        self, interp: Interpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Run native JIT/interpreter dispatch to the next required boundary."""
        assert self.jit_runtime is not None
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step(call_state))
        assert call_state._frame is not None
        pc = call_state.current_pc()
        block_here = self.get_block(pc)
        frame = call_state._frame
        if block_here is not None and len(frame.frames) > block_here.frame_depth:
            frame.frames.truncate(block_here.frame_depth)

        trace = self.jit_runtime.lookup(pc)

        if interp.debugger is not None:
            if trace is not None and not self._trace_fits_operand_stack(frame.values, trace):
                trace = None
            if trace is not None:
                frame.context.native_context.loop_jump_threshold = self.jit_runtime.yield_threshold
                chain_count = self.jit_runtime.chain_length(trace)
                chain_trace = self.jit_runtime.terminal_trace(trace)
                if self.collect_runtime_stats:
                    self.stat_jit_invocations += chain_count
                call_state = self._invoke_trace(
                    interp, call_state, trace, terminal_trace=chain_trace
                )
                native_context = frame.context.native_context
                yield_requested = (
                    native_context.loop_jump_count >= native_context.loop_jump_threshold
                )
                if yield_requested:
                    native_context.loop_jump_count = 0
            else:
                if self.collect_runtime_stats:
                    self.stat_interp_steps += 1
                frame.set_runtime_boundary(
                    block_here.next_pc if block_here is not None else None,
                    block_here.loops_to if block_here is not None else None,
                )
                yield_requested = (
                    block_here is not None
                    and self.jit_runtime.is_trackable(pc)
                    and self.record_block_head(pc)
                )
                call_state = interp.step(call_state)
                if yield_requested:
                    self.idle_hook(budget=idle_budget)
            return RuntimeBoundaryResult(call_state, yield_requested)

        body_count = 0
        dispatcher_trace_transitions = 0
        control_count = 0
        interpreted_block_count = 0
        native_status = 0
        hotness_yield = False
        while True:
            entries, trackable_blocks = self.jit_runtime.native_dispatch_state(
                call_state.func_index
            )
            (
                native_status,
                _dispatch_count,
                native_body_count,
                native_trace_transitions,
                native_control_count,
                eligible_block_visits,
                native_interpreted_block_count,
                block_visits,
            ) = interp.run_native_dispatch(
                call_state,
                entries,
                trackable_blocks,
                self.jit_runtime.yield_threshold,
                self.jit_runtime.exec_counter,
                collect_stats=self.collect_runtime_stats,
                collect_hotspots=self.jit_runtime.hotspot_profiling_enabled,
            )
            body_count += native_body_count
            dispatcher_trace_transitions += native_trace_transitions
            control_count += native_control_count
            interpreted_block_count += native_interpreted_block_count
            oldest_pc: int | None = None
            if native_status == NATIVE_DISPATCH_OLDEST_TRACE:
                oldest_pc = call_state.current_pc()
                assert self.jit_runtime.lookup(oldest_pc) is not None, (
                    "native dispatcher reported an Oldest trace absent from the JIT cache"
                )
            if self.jit_runtime.hotspot_profiling_enabled:
                hotness_yield = (
                    self.jit_runtime.record_native_block_visits(block_visits, eligible_block_visits)
                    or hotness_yield
                )
            if native_status != NATIVE_DISPATCH_OLDEST_TRACE:
                break
            if hotness_yield:
                break
        if native_status == 0:
            call_state = interp.step(call_state)
        if hotness_yield:
            self.jit_runtime.idle_hook(budget=idle_budget)

        native_context = frame.context.native_context
        if self.collect_runtime_stats:
            self.stat_interp_steps += interpreted_block_count
            self.stat_jit_invocations += body_count
            self.stat_native_dispatch_trace_transitions += dispatcher_trace_transitions
            self.stat_native_control_handlers += control_count
        if native_status == NATIVE_DISPATCH_YIELD:
            native_context.loop_jump_count = 0
        yield_requested = native_status == NATIVE_DISPATCH_YIELD or hotness_yield
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
            or native_status == NATIVE_DISPATCH_OLDEST_TRACE
        )
        if not valid_native_status:
            assert False, f"unexpected native JIT dispatch status: {native_status}"
        if self.collect_runtime_stats and not call_state.finished:
            self.stat_trace_exits_to_interp += 1
        return RuntimeBoundaryResult(call_state, yield_requested)

    def _trace_fits_operand_stack(self, values: NativeValueStack, trace: JITTrace) -> bool:
        """Check the shared operand stack against the Tier 3 chain requirement."""
        assert self.jit_runtime is not None
        return len(values) + self.jit_runtime.max_chain_stack_words(trace) <= values.capacity

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
        trace.execute(frame.context_ptr, result_ptr, locals_ptr, 0)
        # The native entry may have traversed several successor bodies before
        # returning through the common epilogue.  Resolve the same resident
        # chain in metadata so result width and the final WASM continuation
        # belong to the body that actually returned.
        assert self.jit_runtime is not None
        if terminal_trace is None:
            terminal_trace = self.jit_runtime.terminal_trace(trace)

        return self._resume_trace(interp, call_state, terminal_trace)

    def _resume_trace(
        self, interp: Interpreter, call_state: InterpreterCall, terminal_trace: JITTrace
    ) -> InterpreterCall:
        """Resume a trace by dispatching its terminator through Interpreter handlers."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        result_slot = len(frame.values)

        if terminal_trace.has_return_val:
            frame.values.set_size(result_slot + terminal_trace.result_words)

        terminal_block = self.get_block(terminal_trace.head_pc)
        assert terminal_block is not None
        terminal_ip = (terminal_trace.head_pc & 0xFFFF) + terminal_block.byte_span
        if terminal_ip >= len(frame.code):
            # A body that ends at the implicit function boundary has no opcode
            # handler to invoke. All explicit control terminators stay on the
            # interpreter path below.
            call_state._ip = RETURN_SENTINEL_IP
            call_state._frame = frame
            call_state._locals = locals_arr
            call_state._tos = frame.values.raw_top() if frame.values else 0
            return call_state

        if len(frame.frames) > terminal_block.frame_depth:
            frame.frames.truncate(terminal_block.frame_depth)

        # The trace body leaves its terminator unexecuted. Run a supported
        # control terminator through exactly one C++ interpreter handler, then
        # return to Tier 2 for the next lookup. This keeps branch conditions,
        # target depth, and control-frame changes in the interpreter handler.
        call_state._ip = terminal_ip
        call_state._frame = frame
        call_state._locals = locals_arr
        call_state._tos = frame.values.raw_top() if frame.values else 0
        frame.set_runtime_boundary(terminal_block.next_pc, terminal_block.loops_to)
        terminal_opcode = frame.code[terminal_ip]
        is_non_call_boundary = opcode_has_attribute(
            terminal_opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY
        ) and not opcode_has_attribute(terminal_opcode, OpcodeAttribute.CALL)
        if is_non_call_boundary:
            # Invoke this one terminal opcode through the native handler table.
            # The C++ entry returns after the handler updates stacks and PC.
            return interp.step_native_control(call_state)
        return interp.step(call_state)
