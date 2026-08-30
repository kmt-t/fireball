import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

"""Integration Scenario 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch.

Tests:
- Deep recursive stack unwinding with UnifiedStack CallFrames
- `call_indirect` dispatch via WASM function tables
- Multi-label branch dispatch via `br_table`
"""

import wasmtime
from interpreter import Interpreter
from system import System
from wasi import WasiHostContext
from wasm_reader import parse

SCENARIO3_WAT = """
(module
  ;; Function Table with 4 arithmetic operations
  (table 4 funcref)
  (elem (i32.const 0) $op_add $op_sub $op_mul $op_xor)
  (type $binop_t (func (param i32 i32) (result i32)))
  (func $op_add (param $a i32) (param $b i32) (result i32)
    (i32.add (local.get $a) (local.get $b))
  )
  (func $op_sub (param $a i32) (param $b i32) (result i32)
    (i32.sub (local.get $a) (local.get $b))
  )
  (func $op_mul (param $a i32) (param $b i32) (result i32)
    (i32.mul (local.get $a) (local.get $b))
  )
  (func $op_xor (param $a i32) (param $b i32) (result i32)
    (i32.xor (local.get $a) (local.get $b))
  )
  ;; Indirect call dispatch: op_id (0:add, 1:sub, 2:mul, 3:xor)
  (func (export "dispatch_calc") (param $op_id i32) (param $a i32) (param $b i32) (result i32)
    (call_indirect (type $binop_t) (local.get $a) (local.get $b) (local.get $op_id))
  )
  ;; Deep recursive Fibonacci calculation: fib(n) = fib(n-1) + fib(n-2)
  (func $fib (export "fib") (param $n i32) (result i32)
    (if (i32.le_s (local.get $n) (i32.const 1))
      (then (return (local.get $n)))
    )
    (i32.add
      (call $fib (i32.sub (local.get $n) (i32.const 1)))
      (call $fib (i32.sub (local.get $n) (i32.const 2)))
    )
  )
  ;; Multi-way switch via br_table
  (func (export "test_br_table") (param $selector i32) (result i32)
    (block $b_default
      (block $b_2
        (block $b_1
          (block $b_0
            (br_table $b_0 $b_1 $b_2 $b_default (local.get $selector))
          )
          (return (i32.const 100))
        )
        (return (i32.const 200))
      )
      (return (i32.const 300))
    )
    (i32.const 999)
  )
)
"""


def test_scenario_recursion_and_tables():
    print("[*] Running Scenario 3: Recursion & Indirect Table Dispatch...")
    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO3_WAT))
    module = parse(wasm_bytes)
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(
        module, memory=wasi_ctx.guest_memory, host_functions=host_funcs
    )
    # 1. Test Recursive Fibonacci (fib(12) = 144)
    fn_fib = module.export_func_index("fib")
    res_fib = interp.call(fn_fib, [12])
    assert res_fib == [144], f"Fibonacci(12) expected 144, got {res_fib}"
    # 2. Test Indirect Call Dispatch via Table
    fn_dispatch = module.export_func_index("dispatch_calc")
    assert interp.call(fn_dispatch, [0, 40, 2]) == [42], "call_indirect (add) failed"
    assert interp.call(fn_dispatch, [1, 50, 8]) == [42], "call_indirect (sub) failed"
    assert interp.call(fn_dispatch, [2, 6, 7]) == [42], "call_indirect (mul) failed"
    assert interp.call(fn_dispatch, [3, 0x55, 0x7F]) == [0x2A], (
        "call_indirect (xor) failed"
    )
    # 3. Test br_table switch
    fn_br_table = module.export_func_index("test_br_table")
    assert interp.call(fn_br_table, [0]) == [100], "br_table case 0 failed"
    assert interp.call(fn_br_table, [1]) == [200], "br_table case 1 failed"
    assert interp.call(fn_br_table, [2]) == [300], "br_table case 2 failed"
    assert interp.call(fn_br_table, [99]) == [999], "br_table default failed"
    print("    [PASS] Scenario 3 (Recursion & Table Dispatch) succeeded seamlessly.")


if __name__ == "__main__":
    test_scenario_recursion_and_tables()
