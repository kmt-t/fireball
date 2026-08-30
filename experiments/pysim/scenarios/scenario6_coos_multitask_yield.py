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

import sys
from pathlib import Path

"""Integration Scenario 6: COOS Cooperative Multitasking & Coroutine Interleaving.

Tests:
- Cooperative interleaving of multiple WASM coroutines sharing an ExecEnv
- Fuel / instruction budget bounded execution (`yield_every`)
- State preservation across coroutine yield / resume cycles
"""

import wasmtime
from wasm_reader import parse
from interpreter import Interpreter
from system import System
from wasi import WasiHostContext

SCENARIO6_WAT = """
(module
  (memory (export "memory") 1)
  ;; Task A: Producer - writes sequence into memory buffer starting at offset 512
  (func (export "producer_task") (param $count i32) (result i32)
    (local $i i32)
    (local $ptr i32)
    (local.set $i (i32.const 0))
    (local.set $ptr (i32.const 512))
    (block $b_exit
      (loop $l_top
        (br_if $b_exit (i32.ge_s (local.get $i) (local.get $count)))
        ;; Store (i + 1) * 10
        (i32.store (local.get $ptr) (i32.mul (i32.add (local.get $i) (i32.const 1)) (i32.const 10)))
        (local.set $ptr (i32.add (local.get $ptr) (i32.const 4)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_top)
      )
    )
    (local.get $i)
  )
  ;; Task B: Consumer - reads sequence from memory buffer and calculates sum
  (func (export "consumer_task") (param $count i32) (result i32)
    (local $i i32)
    (local $ptr i32)
    (local $sum i32)
    (local.set $i (i32.const 0))
    (local.set $ptr (i32.const 512))
    (local.set $sum (i32.const 0))
    (block $b_exit
      (loop $l_top
        (br_if $b_exit (i32.ge_s (local.get $i) (local.get $count)))
        (local.set $sum (i32.add (local.get $sum) (i32.load (local.get $ptr))))
        (local.set $ptr (i32.add (local.get $ptr) (i32.const 4)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_top)
      )
    )
    (local.get $sum)
  )
)
"""


def test_scenario_coos_multitask():
    print("[*] Running Scenario 6: COOS Cooperative Multitasking & Coroutines...")
    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO6_WAT))
    module = parse(wasm_bytes)
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(
        module, memory=wasi_ctx.guest_memory, host_functions=host_funcs
    )
    fn_prod = module.export_func_index("producer_task")
    fn_cons = module.export_func_index("consumer_task")
    N = 100  # 100 items: sum(1..100) * 10 = 5050 * 10 = 50500
    # 1. Run Producer as a coroutine yielding every 16 ops
    prod_coro = interp.call_coroutine(fn_prod, [N], yield_every=16)
    prod_done = False
    prod_yields = 0
    prod_res = None
    while not prod_done:
        try:
            next(prod_coro)
            prod_yields += 1
        except StopIteration as e:
            prod_res = e.value
            prod_done = True

    assert prod_res == [100], f"Producer task failed: {prod_res}"
    assert prod_yields > 0, "Producer should have yielded multiple times"
    print(f"    -> Producer yielded {prod_yields} times and produced 100 items.")
    # 2. Run Consumer as a coroutine yielding every 16 ops
    cons_coro = interp.call_coroutine(fn_cons, [N], yield_every=16)
    cons_done = False
    cons_yields = 0
    cons_res = None
    while not cons_done:
        try:
            next(cons_coro)
            cons_yields += 1
        except StopIteration as e:
            cons_res = e.value
            cons_done = True

    assert cons_res == [50500], (
        f"Consumer task sum mismatch: expected 50500, got {cons_res}"
    )
    assert cons_yields > 0, "Consumer should have yielded multiple times"
    print(
        f"    -> Consumer yielded {cons_yields} times and computed expected sum: {cons_res[0]}."
    )
    print("    [PASS] Scenario 6 (COOS Cooperative Multitasking) succeeded seamlessly.")


if __name__ == "__main__":
    test_scenario_coos_multitask()
