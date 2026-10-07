"""Optional native execution extension contract owned by the Interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tier2_runtime.wasm.module import Module

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import (
        NativeDispatchEntryPoint,
        NativeModuleExecution,
    )


class NativeExecutionPlugin(Protocol):
    """Bind an optional native executor to the Interpreter's shared state."""

    def register_module(self, module: Module) -> None: ...

    @property
    def native_entry(self) -> NativeDispatchEntryPoint: ...

    def bind_execution(
        self, dispatcher: NativeDispatchEntryPoint, execution: NativeModuleExecution
    ) -> int: ...

    def idle_hook(self, budget: int = 4) -> int: ...


__all__ = ("NativeExecutionPlugin",)
