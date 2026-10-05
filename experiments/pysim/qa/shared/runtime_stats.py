"""QA-only execution counters over the unchanged production runtime boundary."""

from __future__ import annotations

import os
import sys
from functools import partial
from typing import TextIO

from bump_allocator import BumpAllocator
from config import FB_CONF_RUNTIME_YIELD_THRESHOLD, RUNTIME_DEBUG_REPORT_LINE_CAPACITY
from qa.private.interpreter_native_abi import NativeDiagnosticResult, select_native_dispatch_entry
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import (
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeInterpreter,
    WasmNumber,
)
from tier2_runtime.runtime.engine import RuntimeBoundaryResult, RuntimeDriveMode, RuntimeEngine
from tier2_runtime.runtime.execution_plugin import NativeExecutionPlugin


class RuntimeStatsEngine(RuntimeEngine):
    """Test and benchmark observer; product RuntimeEngine has no diagnostic state."""

    __slots__ = (
        "_collect_runtime_stats",
        "debug",
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
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        collect_runtime_stats: bool = False,
        bump_allocator: BumpAllocator | None = None,
    ) -> None:
        super().__init__(
            jit_runtime=jit_runtime,
            drive_mode=drive_mode,
            yield_threshold=yield_threshold,
            bump_allocator=bump_allocator,
        )
        self._collect_runtime_stats = collect_runtime_stats
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env in ("1", "true", "yes")
        dispatcher = select_native_dispatch_entry(collect_runtime_stats, jit_runtime is not None)
        self._native_dispatcher = dispatcher if jit_runtime is None else jit_runtime.native_entry
        self._execution_initializer = (
            None if jit_runtime is None else partial(jit_runtime.bind_execution, dispatcher)
        )
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_native_control_handlers = 0
        self.stat_native_dispatch_trace_transitions = 0
        self.stat_trace_exits_to_interp = 0

    @property
    def collect_runtime_stats(self) -> bool:
        return self._collect_runtime_stats

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        native_dispatch = call_state._ip != RETURN_SENTINEL_IP
        metrics = call_state.context._native_result
        if not isinstance(metrics, NativeDiagnosticResult):
            metrics = NativeDiagnosticResult()
            call_state.context._native_result = metrics
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

    def complete_call(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int = 4
    ) -> StaticVector[WasmNumber]:
        results = super().complete_call(interp, call_state, idle_budget)
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
