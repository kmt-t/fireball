"""Tier 2 Runtime execution boundary for the Interpreter and optional JIT extension.

The Runtime owns execution dispatch and continuation handling. An optional
Tier 3 JIT extension owns hotspot and cache state behind the bounded contract.
Execution model:
  Interpreter execution advances to a native dispatch boundary and preserves
  the shared execution context. An optional JIT extension supplies a bounded
  trace snapshot and receives candidate history at those boundaries.
  JIT cache policy and compilation remain inside that extension.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import TextIO

from bump_allocator import BumpAllocator
from config import (
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    RUNTIME_DEBUG_REPORT_LINE_CAPACITY,
)
from tier2_runtime.runtime.recovery import Result
from system_containers import StaticVector
from tier2_runtime.abi.jit_abi import EMPTY_NATIVE_DISPATCH_SNAPSHOT
from tier2_runtime.runtime.jit_plugin import JITRuntime
from tier2_runtime.interpreter.interpreter import (
    NATIVE_DISPATCH_YIELD,
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeDispatchEntryPoint,
    NativeInterpreter,
    WasmNumber,
    select_native_dispatch_entry,
)
from tier2_runtime.hal.virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
)
from tier2_runtime.wasm.module import BasicBlock, Module


class RuntimeDriveMode(IntEnum):
    """Select which layer owns advancing the guest between native dispatch boundaries."""

    SYNCHRONOUS = 1
    COOS = 2


@dataclass(frozen=True, slots=True)
class RuntimeBoundaryResult:
    """State returned after yield, fallback, trap, or completion from native dispatch."""

    call_state: InterpreterCall
    yield_requested: bool = False
    interpreted_block_count: int = 0
    trace_execution_count: int = 0
    control_handler_count: int = 0
    trace_transition_count: int = 0


__all__ = ("RuntimeBoundaryResult", "RuntimeDriveMode", "RuntimeEngine")


class RuntimeEngine:
    """Runtime coordinator for interpreter execution and optional JIT traces."""

    __slots__ = (
        "_bump_allocator",
        "_collect_runtime_stats",
        "_native_dispatcher",
        "_jit_dispatcher",
        "_owns_bump_allocator",
        "_virq",
        "_virq_interp",
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
        collect_runtime_stats: bool = False,
        bump_allocator: BumpAllocator | None = None,
    ):
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env == "1" or debug_env == "true" or debug_env == "yes"
        collect_hotspots = jit_runtime is not None and jit_runtime.hotspot_profiling_enabled
        self._collect_runtime_stats = collect_runtime_stats
        self._native_dispatcher = select_native_dispatch_entry(collect_runtime_stats, False)
        self._jit_dispatcher = (
            select_native_dispatch_entry(collect_runtime_stats, collect_hotspots)
            if jit_runtime is not None
            else self._native_dispatcher
        )
        # RuntimeEngine is the runtime owner; loaders borrow this arena.
        self._bump_allocator = bump_allocator if bump_allocator is not None else BumpAllocator()
        self._owns_bump_allocator = bump_allocator is None
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
        self._virq_interp: NativeInterpreter | None = None

    @property
    def collect_runtime_stats(self) -> bool:
        """Whether this Runtime instance was composed with diagnostic counters."""

        return self._collect_runtime_stats

    @property
    def native_dispatcher(self) -> NativeDispatchEntryPoint:
        """Return the native dispatch entry selected when this Runtime was composed."""

        return self._jit_dispatcher

    @property
    def bump_allocator(self) -> BumpAllocator:
        """Return the arena shared by this runtime and its WASM module."""

        return self._bump_allocator

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from tier2_runtime.wasm.reader import parse

        module = parse(wasm_bytes, self._bump_allocator)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        return self.module.get_block(pc) if self.module is not None else None

    def register_module_blocks(self, module: Module) -> None:
        """Binds the loader-owned immutable block index."""
        if (
            self._owns_bump_allocator
            and self._bump_allocator.offset == 0
            and module.allocator is not None
        ):
            self._bump_allocator = module.allocator
        module.bind_allocator(self._bump_allocator)
        if module.block_storage is None:
            module.build_basic_block_index(self._bump_allocator)
        self.module = module
        self._virq = VirqDispatcher(module, self._invoke_virq)
        if self.jit_runtime is not None:
            self.jit_runtime.register_module(module)

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
        """Notify the optional Tier 3 extension of the Runtime yield boundary."""
        if self.jit_runtime is not None:
            self.jit_runtime.on_yield()

    def age_step(self) -> int:
        """Delegate one cache-rotation aging step to Tier 3."""
        return self.jit_runtime.age_step() if self.jit_runtime is not None else 0

    def idle_hook(self, budget: int = 4) -> int:
        """
        Gives the optional Tier 3 extension a bounded COOS idle slice.
        """

        if self.jit_runtime is None:
            return 0
        assert budget >= 0
        return self.jit_runtime.idle_hook(budget)

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
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int = 4,
    ) -> RuntimeBoundaryResult:
        """Run native dispatch to a yield, fallback, trap, or completion boundary."""
        self._bind_interpreter(interp, call_state)
        return self._run_bound(interp, call_state, idle_budget)

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Advance an already-bound interpreter through the selected native boundary."""
        assert not call_state.finished, "cannot advance a completed interpreter call"
        if self.jit_runtime is not None and interp.debugger is None:
            result = self.jit_runtime.run_boundary(
                interp,
                call_state,
                idle_budget,
                self._jit_dispatcher,
            )
        else:
            result = self._run_interpreter_boundary(interp, call_state)
        if self.collect_runtime_stats:
            self.stat_interp_steps += result.interpreted_block_count
            self.stat_jit_invocations += result.trace_execution_count
            self.stat_native_control_handlers += result.control_handler_count
            self.stat_native_dispatch_trace_transitions += result.trace_transition_count
            if not result.call_state.finished:
                self.stat_trace_exits_to_interp += 1
        return result

    def call(
        self,
        interp: NativeInterpreter,
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
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int = 4
    ) -> StaticVector[WasmNumber]:
        """Finish a call by repeatedly using the same native-dispatch ``run`` path."""
        self._bind_interpreter(interp, call_state)
        if self.drive_mode == RuntimeDriveMode.COOS:
            assert call_state.finished, (
                "COOS runtime calls must be advanced by System at each trace boundary"
        )
        while not call_state.finished:
            call_state = self._run_bound(interp, call_state, idle_budget).call_state

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _bind_interpreter(
        self, interp: NativeInterpreter, call_state: InterpreterCall | None = None
    ) -> None:
        """Bind module-owned metadata and the current interpreter activation."""
        if self._owns_bump_allocator and self._bump_allocator.offset == 0:
            if interp.module.allocator is not None:
                self._bump_allocator = interp.module.allocator
            else:
                self._bump_allocator = interp.bump_allocator
        interp.module.bind_allocator(self._bump_allocator)
        interp.bind_allocator(self._bump_allocator)
        if call_state is not None and not call_state.finished:
            call_state.context.bind_allocator(self._bump_allocator)
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

    def _run_interpreter_boundary(
        self, interp: NativeInterpreter, call_state: InterpreterCall
    ) -> RuntimeBoundaryResult:
        """Run C++ interpreter handlers through one count-based yield boundary."""
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step_native(call_state))
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
            EMPTY_NATIVE_DISPATCH_SNAPSHOT,
            self.yield_threshold,
            0,
            native_dispatcher=self._native_dispatcher,
        )
        if native_status == 0:
            call_state = interp.resolve_native_call_boundary(call_state)
        elif native_status == NATIVE_DISPATCH_YIELD:
            frame.context.loop_jump_count = 0
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
        )
        if not valid_native_status:
            assert False, f"unexpected native interpreter dispatch status: {native_status}"
        return RuntimeBoundaryResult(
            call_state,
            native_status == NATIVE_DISPATCH_YIELD,
            interpreted_block_count=interpreted_block_count,
            control_handler_count=control_count,
        )
