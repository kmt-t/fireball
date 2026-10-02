from __future__ import annotations

"""
experiments/pysim/qa/tier2_runtime/test_wasm_differential.py
Differential Oracle Testing: pysim WASM Interpreter vs wasmtime engine.
Validates arithmetic, bitwise, floating-point (f32/f64), control flow, and memory
operations between Fireball's interpreter and wasmtime (WebAssembly Reference).
"""

import math
import struct
from pathlib import Path

import pytest
import wasmtime

# Setup search paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from helpers import make_interpreter as Interpreter
from helpers import wat_to_wasm
from tier3_executer.interpreter.interpreter import TrapCode
from wasm_reader import parse


def _run_differential(
    wat_text: str,
    func_name: str,
    args: list[int | float],
    expected_trap: TrapCode | None = None,
    memory_export: str | None = None,
) -> None:
    """Compare successful values or the declared guest trap, and optional full memory state."""

    wasm_bytes = wat_to_wasm(wat_text)
    assert wasm_bytes, "wasmtime.wat2wasm must succeed in differential test environment"

    # 1. Execute with wasmtime
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    module = wasmtime.Module(engine, wasm_bytes)
    instance = wasmtime.Instance(store, module, [])
    wt_func = instance.exports(store)[func_name]
    assert isinstance(wt_func, wasmtime.Func)

    wt_trap_code = None
    wt_result = None
    try:
        wt_result = wt_func(store, *args)
    except wasmtime.Trap as trap:
        wt_trap_code = trap.trap_code
        assert wt_trap_code is not None, "reference engine raised an unclassified trap"

    # 2. Execute with pysim Interpreter
    pysim_mod = parse(wasm_bytes)
    pysim_interp = Interpreter(pysim_mod)
    func_idx = pysim_mod.export_func_index(func_name)

    call_state = pysim_interp.start(func_idx, args)
    while not call_state.finished:
        call_state = pysim_interp.step(call_state)

    # 3. Compare externally visible memory, including partial-write rejection.
    if memory_export is not None:
        wt_memory = instance.exports(store)[memory_export]
        assert isinstance(wt_memory, wasmtime.Memory)
        assert bytes(pysim_interp.memory) == bytes(wt_memory.read(store)), (
            f"Memory mismatch after {func_name}{args}"
        )

    # 4. Guest traps are structured outcomes. Unrelated Python failures propagate.
    if expected_trap is not None:
        reference_codes = {
            TrapCode.UNREACHABLE: wasmtime.TrapCode.UNREACHABLE,
            TrapCode.INTEGER_DIVIDE_BY_ZERO: wasmtime.TrapCode.INTEGER_DIVISION_BY_ZERO,
            TrapCode.INTEGER_OVERFLOW: wasmtime.TrapCode.INTEGER_OVERFLOW,
            TrapCode.MEMORY_OUT_OF_BOUNDS: wasmtime.TrapCode.MEMORY_OUT_OF_BOUNDS,
            TrapCode.INVALID_CONVERSION: wasmtime.TrapCode.BAD_CONVERSION_TO_INTEGER,
        }
        assert wt_trap_code == reference_codes[expected_trap], (
            f"Wrong reference trap for {func_name}{args}: {wt_trap_code}, expected {expected_trap}"
        )
        assert call_state.trap is not None, f"Expected guest trap for {func_name}{args}"
        assert call_state.trap.code == expected_trap, (
            f"Wrong pysim trap for {func_name}{args}: {call_state.trap.code}, expected {expected_trap}"
        )
        assert call_state.results is None, "trapping call must not publish a successful result"
        return

    assert wt_trap_code is None, f"Unexpected reference trap for {func_name}{args}: {wt_trap_code}"
    assert call_state.trap is None, (
        f"Unexpected pysim trap for {func_name}{args}: {call_state.trap}"
    )
    assert call_state.results is not None
    assert len(call_state.results) == 1, "these fixtures declare exactly one result"
    pysim_result = call_state.results[0]
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
    _run_differential(wat, "div_s32", [100, 0], expected_trap=TrapCode.INTEGER_DIVIDE_BY_ZERO)
    _run_differential(wat, "div_u32", [0xFFFFFFFF, 2])
    _run_differential(wat, "rem_s32", [-105, 10])
    _run_differential(wat, "rotl32", [0x12345678, 4])
    _run_differential(wat, "clz32", [0x000F0000])
    _run_differential(wat, "popcnt32", [0x12345678])
    _run_differential(wat, "add64", [0x100000000, 0x200000000])


@pytest.mark.parametrize("value_type", ["i32", "i64"])
@pytest.mark.parametrize("opcode", ["div_s", "div_u", "rem_s", "rem_u"])
def test_differential_integer_zero_divisor_trap(value_type: str, opcode: str):
    """TEST-WASM-54: All eight integer division/remainder forms report divide-by-zero."""
    wat = f"""
    (module
      (func (export "run") (param {value_type} {value_type}) (result {value_type})
        ({value_type}.{opcode} (local.get 0) (local.get 1))))
    """
    _run_differential(wat, "run", [100, 0], expected_trap=TrapCode.INTEGER_DIVIDE_BY_ZERO)


@pytest.mark.parametrize("value_type, minimum", [("i32", -(1 << 31)), ("i64", -(1 << 63))])
def test_differential_signed_division_overflow_trap(value_type: str, minimum: int):
    """TEST-WASM-57: Signed minimum divided by -1 reports overflow, not divide-by-zero."""
    wat = f"""
    (module
      (func (export "run") (param {value_type} {value_type}) (result {value_type})
        ({value_type}.div_s (local.get 0) (local.get 1))))
    """
    _run_differential(wat, "run", [minimum, -1], expected_trap=TrapCode.INTEGER_OVERFLOW)


@pytest.mark.parametrize(
    "instruction, expected_trap",
    [
        pytest.param("unreachable", TrapCode.UNREACHABLE, id="unreachable"),
        pytest.param(
            "(i32.trunc_f64_s (f64.const nan))", TrapCode.INVALID_CONVERSION, id="nan-to-integer"
        ),
    ],
)
def test_differential_declared_instruction_traps(instruction: str, expected_trap: TrapCode):
    """TEST-WASM-10/58: Semantic guest faults report their declared causes."""
    wat = f'(module (func (export "run") (result i32) {instruction}))'
    _run_differential(wat, "run", [], expected_trap=expected_trap)


@pytest.mark.parametrize("func_name", ["load", "store"])
@pytest.mark.parametrize("address", [65532, 65533, 65536, 0x7FFFFFFF])
def test_differential_memory_boundary_and_side_effects(func_name: str, address: int):
    """TEST-WASM-40/42/44: Last valid word succeeds; out-of-range accesses trap unchanged."""
    wat = """
    (module
      (memory (export "memory") 1)
      (func (export "load") (param i32) (result i32)
        (i32.load (local.get 0)))
      (func (export "store") (param i32) (result i32)
        (i32.store (local.get 0) (i32.const 0x12345678))
        (i32.const 7)))
    """
    expected_trap = None if address == 65532 else TrapCode.MEMORY_OUT_OF_BOUNDS
    _run_differential(wat, func_name, [address], expected_trap, memory_export="memory")


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
    raise SystemExit(pytest.main([__file__]))
