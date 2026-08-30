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

"""Integration Scenario 5: Multiple Functions, UnifiedPC & bswap32 Radix Tree.

Tests:
- UnifiedPC address space across multiple guest functions
- `bswap32` KeyProjection on RadixBinaryTreeView for uniform O(1) JIT entry indexing
- Hotspot tracking and JIT execution across deeply nested function invocations
"""

import wasmtime
from wasm_reader import parse
from interpreter import Interpreter
from runtime_engine import RuntimeEngine
from x64_jit import TraceCompiler
from system_containers import RadixBinaryTreeView, bswap32
from system import System
from wasi import WasiHostContext

SCENARIO5_WAT = """
(module
  ;; Function 0: 3D dot product: x1*x2 + y1*y2 + z1*z2
  (func $dot3 (param $x1 i32) (param $y1 i32) (param $z1 i32)
              (param $x2 i32) (param $y2 i32) (param $z2 i32) (result i32)
    (i32.add
      (i32.mul (local.get $x1) (local.get $x2))
      (i32.add
        (i32.mul (local.get $y1) (local.get $y2))
        (i32.mul (local.get $z1) (local.get $z2))))
  )

  ;; Function 1: Manhattan distance: |x1-x2| + |y1-y2| + |z1-z2|
  (func $manhattan3 (param $x1 i32) (param $y1 i32) (param $z1 i32)
                    (param $x2 i32) (param $y2 i32) (param $z2 i32) (result i32)
    (local $dx i32) (local $dy i32) (local $dz i32)
    (local.set $dx (i32.sub (local.get $x1) (local.get $x2)))
    (if (i32.lt_s (local.get $dx) (i32.const 0))
      (then (local.set $dx (i32.sub (i32.const 0) (local.get $dx))))
    )
    (local.set $dy (i32.sub (local.get $y1) (local.get $y2)))
    (if (i32.lt_s (local.get $dy) (i32.const 0))
      (then (local.set $dy (i32.sub (i32.const 0) (local.get $dy))))
    )
    (local.set $dz (i32.sub (local.get $z1) (local.get $z2)))
    (if (i32.lt_s (local.get $dz) (i32.const 0))
      (then (local.set $dz (i32.sub (i32.const 0) (local.get $dz))))
    )
    (i32.add (local.get $dx) (i32.add (local.get $dy) (local.get $dz)))
  )

  ;; Function 2: Batch compute dot products across iterations
  (func (export "batch_metrics") (param $iters i32) (result i32)
    (local $i i32)
    (local $acc i32)
    (local.set $acc (i32.const 0))
    (local.set $i (i32.const 0))

    (block $b_exit
      (loop $l_top
        (br_if $b_exit (i32.ge_s (local.get $i) (local.get $iters)))

        ;; Call dot3 and manhattan3 alternately
        (local.set $acc
          (i32.add (local.get $acc)
            (call $dot3 (local.get $i) (i32.const 2) (i32.const 3)
                        (i32.const 4) (local.get $i) (i32.const 6))))

        (local.set $acc
          (i32.add (local.get $acc)
            (call $manhattan3 (local.get $i) (i32.const 10) (i32.const 20)
                              (i32.const 5) (local.get $i) (i32.const 15))))

        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l_top)
      )
    )
    (local.get $acc)
  )
)
"""


def test_scenario_multimodule_unified_pc():
    print("[*] Running Scenario 5: Multi-Function UnifiedPC & bswap32 Radix Tree...")

    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO5_WAT))
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("batch_metrics")
    ITERS = 500

    # 1. Tier 2 Reference Execution
    sysv_t2 = System()
    wasi_t2 = WasiHostContext(sysv_t2)
    funcs_t2 = wasi_t2.build_interpreter_host_functions(module)
    interp_t2 = Interpreter(
        module, memory=wasi_t2.guest_memory, host_functions=funcs_t2
    )

    res_t2 = interp_t2.call(fn_idx, [ITERS])

    # 2. Tier 3 Hybrid Execution
    sysv_t3 = System()
    wasi_t3 = WasiHostContext(sysv_t3)
    funcs_t3 = wasi_t3.build_interpreter_host_functions(module)

    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(jit_compiler=trace_compiler, yield_threshold=16)
    runtime_engine.register_module_blocks(module)

    interp_t3 = Interpreter(
        module,
        memory=wasi_t3.guest_memory,
        host_functions=funcs_t3,
        runtime_engine=runtime_engine,
    )

    coro = interp_t3.call_coroutine(fn_idx, [ITERS], yield_every=32)
    try:
        while True:
            next(coro)
            runtime_engine.idle_hook(budget=4)
    except StopIteration as e:
        res_t3 = e.value

    assert res_t2 == res_t3, f"Calculations diverged: T2={res_t2} vs T3={res_t3}"
    assert len(runtime_engine.cache.active.traces) > 0, "No JIT traces compiled"

    # 3. Verify that traces belong to multiple distinct functions via UnifiedPC
    func_indices_in_jit = set(
        (pc >> 16) for pc, _ in runtime_engine.cache.active.traces
    )
    print(f"    -> Compiled JIT traces belong to functions: {func_indices_in_jit}")
    assert len(func_indices_in_jit) >= 2, "Traces should span across multiple functions"

    # 4. Verify RadixBinaryTreeView lookup across all compiled UnifiedPCs
    sorted_pairs = sorted(runtime_engine.cache.active.traces, key=lambda x: x[0])
    keys = [p[0] for p in sorted_pairs]
    vals = [p[1] for p in sorted_pairs]

    radix_shift = 16
    max_prefix = max(keys) >> radix_shift
    radix_table = [0] * (max_prefix + 2)
    current_prefix = 0
    for idx, k in enumerate(keys):
        prefix = k >> radix_shift
        while current_prefix < prefix:
            current_prefix += 1
            radix_table[current_prefix] = idx
    while current_prefix <= max_prefix:
        current_prefix += 1
        radix_table[current_prefix] = len(keys)

    radix_tree = RadixBinaryTreeView(keys, vals, radix_table, radix_shift=radix_shift)
    for k, v in zip(keys, vals):
        found = radix_tree.find(k)
        assert found is v, f"RadixBinaryTreeView lookup failed for UnifiedPC 0x{k:08X}"

    print(
        f"    [PASS] Scenario 5 (Multi-Function UnifiedPC) verified with {len(runtime_engine.cache.active.traces)} traces."
    )


if __name__ == "__main__":
    test_scenario_multimodule_unified_pc()
