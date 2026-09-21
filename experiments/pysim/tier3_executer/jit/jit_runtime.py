"""Tier 3 JIT execution strategy built on the Interpreter template boundary."""

from __future__ import annotations

from tier2_runtime.logger import Logger
from tier2_runtime.runtime_engine import RuntimeEngine
from tier3_executer.interpreter.interpreter import (
    Interpreter,
    InterpreterBindings,
    InterpreterCall,
    WasmNumber,
)
from system_containers import StaticVector
from vmmio import VMMIOController
from wasm_module import Module


class JITInterpreter(Interpreter):
    """Interpreter の共通 ``call`` 境界だけを継承し、実行ドライバをJITへ差し替える。"""

    __slots__ = ("idle_budget", "runtime_engine")

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        runtime_engine: RuntimeEngine,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: Logger | None = None,
        idle_budget: int = 4,
    ):
        assert idle_budget >= 1
        self.runtime_engine = runtime_engine
        self.idle_budget = idle_budget
        if runtime_engine.module is None:
            runtime_engine.register_module_blocks(module)
        else:
            assert runtime_engine.module is module
        super().__init__(module, bindings, vmmio=vmmio, phys_mem=phys_mem, logger=logger)

    def _complete_call(self, call_state: InterpreterCall) -> StaticVector[WasmNumber]:
        """共通テンプレートの実行フックをTier 2 RuntimeEngineへ委譲する。"""

        self.runtime_engine._virq_interp = self
        return self.runtime_engine._drive_call(self, call_state, self.idle_budget)
