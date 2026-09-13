from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: WASM Interpreter & Instructions
Traceability: runtime_interpreter_test_spec.md, wasm_instruction_set_test_spec.md
"""

import struct
import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from interpreter import Interpreter, InterpreterContext, Trap
from scheduler import Scheduler
from system_containers import StaticVector
from vmmio import VMMIOController
from wasm_module import F64, I32, I64, Function, FuncType, Module
from wasm_reader import WasmUnsupportedFeatureError, parse


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_intp_01_02_cps_handlers_and_dispatch_table():
    """TEST-INTP-01, 02: Opcode handlers use the direct raw signature and array dispatch."""
    import inspect

    from interpreter import _HANDLERS

    # Direct 256-element array table (no dynamic dict lookup)
    assert type(_HANDLERS) is list
    assert len(_HANDLERS) == 256
    # Every registered handler must accept exactly 4 raw arguments.
    registered_count = 0
    for op, handler in enumerate(_HANDLERS):
        if handler is not None:
            registered_count += 1
            sig = inspect.signature(handler)
            assert len(sig.parameters) == 4, (
                f"Handler for opcode 0x{op:02X} must have exactly 4 arguments"
            )
            assert tuple(sig.parameters) == ("ctx", "sp", "local_base", "tos"), (
                f"Handler for opcode 0x{op:02X} must use the native CPS prototype"
            )

    assert registered_count >= 30, (
        f"Expected at least 30 registered MVP opcode handlers, found {registered_count}"
    )


def test_intp_03_control_frame_enum_and_opcode_attribute_table():
    """TEST-INTP-03: Control-frame kinds and loader opcode metadata are typed and shared."""
    from control_flow import OpcodeAttribute, opcode_has_attribute
    from interpreter import ControlFrameKind, NativeControlStack
    from wasm_opcodes import BR_IF, CALL, I32_ADD, LOOP

    control_stack = NativeControlStack(capacity=1)
    assert control_stack.push_back(ControlFrameKind.LOOP, start=0, match_end=4, stack_height=0)
    assert control_stack[0].kind == int(ControlFrameKind.LOOP)
    assert opcode_has_attribute(CALL, OpcodeAttribute.CALL)
    assert opcode_has_attribute(BR_IF, OpcodeAttribute.BRANCH)
    assert opcode_has_attribute(LOOP, OpcodeAttribute.BASIC_BLOCK_BOUNDARY)
    assert not opcode_has_attribute(I32_ADD, OpcodeAttribute.BASIC_BLOCK_BOUNDARY)


def test_intp_04_locals_use_fixed_eight_byte_slots():
    """Each logical local uses a fixed 8-byte slot; wide values stay aligned."""
    function = Function(
        type_index=0,
        locals_extra=(I32, I64),
        code=bytes((0x20, 1, 0x0B)),
    )
    module = Module(
        types=(FuncType(params=(I32, I64, F64), results=(I64,)),),
        functions=(function,),
    )

    assert function.param_packed_slot_count_cache == 5
    assert function.local_widths_cache == (1, 2, 2, 1, 2)
    assert function.local_slot_count_cache == 10
    assert Interpreter(module).call(0, [7, 42, 3.5]) == [42]


def test_intp_04_control_map_uses_four_entry_locality_caches():
    """The small per-function ControlMap keeps only four direct-mapped cache slots."""
    from control_flow import build_control_map

    control_map = build_control_map(b"\x02\x40\x0B")
    assert len(control_map.block_cache) == 4
    assert len(control_map.br_table_cache) == 4
    for ip in (0, 1, 2, 0xFFFF):
        assert 0 <= control_map._cache_slot(ip) < 4


def test_intp_05_handler_returns_trap_outcome():
    """A WASM trap is an explicit handler outcome, not an absent continuation."""
    from interpreter import _HANDLERS
    from wasm_opcodes import UNREACHABLE

    module = parse(wat_to_wasm("(module (func unreachable))"))
    call_state = Interpreter(module).start(0, [])
    assert call_state.cont is not None
    ip, frame, local_base, tos = call_state.cont
    call_state.context.bind_handler_state(ip, frame)

    handler = _HANDLERS[UNREACHABLE]
    assert handler is not None
    result = handler(call_state.context, frame.values, local_base, tos)
    assert result is not None
    result_ctx, result_sp, result_locals, result_tos, trap = result
    assert result_ctx is call_state.context
    assert result_sp is frame.values
    assert result_locals is local_base
    assert result_tos == tos
    assert isinstance(trap, Trap)

    stepped = Interpreter(module).step(Interpreter(module).start(0, []))
    assert stepped.finished
    assert stepped.results is None
    assert isinstance(stepped.trap, Trap)


def test_intp_06_memory_traps_are_handler_results():
    """vMMIO faults leave the handler as a trap result, never as a Python exception."""
    module = parse(
        wat_to_wasm("(module (func (param i32) (result i32) local.get 0 i32.load))")
    )
    interp = Interpreter(module, memory=bytearray(65536))
    stepped = interp.step(interp.start(0, [0x8000_0000]))
    assert stepped.finished
    assert stepped.results is None
    assert isinstance(stepped.trap, Trap)


def test_intp_07_vmmio_syscall_doorbell_and_vector_table():
    """Interpreter load/store reaches host syscalls through static vMMIO."""
    from system import SYS_CONTROL_SYSCALL, SYSCTL_BASE, System

    sysv = System()
    sysv.start_runtime_task(name="interpreter_runtime_task")
    try:
        module = parse(
            wat_to_wasm(
                f"""(module
                  (memory 1)
                  (func (result i32)
                    i32.const {SYSCTL_BASE + 0x10}
                    i32.const 2
                    i32.store
                    i32.const {SYSCTL_BASE + 0x00}
                    i32.const {SYS_CONTROL_SYSCALL}
                    i32.store
                    i32.const {SYSCTL_BASE + 0x18}
                    i32.load))"""
            )
        )
        interp = Interpreter(
            module,
            memory=bytearray(65536),
            vmmio=sysv.vmmio,
            phys_mem=sysv.phys_mem,
        )
        result = interp.call(0, [])
        assert result[0] == 0
        assert sysv.halted
        assert sysv.sysctl_regs[0x18:0x1C] == b"\x00\x00\x00\x00"
    finally:
        sysv.shutdown()

    scheduler = Scheduler()
    task_id = scheduler.spawn("vector_task")
    scheduler.current_task = scheduler.get_task(task_id)
    ctrl = VMMIOController(guest_ram_size=65536, scheduler=scheduler)
    vector_page = (0xC000_0000 | (3 << 16))
    ctrl.map_static_device(vector_page >> 12)
    interp_module = parse(
        wat_to_wasm(
            f"""(module
              (memory 1)
              (func (result i32)
                i32.const {vector_page}
                i32.load))"""
        )
    )
    interp = Interpreter(interp_module, memory=bytearray(65536), vmmio=ctrl)
    interp.register_vector_table(
        (None, None, None, lambda offset, value, is_write: 0x1234)
    )
    result = interp.call(0, [])
    assert result[0] == 0x1234


def test_wasm_01_to_06_unsupported_features_rejected():
    """TEST-WASM-01..06: Unsupported features (SIMD, threads, tail-call) are rejected with error code."""
    # Module with unsupported SIMD opcode 0xFD
    wasm_bytes = (
        b"\x00asm\x01\x00\x00\x00"
        b"\x01\x05\x01\x60\x00\x01\x7f"
        b"\x03\x02\x01\x00"
        b"\x07\x0d\x01\x09test_simd\x00\x00"
        b"\x0a\x06\x01\x04\x00\xfd\x00\x0b"
    )
    try:
        mod = parse(wasm_bytes)
        interp = Interpreter(mod)
        interp.call(0, [])
        raise AssertionError("Expected WasmUnsupportedFeatureError for SIMD opcode")
    except WasmUnsupportedFeatureError as e:
        assert "ERR_WASM_UNSUPPORTED_FEATURE" in str(e)


def test_wasm_10_to_15_control_flow_and_calls():
    """TEST-WASM-10..15: Unreachable trap, block/loop/if/br_table, call, and call_indirect."""
    wat = """
    (module
      (table 2 2 funcref)
      (type $sig_calc (func (param i32) (result i32)))
      (func $unreachable_fn (export "unreachable_fn")
        (unreachable)
      )
      (func $calc_fn (export "calc_fn") (param $x i32) (result i32)
        (block $b0
          (block $b1
            (br_table $b1 $b0 (local.get $x))
          )
          (return (i32.const 100))
        )
        (return (i32.const 200))
      )
      (func $call_ind (export "call_ind") (param $arg i32) (param $idx i32) (result i32)
        (call_indirect (type $sig_calc) (local.get $arg) (local.get $idx))
      )
      (elem (i32.const 0) $calc_fn $calc_fn)
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_10_to_15")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # TEST-WASM-10: unreachable traps
    try:
        interp.call(mod.export_func_index("unreachable_fn"), [])
        raise AssertionError("Expected Trap for unreachable")
    except Trap:
        pass
    # TEST-WASM-13: br_table branch resolution
    assert interp.call(mod.export_func_index("calc_fn"), [0]) == [100]
    assert interp.call(mod.export_func_index("calc_fn"), [1]) == [200]
    # TEST-WASM-15: call_indirect
    assert interp.call(mod.export_func_index("call_ind"), [0, 0]) == [100]
    assert interp.call(mod.export_func_index("call_ind"), [1, 1]) == [200]


def test_wasm_20_21_drop_and_select():
    """TEST-WASM-20..21: drop and select parametric instructions."""
    wat = """
    (module
      (func $sel (export "sel") (param $cond i32) (param $val1 i32) (param $val2 i32) (result i32)
        (drop (local.get $cond))
        (select (local.get $val1) (local.get $val2) (local.get $cond))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_20_21")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("sel"), [1, 10, 20]) == [10]
    assert interp.call(mod.export_func_index("sel"), [0, 10, 20]) == [20]


def test_wasm_30_31_locals_and_globals():
    """TEST-WASM-30..31: local.get/set/tee and global.get/set."""
    wat = """
    (module
      (global $g (mut i32) (i32.const 42))
      (func $loc_glob (export "loc_glob") (param $p0 i32) (result i32)
        (local $l1 i32)
        (local.set $l1 (local.get $p0))
        (global.set $g (local.get $l1))
        (i32.add (global.get $g) (local.get $l1))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_30_31")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("loc_glob"), [5]) == [10]
    assert interp.globals[0] == 5


def test_wasm_40_to_46_memory_load_store_grow_and_data():
    """TEST-WASM-40..46 & TEST-WASM-60: Linear memory load, store, size, grow, bounds traps, and Data segments."""
    wat = """
    (module
      (memory 1 2)
      (data (i32.const 0) "WASM_INIT")
      (func $mem_ops (export "mem_ops") (result i32)
        (drop (i32.load (i32.const 0)))
        (i32.store (i32.const 16) (i32.const 0x12345678))
        (drop (memory.grow (i32.const 1)))
        (memory.size)
      )
      (func $trap_oob (export "trap_oob")
        (drop (i32.load (i32.const 0x1000000)))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_40_to_46")
        return
    mod = parse(wasm_bytes)
    mem = bytearray(65536)
    mod.init_memory_data(mem)
    interp = Interpreter(mod, memory=mem)
    # Initial data check
    assert bytes(mem[0:9]) == b"WASM_INIT"
    # Execution
    pages = interp.call(mod.export_func_index("mem_ops"), [])
    assert pages == [2]
    assert struct.unpack_from("<I", mem, 16)[0] == 0x12345678
    # OOB trap check
    try:
        interp.call(mod.export_func_index("trap_oob"), [])
        raise AssertionError("Expected Trap on out of bounds memory access")
    except Trap:
        pass


def test_wasm_50_to_56_integer_arithmetic_and_bitwise():
    """TEST-WASM-50..56: 32-bit integer arithmetic, div-by-zero trap, popcnt, clz, rotl, rotr."""
    wat = """
    (module
      (func $div_s (export "div_s") (param $a i32) (param $b i32) (result i32)
        (i32.div_s (local.get $a) (local.get $b))
      )
      (func $bit_ops (export "bit_ops") (param $x i32) (result i32)
        (i32.xor
          (i32.add (i32.popcnt (local.get $x)) (i32.clz (local.get $x)))
          (i32.rotl (local.get $x) (i32.const 4))
        )
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_50_to_56")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # TEST-WASM-54: Div by zero traps
    try:
        interp.call(mod.export_func_index("div_s"), [10, 0])
        raise AssertionError("Expected Trap on division by zero")
    except Trap:
        pass
    # Normal div
    assert interp.call(mod.export_func_index("div_s"), [10, 2]) == [5]
    # TEST-WASM-52, 55, 56: Bit ops
    # x = 0x80000001 -> popcnt=2, clz=0 -> sum=2. rotl(x, 4) = 0x00000018. 2 ^ 0x18 = 0x1A (26)
    assert interp.call(mod.export_func_index("bit_ops"), [0x80000001]) == [26]


def test_wasm_f32_arithmetic_min_max_and_precision():
    """TEST-WASM-57: F32 single-precision rounding, IEEE 754 min/max with NaNs and signed zeroes."""
    import math

    wat = """
    (module
      (func $f32_add (export "f32_add") (param $a f32) (param $b f32) (result f32)
        (f32.add (local.get $a) (local.get $b))
      )
      (func $f32_min (export "f32_min") (param $a f32) (param $b f32) (result f32)
        (f32.min (local.get $a) (local.get $b))
      )
      (func $f32_max (export "f32_max") (param $a f32) (param $b f32) (result f32)
        (f32.max (local.get $a) (local.get $b))
      )
      (func $f32_demote (export "f32_demote") (param $d f64) (result f32)
        (f32.demote_f64 (local.get $d))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_f32_arithmetic")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)

    # 1. Single precision rounding: 1.0 + 1e-8 in double is > 1.0, but in f32 it rounds to 1.0
    res = interp.call(mod.export_func_index("f32_add"), [1.0, 1e-8])
    assert res[0] == 1.0

    # 2. Signed zero min/max: min(-0.0, +0.0) must preserve -0.0
    res_min = interp.call(mod.export_func_index("f32_min"), [-0.0, 0.0])
    assert math.copysign(1.0, res_min[0]) == -1.0
    res_min_rev = interp.call(mod.export_func_index("f32_min"), [0.0, -0.0])
    assert math.copysign(1.0, res_min_rev[0]) == -1.0

    res_max = interp.call(mod.export_func_index("f32_max"), [-0.0, 0.0])
    assert math.copysign(1.0, res_max[0]) == 1.0
    res_max_rev = interp.call(mod.export_func_index("f32_max"), [0.0, -0.0])
    assert math.copysign(1.0, res_max_rev[0]) == 1.0

    # 3. NaN propagation
    res_nan = interp.call(mod.export_func_index("f32_min"), [float("nan"), 42.0])
    assert math.isnan(res_nan[0])
    res_nan2 = interp.call(mod.export_func_index("f32_max"), [42.0, float("nan")])
    assert math.isnan(res_nan2[0])

    # 4. f32.demote_f64 rounding
    demote_res = interp.call(mod.export_func_index("f32_demote"), [1.0000000000000002])
    assert demote_res[0] == 1.0


# ===========================================================================
# System Containers (TEST-CONT-01 .. TEST-CONT-10)
# ===========================================================================


def test_wasm_loader_and_radix_binary_tree_view_indexes():
    """TEST-LOAD-01..47: Verifies WASM Loader zero-copy indexing, verification, and RadixBinaryTreeView file offset & hash symbol indexes."""
    from loader import WasmLoader, WasmVerifyError
    from test_loader import _build_test_wasm_binary

    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("test_module", wasm_bytes)
    # 1. Zero-copy & Hash + RadixBinaryTreeView export lookup (TEST-LOAD-13)
    assert [e.name for e in view.exports_dict] == ["alpha", "beta", "zeta"]
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("unknown") is None
    # 2. Transactional rollback on invalid WASM
    watermark = loader.allocator.offset
    try:
        loader.prepare("bad", _build_test_wasm_binary(magic=b"\x7fELF"))
        assert False
    except WasmVerifyError:
        pass
    assert loader.allocator.offset == watermark
    # 3. RadixBinaryTreeView file offset reverse-lookup (TEST-LOAD-40..44)
    assert len(view.entity_registry) > 0
    func_start, func_size = view.code_offsets[0]
    entity_fn = view.lookup_by_file_offset(func_start)
    assert entity_fn is not None
    assert entity_fn.kind == "FUNCTION"
    assert entity_fn.name_or_idx == 0
    # Middle of function
    entity_fn_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_fn_mid is not None
    assert entity_fn_mid.kind == "FUNCTION"
    # Global lookup
    glob_entry = view.globals[0]
    entity_glob = view.lookup_by_file_offset(glob_entry.init_expr_offset)
    assert entity_glob is not None
    assert entity_glob.kind == "GLOBAL"
    # Out-of-bounds offset
    assert view.lookup_by_file_offset(len(wasm_bytes) + 1000) is None


def test_intp_70_to_72_direct_bytecode_execution():
    """TEST-INTP-70..72 & GOTCHA-INTP-05: Direct bytecode decoding without instruction objects or binary search."""
    wat = """
    (module
      (func (export "calc") (param $x i32) (result i32)
        (local $res i32)
        (local.set $res (i32.add (local.get $x) (i32.const 10)))
        (block $b
          (if (i32.gt_s (local.get $x) (i32.const 5))
            (then
              (local.set $res (i32.add (local.get $res) (i32.const 100)))
            )
            (else
              (local.set $res (i32.add (local.get $res) (i32.const 200)))
            )
          )
        )
        (local.get $res)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_intp_70_to_72")
        return
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    # 1. TEST-INTP-70: the context owns the call-frame and LocalStack construction.
    context = InterpreterContext(module)
    frame, locals_arr = interp._build_frame(0, StaticVector.of((15,), capacity=64), context)
    assert frame.code == module.functions[0].code
    assert frame.control_map is not None
    assert frame.control_map is module.functions[0].control_map
    assert context.call_frame_stack[-1] is frame
    assert context.local_offset == 4
    assert len(context.local_stack) == 4
    context.end_call_frame(frame)
    assert context.local_offset == 0
    assert len(context.local_stack) == 0

    # 2. TEST-INTP-71 & TEST-INTP-72: Execution proceeds by direct byte reading and ip addition
    res = interp.call(0, [15])
    # 15 + 10 + 100 = 125
    assert res == [125]

    res2 = interp.call(0, [3])
    # 3 + 10 + 200 = 213
    assert res2 == [213]


# ===========================================================================
# Test Runner
# ===========================================================================

ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} comprehensive pysim invariant tests passed.")

if __name__ == "__main__":
    test_intp_01_02_cps_handlers_and_dispatch_table()
    test_intp_04_locals_use_fixed_eight_byte_slots()
    test_wasm_01_to_06_unsupported_features_rejected()
    test_wasm_10_to_15_control_flow_and_calls()
    test_wasm_20_21_drop_and_select()
    test_wasm_30_31_locals_and_globals()
    test_wasm_40_to_46_memory_load_store_grow_and_data()
    test_wasm_50_to_56_integer_arithmetic_and_bitwise()
    test_wasm_loader_and_radix_binary_tree_view_indexes()
    print("[PASS] All 8 WASM Interpreter & Instructions tests passed.")
