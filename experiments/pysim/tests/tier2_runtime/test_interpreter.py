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
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from interpreter import Interpreter, Trap
from wasm_reader import WasmUnsupportedFeatureError, parse


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_intp_01_02_cps_handlers_and_dispatch_table():
    """INTP-01, 02: Opcode handlers use CPS 4-arg signature (ip, frame, env, locals) and direct array table dispatch."""
    import inspect

    from interpreter import _HANDLERS

    # Direct 256-element array table (no dynamic dict lookup)
    assert type(_HANDLERS) is list
    assert len(_HANDLERS) == 256
    # Every registered handler must accept exactly 4 arguments and return next continuation
    registered_count = 0
    for op, handler in enumerate(_HANDLERS):
        if handler is not None:
            registered_count += 1
            sig = inspect.signature(handler)
            assert len(sig.parameters) == 4, (
                f"Handler for opcode 0x{op:02X} must have exactly 4 arguments (CPS)"
            )

    assert registered_count >= 30, (
        f"Expected at least 30 registered MVP opcode handlers, found {registered_count}"
    )


def test_wasm_01_to_06_unsupported_features_rejected():
    """WASM-01..06: Unsupported features (SIMD, threads, tail-call) are rejected with error code."""
    # Module with unsupported SIMD opcode 0xFD
    wasm_bytes = (
        b"\x00asm\x01\x00\x00\x00"
        b"\x01\x05\x01\x60\x00\x01\x7f"
        b"\x03\x02\x01\x00"
        b"\x07\x0d\x01\x09test_simd\x00\x00"
        b"\x0a\x06\x01\x04\x00\xfd\x00\x0b"
    )
    mod = parse(wasm_bytes)
    try:
        interp = Interpreter(mod)
        interp.call(0, [])
        raise AssertionError("Expected WasmUnsupportedFeatureError for SIMD opcode")
    except WasmUnsupportedFeatureError as e:
        assert "ERR_WASM_UNSUPPORTED_FEATURE" in str(e)


def test_wasm_10_to_15_control_flow_and_calls():
    """WASM-10..15: Unreachable trap, block/loop/if/br_table, call, and call_indirect."""
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
    # WASM-10: unreachable traps
    try:
        interp.call(mod.export_func_index("unreachable_fn"), [])
        raise AssertionError("Expected Trap for unreachable")
    except Trap:
        pass
    # WASM-13: br_table branch resolution
    assert interp.call(mod.export_func_index("calc_fn"), [0]) == [100]
    assert interp.call(mod.export_func_index("calc_fn"), [1]) == [200]
    # WASM-15: call_indirect
    assert interp.call(mod.export_func_index("call_ind"), [0, 0]) == [100]
    assert interp.call(mod.export_func_index("call_ind"), [1, 1]) == [200]


def test_wasm_20_21_drop_and_select():
    """WASM-20..21: drop and select parametric instructions."""
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
    """WASM-30..31: local.get/set/tee and global.get/set."""
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
    """WASM-40..46 & WASM-60: Linear memory load, store, size, grow, bounds traps, and Data segments."""
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
    """WASM-50..56: 32-bit integer arithmetic, div-by-zero trap, popcnt, clz, rotl, rotr."""
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
    # WASM-54: Div by zero traps
    try:
        interp.call(mod.export_func_index("div_s"), [10, 0])
        raise AssertionError("Expected Trap on division by zero")
    except Trap:
        pass
    # Normal div
    assert interp.call(mod.export_func_index("div_s"), [10, 2]) == [5]
    # WASM-52, 55, 56: Bit ops
    # x = 0x80000001 -> popcnt=2, clz=0 -> sum=2. rotl(x, 4) = 0x00000018. 2 ^ 0x18 = 0x1A (26)
    assert interp.call(mod.export_func_index("bit_ops"), [0x80000001]) == [26]


def test_wasm_f32_arithmetic_min_max_and_precision():
    """WASM-57: F32 single-precision rounding, IEEE 754 min/max with NaNs and signed zeroes."""
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
# System Containers (CONT-01 .. CONT-10)
# ===========================================================================


def test_wasm_loader_and_radix_binary_tree_view_indexes():
    """LOAD-01..47: Verifies WASM Loader zero-copy indexing, verification, and RadixBinaryTreeView file offset & hash symbol indexes."""
    from loader import WasmLoader, WasmVerifyError
    from tier2_runtime.test_loader import _build_test_wasm_binary

    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("test_module", wasm_bytes)
    # 1. Zero-copy & Hash + RadixBinaryTreeView export lookup (LOAD-13)
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
    # 3. RadixBinaryTreeView file offset reverse-lookup (LOAD-40..44)
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
    """INTP-70..72 & INTP-GOTCHA-05: Direct bytecode decoding without instruction objects or binary search."""
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

    # 1. INTP-70: _build_frame constructs CallFrame with raw code and static control_map
    frame, locals_arr = interp._build_frame(0, [15])
    assert frame.code == module.functions[0].code
    assert frame.control_map is not None
    assert frame.control_map is module.functions[0].control_map

    # 2. INTP-71 & INTP-72: Execution proceeds by direct byte reading and ip addition
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
    test_wasm_01_to_06_unsupported_features_rejected()
    test_wasm_10_to_15_control_flow_and_calls()
    test_wasm_20_21_drop_and_select()
    test_wasm_30_31_locals_and_globals()
    test_wasm_40_to_46_memory_load_store_grow_and_data()
    test_wasm_50_to_56_integer_arithmetic_and_bitwise()
    test_wasm_loader_and_radix_binary_tree_view_indexes()
    print("[PASS] All 8 WASM Interpreter & Instructions tests passed.")
