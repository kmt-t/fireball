"""QA-only execution counters over the unchanged production runtime boundary."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from bump_allocator import BumpAllocator
from config import RUNTIME_DEBUG_REPORT_LINE_CAPACITY
from qa.private.interpreter_native_abi import NativeDiagnosticResult, select_native_dispatch_entry
from system_containers import StaticVector
from tier2_runtime.interpreter.execution_plugin import NativeExecutionPlugin
from tier2_runtime.interpreter.interpreter import (
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeInterpreter,
    WasmNumber,
)
from tier2_runtime.runtime.engine import RuntimeBoundaryResult, RuntimeDriveMode, RuntimeEngine
from tier2_runtime.wasm.module import Module


class RuntimeStatsEngine(RuntimeEngine):
    """Test and benchmark observer; product RuntimeEngine has no diagnostic state."""

    __slots__ = (
        "_collect_runtime_stats",
        "debug",
        "jit_runtime",
        "stat_interp_steps",
        "stat_jit_invocations",
        "stat_native_control_handlers",
        "stat_native_dispatch_trace_transitions",
        "stat_trace_exits_to_interp",
    )

    def __init__(
        self,
        jit_runtime: NativeExecutionPlugin | None = None,
        debug: bool = False,
        drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
        collect_runtime_stats: bool = False,
        bump_allocator: BumpAllocator | None = None,
    ) -> None:
        super().__init__(
            drive_mode=drive_mode,
            bump_allocator=bump_allocator,
        )
        self._collect_runtime_stats = collect_runtime_stats
        self.jit_runtime = jit_runtime
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env in ("1", "true", "yes")
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_native_control_handlers = 0
        self.stat_native_dispatch_trace_transitions = 0
        self.stat_trace_exits_to_interp = 0

    @property
    def collect_runtime_stats(self) -> bool:
        return self._collect_runtime_stats

    def register_module_blocks(self, module: Module) -> None:
        super().register_module_blocks(module)
        if self.jit_runtime is not None:
            self.jit_runtime.register_module(module)

    def _activate_interpreter(self, interp: NativeInterpreter) -> None:
        """Install QA dispatch instrumentation at the Interpreter plugin seam."""
        plugin = self.jit_runtime
        if plugin is None:
            plugin = interp._execution_plugin
        with_extension = plugin is not None
        dispatcher = select_native_dispatch_entry(self.collect_runtime_stats, with_extension)
        if plugin is not None:
            interp.attach_execution_plugin(plugin, dispatcher)
        else:
            interp._native_dispatcher = dispatcher
        super()._activate_interpreter(interp)

    def idle_hook(self, budget: int = 4) -> int:
        """Service the manager retained by this QA-only driver."""
        if self.jit_runtime is not None:
            assert budget >= 0
            return self.jit_runtime.idle_hook(budget)
        return super().idle_hook(budget)

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        native_dispatch = call_state._ip != RETURN_SENTINEL_IP
        metrics = call_state.context._native_result
        if not isinstance(metrics, NativeDiagnosticResult):
            metrics = NativeDiagnosticResult()
            call_state.context._native_result = metrics
        if self.collect_runtime_stats:
            # A boundary can cross several Python memory fallbacks. Preserve
            # their counters inside the QA dispatcher, but start this boundary
            # with a clean aggregate.
            metrics.reset_counters()
        result = super()._run_bound(interp, call_state, idle_budget)
        if self.collect_runtime_stats:
            if native_dispatch:
                self.stat_interp_steps += int(metrics.interpreted_block_count)
                self.stat_jit_invocations += int(metrics.body_count)
                self.stat_native_control_handlers += int(metrics.control_handler_count)
                self.stat_native_dispatch_trace_transitions += int(
                    metrics.dispatcher_trace_transitions
                )
            if not result.call_state.finished:
                self.stat_trace_exits_to_interp += 1
        return result

    def call(
        self,
        interp: NativeInterpreter,
        func_index: int,
        args: tuple[WasmNumber, ...] | list[WasmNumber],
        idle_budget: int = 4,
    ) -> StaticVector[WasmNumber]:
        """QA-only driver for collecting native dispatch counters around a call."""
        self._activate_interpreter(interp)
        return self.complete_call(interp, interp.start(func_index, args), idle_budget)

    def complete_call(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int = 4
    ) -> StaticVector[WasmNumber]:
        self._activate_interpreter(interp)
        while not call_state.finished:
            call_state = self.run(interp, call_state, idle_budget).call_state
        self.idle_hook(idle_budget)
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        results = call_state.results
        if self.debug:
            self.dump_internal_state()
        return results

    def reset_stats(self) -> None:
        """Begin a new QA measurement interval without changing product behavior."""
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_native_control_handlers = 0
        self.stat_native_dispatch_trace_transitions = 0
        self.stat_trace_exits_to_interp = 0

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
