import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

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

"""Integration Scenario 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation.

Tests:
- 2-bit Card Marking Hotspot tracking during loop execution
- Automatic trace extraction and queueing in RuntimeEngine
- COOS `idle_hook` batch JIT compilation
- Native x64 JIT trace execution from Active Cache Bank
- 100% Differential byte/value equality between pure Tier 2 and Tier 3 JIT
"""

import wasmtime
from interpreter import Interpreter
from runtime_engine import RuntimeEngine
from system import System
from wasi import WasiHostContext
from wasm_reader import parse
from x64_jit import TraceCompiler

SCENARIO4_WAT = """
(module
  ;; Sieve of Eratosthenes / Prime Counter up to limit N
  (memory (export "memory") 1)
  (func (export "count_primes") (param $limit i32) (result i32)
    (local $count i32)
    (local $i i32)
    (local $j i32)
    ;; Clear memory (first $limit bytes set to 1, representing prime candidates)
    (local.set $i (i32.const 2))
    (block $b_init_exit
      (loop $l_init
        (br_if $b_init_exit (i32.ge_s (local.get $i) (local.get $limit)))
        (i32.store8 (local.get $i) (i32.const 1))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_init)
      )
    )
    ;; Sieve loop
    (local.set $i (i32.const 2))
    (block $b_sieve_exit
      (loop $l_sieve
        (br_if $b_sieve_exit (i32.ge_s (i32.mul (local.get $i) (local.get $i)) (local.get $limit)))
        (if (i32.eq (i32.load8_u (local.get $i)) (i32.const 1))
          (then
            (local.set $j (i32.mul (local.get $i) (local.get $i)))
            (block $b_mark_exit
              (loop $l_mark
                (br_if $b_mark_exit (i32.ge_s (local.get $j) (local.get $limit)))
                (i32.store8 (local.get $j) (i32.const 0))
                (local.set $j (i32.add (local.get $j) (local.get $i)))
                (br $l_mark)
              )
            )
          )
        )
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_sieve)
      )
    )
    ;; Count primes
    (local.set $count (i32.const 0))
    (local.set $i (i32.const 2))
    (block $b_count_exit
      (loop $l_count
        (br_if $b_count_exit (i32.ge_s (local.get $i) (local.get $limit)))
        (if (i32.eq (i32.load8_u (local.get $i)) (i32.const 1))
          (then (local.set $count (i32.add (local.get $count) (i32.const 1))))
        )
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_count)
      )
    )
    (local.get $count)
  )
)
"""


def test_scenario_hybrid_jit():
    print("[*] Running Scenario 4: Hybrid JIT Compilation & Differential Check...")
    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO4_WAT))
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("count_primes")
    LIMIT = 1000  # Primes below 1000 is known to be exactly 168
    # 1. Tier 2 Reference Execution
    sysv_t2 = System()
    wasi_t2 = WasiHostContext(sysv_t2)
    funcs_t2 = wasi_t2.build_interpreter_host_functions(module)
    interp_t2 = Interpreter(module, memory=wasi_t2.guest_memory, host_functions=funcs_t2)
    res_t2 = interp_t2.call(fn_idx, [LIMIT])
    assert res_t2 == [168], f"Tier 2 prime count mismatch: expected 168, got {res_t2}"
    # 2. Tier 3 Hybrid Execution with Card Marking and idle_hook JIT
    sysv_t3 = System()
    wasi_t3 = WasiHostContext(sysv_t3)
    funcs_t3 = wasi_t3.build_interpreter_host_functions(module)
    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(jit_compiler=trace_compiler, yield_threshold=16)
    runtime_engine.register_module_blocks(module)
    interp_t3 = Interpreter(module, memory=wasi_t3.guest_memory, host_functions=funcs_t3)
    res_t3 = runtime_engine.run(interp_t3, fn_idx, [LIMIT], quantum=32)

    assert res_t3 == [168], f"Tier 3 prime count mismatch: expected 168, got {res_t3}"
    assert res_t2 == res_t3, "Tier 2 and Tier 3 calculation diverged!"
    assert len(runtime_engine.cache.active.traces) > 0, "No JIT traces were compiled"
    print(
        f"    [PASS] Scenario 4 (Hybrid JIT) verified with {len(runtime_engine.cache.active.traces)} hot JIT traces."
    )


if __name__ == "__main__":
    test_scenario_hybrid_jit()
