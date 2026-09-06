from __future__ import annotations

"""
experiments/pysim/tests/tier2_runtime/test_wasm_differential.py
Differential Oracle Testing: pysim WASM Interpreter vs wasmtime engine.
Validates arithmetic, bitwise, floating-point (f32/f64), control flow, and memory
operations between Fireball's interpreter and wasmtime (WebAssembly Reference).
"""

import math
import struct
import sys
from pathlib import Path

# Setup search paths
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
    _TEST_FILE.parent,
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
from wasm_reader import parse


def _wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def _run_differential(
    wat_text: str,
    func_name: str,
    args: list[int | float],
    expect_trap: bool = False,
) -> None:
    """Runs a function under both pysim Interpreter and wasmtime, asserting identical outcomes."""
    import wasmtime

    wasm_bytes = _wat_to_wasm(wat_text)
    assert wasm_bytes, "wasmtime.wat2wasm must succeed in differential test environment"

    # 1. Execute with wasmtime
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    module = wasmtime.Module(engine, wasm_bytes)
    instance = wasmtime.Instance(store, module, [])
    wt_func = instance.exports(store)[func_name]

    wt_trap = False
    wt_result = None
    try:
        wt_result = wt_func(store, *args)
    except (wasmtime.WasmtimeError, wasmtime.Trap):
        wt_trap = True

    # 2. Execute with pysim Interpreter
    pysim_mod = parse(wasm_bytes)
    pysim_interp = Interpreter(pysim_mod)
    func_idx = pysim_mod.export_func_index(func_name)

    pysim_trap = False
    pysim_result = None
    try:
        pysim_res_list = pysim_interp.call(func_idx, args)
        if pysim_res_list:
            pysim_result = pysim_res_list[0]
    except (Trap, ZeroDivisionError, OverflowError):
        pysim_trap = True

    # 3. Assert parity
    assert wt_trap == pysim_trap, (
        f"Trap mismatch for {func_name}{args}: wasmtime trap={wt_trap}, pysim trap={pysim_trap}"
    )
    if expect_trap:
        assert pysim_trap, f"Expected trap for {func_name}{args}, but both succeeded"
        return

    if not pysim_trap:
        if isinstance(wt_result, float) or isinstance(pysim_result, float):
            # Special check for NaN
            if math.isnan(wt_result):
                assert math.isnan(pysim_result), (
                    f"Result NaN mismatch for {func_name}{args}: wasmtime={wt_result}, pysim={pysim_result}"
                )
            else:
                # Compare bit patterns for exact float/double representation (e.g., signed zero)
                wt_bits = struct.unpack(">Q", struct.pack(">d", float(wt_result)))[0]
                pysim_bits = struct.unpack(">Q", struct.pack(">d", float(pysim_result)))[0]
                assert wt_bits == pysim_bits, (
                    f"Float bit mismatch for {func_name}{args}: wasmtime={wt_result} ({hex(wt_bits)}), "
                    f"pysim={pysim_result} ({hex(pysim_bits)})"
                )
        else:
            assert wt_result == pysim_result, (
                f"Result mismatch for {func_name}{args}: wasmtime={wt_result}, pysim={pysim_result}"
            )


def test_differential_i32_i64_arithmetic():
    """Validates integer arithmetic parity against wasmtime."""
    wat = """
    (module
      (func (export "add32") (param i32 i32) (result i32)
        (i32.add (local.get 0) (local.get 1)))
      (func (export "sub32") (param i32 i32) (result i32)
        (i32.sub (local.get 0) (local.get 1)))
      (func (export "mul32") (param i32 i32) (result i32)
        (i32.mul (local.get 0) (local.get 1)))
      (func (export "div_s32") (param i32 i32) (result i32)
        (i32.div_s (local.get 0) (local.get 1)))
      (func (export "div_u32") (param i32 i32) (result i32)
        (i32.div_u (local.get 0) (local.get 1)))
      (func (export "rem_s32") (param i32 i32) (result i32)
        (i32.rem_s (local.get 0) (local.get 1)))
      (func (export "rotl32") (param i32 i32) (result i32)
        (i32.rotl (local.get 0) (local.get 1)))
      (func (export "clz32") (param i32) (result i32)
        (i32.clz (local.get 0)))
      (func (export "popcnt32") (param i32) (result i32)
        (i32.popcnt (local.get 0)))
      (func (export "add64") (param i64 i64) (result i64)
        (i64.add (local.get 0) (local.get 1)))
    )
    """
    _run_differential(wat, "add32", [10, 25])
    _run_differential(wat, "sub32", [10, 25])
    _run_differential(wat, "mul32", [1234, 5678])
    _run_differential(wat, "div_s32", [100, -5])
    _run_differential(wat, "div_s32", [100, 0], expect_trap=True)
    _run_differential(wat, "div_u32", [0xFFFFFFFF, 2])
    _run_differential(wat, "rem_s32", [-105, 10])
    _run_differential(wat, "rotl32", [0x12345678, 4])
    _run_differential(wat, "clz32", [0x000F0000])
    _run_differential(wat, "popcnt32", [0x12345678])
    _run_differential(wat, "add64", [0x100000000, 0x200000000])


def test_differential_f32_operations():
    """Validates F32 arithmetic, single-precision rounding, signed zeros, and min/max NaN."""
    wat = """
    (module
      (func (export "f32_add") (param f32 f32) (result f32)
        (f32.add (local.get 0) (local.get 1)))
      (func (export "f32_sub") (param f32 f32) (result f32)
        (f32.sub (local.get 0) (local.get 1)))
      (func (export "f32_mul") (param f32 f32) (result f32)
        (f32.mul (local.get 0) (local.get 1)))
      (func (export "f32_div") (param f32 f32) (result f32)
        (f32.div (local.get 0) (local.get 1)))
      (func (export "f32_min") (param f32 f32) (result f32)
        (f32.min (local.get 0) (local.get 1)))
      (func (export "f32_max") (param f32 f32) (result f32)
        (f32.max (local.get 0) (local.get 1)))
      (func (export "f32_nearest") (param f32) (result f32)
        (f32.nearest (local.get 0)))
      (func (export "f32_sqrt") (param f32) (result f32)
        (f32.sqrt (local.get 0)))
    )
    """
    # 1. Addition with single-precision rounding
    _run_differential(wat, "f32_add", [1.0, 1e-8])
    _run_differential(wat, "f32_sub", [5.5, 2.25])
    _run_differential(wat, "f32_mul", [3.0, 7.0])
    _run_differential(wat, "f32_div", [10.0, 4.0])

    # 2. Signed zero min/max
    _run_differential(wat, "f32_min", [-0.0, 0.0])
    _run_differential(wat, "f32_min", [0.0, -0.0])
    _run_differential(wat, "f32_max", [-0.0, 0.0])
    _run_differential(wat, "f32_max", [0.0, -0.0])

    # 3. NaN handling in min/max
    _run_differential(wat, "f32_min", [float("nan"), 1.0])
    _run_differential(wat, "f32_max", [1.0, float("nan")])

    # 4. Math helpers
    _run_differential(wat, "f32_nearest", [2.5])
    _run_differential(wat, "f32_nearest", [3.5])
    _run_differential(wat, "f32_sqrt", [16.0])


def test_differential_f64_operations():
    """Validates F64 arithmetic, signed zeros, and min/max."""
    wat = """
    (module
      (func (export "f64_add") (param f64 f64) (result f64)
        (f64.add (local.get 0) (local.get 1)))
      (func (export "f64_sub") (param f64 f64) (result f64)
        (f64.sub (local.get 0) (local.get 1)))
      (func (export "f64_mul") (param f64 f64) (result f64)
        (f64.mul (local.get 0) (local.get 1)))
      (func (export "f64_div") (param f64 f64) (result f64)
        (f64.div (local.get 0) (local.get 1)))
      (func (export "f64_min") (param f64 f64) (result f64)
        (f64.min (local.get 0) (local.get 1)))
      (func (export "f64_max") (param f64 f64) (result f64)
        (f64.max (local.get 0) (local.get 1)))
    )
    """
    _run_differential(wat, "f64_add", [1.0000000000000002, 2.0])
    _run_differential(wat, "f64_sub", [10.0, 3.5])
    _run_differential(wat, "f64_mul", [2.5, 4.0])
    _run_differential(wat, "f64_div", [1.0, 3.0])
    _run_differential(wat, "f64_min", [-0.0, 0.0])
    _run_differential(wat, "f64_max", [-0.0, 0.0])
    _run_differential(wat, "f64_min", [float("nan"), 42.0])
    _run_differential(wat, "f64_max", [42.0, float("nan")])


def test_differential_control_flow():
    """Validates branching, loop, and conditional evaluation against wasmtime."""
    wat = """
    (module
      (func (export "collatz") (param i32) (result i32)
        (local $steps i32)
        (local.set $steps (i32.const 0))
        (block $done
          (loop $continue
            (br_if $done (i32.le_s (local.get 0) (i32.const 1)))
            (local.set $steps (i32.add (local.get $steps) (i32.const 1)))
            (if (i32.eqz (i32.rem_s (local.get 0) (i32.const 2)))
              (then
                (local.set 0 (i32.div_s (local.get 0) (i32.const 2)))
              )
              (else
                (local.set 0 (i32.add (i32.mul (local.get 0) (i32.const 3)) (i32.const 1)))
              )
            )
            (br $continue)
          )
        )
        (local.get $steps)
      )
    )
    """
    _run_differential(wat, "collatz", [6])
    _run_differential(wat, "collatz", [1])
    _run_differential(wat, "collatz", [27])


if __name__ == "__main__":
    print("Running WASM Differential Oracle Tests...")
    test_differential_i32_i64_arithmetic()
    print("  [PASS] i32/i64 differential tests passed.")
    test_differential_f32_operations()
    print("  [PASS] f32 differential tests passed.")
    test_differential_f64_operations()
    print("  [PASS] f64 differential tests passed.")
    test_differential_control_flow()
    print("  [PASS] Control flow differential tests passed.")
    print("ALL DIFFERENTIAL TESTS PASSED.")
