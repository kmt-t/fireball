"""Each Interpreter handler advances rel by exactly the loader's annotation size of its instruction.

The observation uses the existing debugger dispatch (RUN_DEBUG_DISPATCH through _DebugNativeInterpreter):
it stops before every instruction and records the rel and pc at that stop. The advance of one instruction
is the difference between two consecutive stops, compared with the loader's own annotation count.
"""

from __future__ import annotations

import pytest
from qa.shared.helpers import make_interpreter_bindings, wat_to_wasm
from tier2_runtime.interpreter.control_flow import _count_interpreter_annotation_pool_bytes
from tier2_runtime.wasm.reader import parse_standalone
from tier3_plugins.debugger.debugger import _DebugNativeInterpreter, _NativeDebugControl

BODIES = (
    """(module (memory 1) (global $g (mut i32) (i32.const 0))
      (func (export "main") (param i32) (result i32) (local i32)
        local.get 0 i32.const 3 i32.add local.set 1
        local.get 1 drop i64.const 0 drop global.get 0 drop
        local.get 1 i32.const 0 i32.load offset=4 drop drop
        local.get 1))""",
    """(module (memory 1)
      (func (export "main") (param i32) (result i32) (local i32 i32)
        local.get 0 local.tee 1 drop
        i32.const 7 local.get 1 i32.store offset=8
        local.get 0 i32.const 1 i32.add))""",
)


def _stops(module, export: str, arguments: list[int]):
    bindings = make_interpreter_bindings(module, None, None, None, None, None)
    control = _NativeDebugControl()
    interpreter = _DebugNativeInterpreter(module, bindings, control)
    call = interpreter.start(module.export_func_index(export), arguments)
    control.enabled = 1
    control.single_step = 1
    stops = []
    while not call.finished:
        control.executed = 0
        control.stopped = 0
        while not call.finished and control.stopped == 0:
            interpreter.step(call)
        if not call.finished:
            stops.append((call.current_pc(), int(call.context.rel or 0)))
    return stops


@pytest.mark.parametrize("wat", BODIES)
def test_handler_rel_advance_equals_the_loader_annotation_size(wat):
    """Every consecutive pair of stops advances rel by the loader's size of the instruction between them."""
    module = parse_standalone(wat_to_wasm(wat))
    export = "main"
    function_index = module.export_func_index(export)
    base = module.function_pc_offset(function_index)
    code = module.code_for(function_index)
    stops = _stops(module, export, [5])
    assert len(stops) >= 2
    for (pc_before, rel_before), (pc_after, rel_after) in zip(stops, stops[1:]):
        expected = _count_interpreter_annotation_pool_bytes(
            code[pc_before - base : pc_after - base]
        )
        assert rel_after - rel_before == expected, (
            f"instruction at offset {pc_before - base}: handler advanced {rel_after - rel_before}, "
            f"loader annotation size {expected}"
        )
