"""Tier 2 lifecycle contract for the optional Tier 3 JIT runtime plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tier2_runtime.wasm.module import Module

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import (
        InterpreterCall,
        NativeDispatchEntryPoint,
        NativeInterpreter,
    )
    from tier2_runtime.runtime.engine import RuntimeBoundaryResult


class JITRuntime(Protocol):
    """Small lifecycle and execution-boundary contract for a Tier 3 JIT plugin."""

    yield_threshold: int
    hotspot_profiling_enabled: bool

    def register_module(self, module: Module) -> None: ...

    def run_boundary(
        self,
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int,
        native_dispatcher: NativeDispatchEntryPoint,
    ) -> RuntimeBoundaryResult: ...

    def on_yield(self) -> None: ...

    def idle_hook(self, budget: int = 4) -> int: ...

    def age_step(self) -> int: ...

    def reset_stats(self) -> None: ...


__all__ = ("JITRuntime",)
