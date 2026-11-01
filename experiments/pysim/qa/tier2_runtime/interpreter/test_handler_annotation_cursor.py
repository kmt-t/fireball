"""Each Interpreter handler advances rel by exactly the loader's annotation layout.

The observation uses the existing debugger dispatch (RUN_DEBUG_DISPATCH through _DebugNativeInterpreter):
it stops before every instruction and records the pc and rel at each stop. For two consecutive stops:
- when the next stop is a basic-block head, rel must equal that block's annotation start in its
  function's pool (control transfers, calls and their returns);
- otherwise rel must advance by the loader's annotation size of the instruction between the stops.
"""

from __future__ import annotations

import pytest
from qa.shared.helpers import make_interpreter_bindings, wat_to_wasm
from tier2_runtime.interpreter.control_flow import _count_interpreter_annotation_pool_bytes
from tier2_runtime.wasm.reader import parse_standalone
from tier3_plugins.debugger.debugger import _DebugNativeInterpreter, _NativeDebugControl

CONTROL_WAT = """(module (memory 1) (table 2 funcref) (type $t (func (param i32) (result i32)))
  (global $g (mut i32) (i32.const 0))
  (data (i32.const 0) "abcd")
  (func $callee (param i32) (result i32) local.get 0 i32.const 2 i32.mul)
  (elem (i32.const 0) $callee)
  (func (export "ctl") (param $n i32) (result i32) (local $i i32) (local $s i32)
    (block $out (loop $lp
      local.get $i local.get $n i32.ge_s br_if $out
      local.get $i call $callee local.set $s
      local.get $i i32.const 0 call_indirect (type $t) local.get $s i32.add local.set $s
      (block $a (block $b local.get $i i32.const 1 i32.and br_table $b $a))
      local.get $i i32.const 1 i32.and
      if (result i32) i32.const 1 else i32.const 2 end drop
      global.get 0 i32.const 1 i32.add global.set 0
      local.get $i i32.const 1 i32.add local.set $i
      br $lp))
    local.get $s)
  (func (export "ret") (param i32) (result i32) local.get 0 return)
  (func (export "sel") (param i32) (result i32)
    local.get 0 local.get 0 i32.const 3 select)
  (func (export "fc") (param i32) (result i32)
    local.get 0 f32.convert_i32_s i32.trunc_sat_f32_s)
  (func (export "mem") (param i32) (result i32)
    i32.const 8 i32.const 0 i32.const 4 memory.copy
    i32.const 16 i32.const 7 i32.const 4 memory.fill
    i32.const 0 i64.load8_s drop i32.const 0 i64.load32_u drop
    i32.const 0 i64.const 5 i64.store16
    local.get 0)
)"""

CASES = (("ctl", [5]), ("ret", [3]), ("sel", [4]), ("fc", [9]), ("mem", [1]))


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


def _check_stops(module, stops) -> int:
    """Return the number of checked consecutive pairs; fail on the first disagreement."""
    heads = {block.head_pc: block for block in module.blocks}
    checked = 0
    for (pc_before, rel_before), (pc_after, rel_after) in zip(stops, stops[1:]):
        index_before = module.function_index_for_pc(pc_before)
        index_after = module.function_index_for_pc(pc_after)
        assert index_before is not None and index_after is not None
        if pc_after in heads:
            block = heads[pc_after]
            pool = module.functions[index_after - len(module.imports)].interpreter_annotation_pool
            expected = pool.native_address + block.interpreter_annotation_byte_offset
            assert rel_after == expected, (
                f"transfer to pc {pc_after}: rel {rel_after} is not the block annotation {expected}"
            )
        else:
            assert index_before == index_after
            base = module.function_pc_offset(index_before)
            code = module.code_for(index_before)
            expected_size = _count_interpreter_annotation_pool_bytes(
                code[pc_before - base : pc_after - base]
            )
            assert rel_after - rel_before == expected_size, (
                f"instruction at pc {pc_before}: handler advanced {rel_after - rel_before}, "
                f"loader annotation size {expected_size}"
            )
        checked += 1
    return checked


@pytest.mark.parametrize(("export", "arguments"), CASES)
def test_handler_rel_matches_the_loader_layout(export, arguments):
    """Every consecutive pair of stops agrees with the loader's annotation layout."""
    module = parse_standalone(wat_to_wasm(CONTROL_WAT))
    stops = _stops(module, export, arguments)
    assert stops
    assert _check_stops(module, stops) == len(stops) - 1


def _aobench_setup():
    """The benchmark's own host setup (fd_write and friends), so AO imports resolve as in the benchmark."""
    from pathlib import Path

    from ipc_router import FB_URI_HAL_STDOUT
    from system import System
    from tier2_runtime.interpreter.interpreter import InterpreterBindings
    from tier2_runtime.runtime.engine import RuntimeEngine
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.hal.stream import StreamTransport
    from tier3_platform.drivers.wasi.context import WasiHostContext

    wasm = Path(__file__).resolve().parents[3] / "benchmarks" / "aobench" / "aobench.wasm"
    module = parse_standalone(memoryview(wasm.read_bytes()))
    system = System()
    system.transport = StreamTransport(1 << 20)
    wasi = WasiHostContext.create_default(system)
    system.start_hal_driver(
        DummyDriver(system.pool, transport=system.transport), FB_URI_HAL_STDOUT
    ).unwrap()
    functions = wasi.build_interpreter_host_functions(module)
    module.init_memory_data(wasi.guest_memory, ())
    RuntimeEngine().register_module_blocks(module)
    bindings = InterpreterBindings.with_memory_and_functions(wasi.guest_memory, functions)
    return module, bindings


def test_handler_rel_matches_the_loader_layout_on_aobench():
    """All of AO at a 16x9 frame: every consecutive pair of stops agrees with the loader's layout."""
    module, bindings = _aobench_setup()
    control = _NativeDebugControl()
    interpreter = _DebugNativeInterpreter(module, bindings, control)
    call = interpreter.start(module.export_func_index("main"), [16, 9])
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
    assert len(stops) > 1000
    assert _check_stops(module, stops) == len(stops) - 1
