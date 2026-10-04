"""Debugger protocol test context and execution setup, private to QA suites."""

from __future__ import annotations

from collections.abc import Sequence

from tier2_runtime.abi.interpreter_abi import NativeValueStack
from qa.shared.helpers import make_interpreter_bindings
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import (
    WasmHostFunction,
    WasmNumber,
)
from tier3_plugins.debugger.debugger import InterpreterExecutionControl
from tier2_runtime.wasm.module import Module


class DebugTestView:
    """Mutable register and memory view for protocol-only debugger tests."""

    __slots__ = ("locals", "memory", "stack")

    def __init__(self, memory: bytearray | None = None) -> None:
        self.stack = NativeValueStack()
        self.locals = [0] * 16
        self.memory = memory

    @property
    def stack_capacity(self) -> int:
        return self.stack.capacity


def make_debug_execution(
    module: Module,
    func_index: int,
    args: Sequence[WasmNumber],
    memory: bytearray | None = None,
    host_functions: StaticVector[WasmHostFunction | None] | None = None,
) -> InterpreterExecutionControl:
    """Build the production execution control through the composition root."""
    from tier2_runtime.runtime.composer import RuntimeCompositionConfig, RuntimePluginSelection

    return InterpreterExecutionControl(
        RuntimeCompositionConfig(plugins=RuntimePluginSelection(debugger=True)),
        module,
        make_interpreter_bindings(module, memory=memory, host_functions=host_functions),
        func_index,
        args,
    )
