"""Optional JIT trace provider contract owned by the Interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tier2_runtime.wasm.module import Module

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import (
        NativeDispatchEntryPoint,
        NativeModuleExecution,
    )


class NativeExecutionPlugin(Protocol):
    """Bind compiled trace lookup and cache lifetime to an Interpreter."""

    def register_module(self, module: Module) -> None: ...

    @property
    def native_entry(self) -> NativeDispatchEntryPoint: ...

    def bind_execution(
        self, execution: NativeModuleExecution
    ) -> int: ...

    def idle_hook(self, budget: int = 4) -> int: ...


__all__ = ("NativeExecutionPlugin",)
