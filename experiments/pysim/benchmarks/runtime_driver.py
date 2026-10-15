"""Synchronous helper for benchmarks that must drive the production Runtime boundary."""

from __future__ import annotations

from system_containers import SequenceView as Sequence
from system_containers import StaticVector
from tier2_runtime.interpreter.errors import Trap
from tier2_runtime.interpreter.interpreter import NativeInterpreter
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.runtime.python_api import WasmNumber
from tier2_runtime.runtime.recovery import Result


def run_runtime_call(
    runtime: RuntimeEngine,
    interpreter: NativeInterpreter,
    func_index: int,
    args: Sequence[WasmNumber],
) -> Result[StaticVector[WasmNumber], Trap]:
    """Drive scheduler boundaries and service the runtime-owned idle hook."""
    call_state = interpreter.start(func_index, args)
    while not call_state.finished:
        call_state = runtime.run(interpreter, call_state).call_state
    runtime.idle_hook()
    if call_state.trap is not None:
        return Result.err(call_state.trap)
    assert call_state.results is not None
    return Result.ok(call_state.results)
