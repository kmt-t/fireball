"""Tier 2 lifecycle contract for an opaque native execution plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tier2_runtime.wasm.module import Module

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import (
        NativeDispatchEntryPoint,
        NativeModuleExecution,
    )


class NativeExecutionPlugin(Protocol):
    """Lifecycle and native entry binding for a replaceable execution plugin."""

    yield_threshold: int

    def register_module(self, module: Module) -> None: ...

    native_entry: NativeDispatchEntryPoint

    def bind_execution(
        self, dispatcher: NativeDispatchEntryPoint, execution: NativeModuleExecution
    ) -> int: ...

    def on_yield(self) -> None: ...

    def idle_hook(self, budget: int = 4) -> int: ...

    def reset_stats(self) -> None: ...


__all__ = ("NativeExecutionPlugin",)
