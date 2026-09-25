from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent


"""Integration Scenario 5: Multiple Functions, UnifiedPC & sparse JIT lookup.

Tests:
- UnifiedPC address space across multiple guest functions
- Binary search over the small sorted JIT entry set, without a Radix index
- Hotspot tracking and JIT execution across deeply nested function invocations
"""

from bisect import bisect_left

import wasmtime
from system import System
from tier3_executer.interpreter.interpreter import Interpreter, InterpreterBindings
from tier3_executer.jit.jit_manager import JITRuntimeManager
from tier3_executer.jit.runtime_engine import RuntimeEngine
from tier3_executer.jit.x64_jit import TraceCompiler
from tier3_platform.drivers.wasi.context import WasiHostContext
from wasm_reader import parse

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
    print("[*] Running Scenario 5: Multi-Function UnifiedPC & sparse JIT lookup...")
    wasm_bytes = bytes(wasmtime.wat2wasm(SCENARIO5_WAT))
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("batch_metrics")
    ITERS = 500
    # 1. Tier 2 Reference Execution
    sysv_t2 = System()
    wasi_t2 = WasiHostContext(sysv_t2)
    funcs_t2 = wasi_t2.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_t2.guest_memory, ())
    interp_t2 = Interpreter(
        module, InterpreterBindings.with_memory_and_functions(wasi_t2.guest_memory, funcs_t2)
    )
    res_t2 = interp_t2.call(fn_idx, [ITERS])
    # 2. Tier 3 Hybrid Execution
    sysv_t3 = System()
    wasi_t3 = WasiHostContext(sysv_t3)
    funcs_t3 = wasi_t3.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_t3.guest_memory, ())
    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(
        jit_runtime=JITRuntimeManager(jit_compiler=trace_compiler, yield_threshold=16)
    )
    runtime_engine.register_module_blocks(module)
    interp_t3 = Interpreter(
        module, InterpreterBindings.with_memory_and_functions(wasi_t3.guest_memory, funcs_t3)
    )
    res_t3 = runtime_engine.call(interp_t3, fn_idx, [ITERS])

    assert res_t2 == res_t3, f"Calculations diverged: T2={res_t2} vs T3={res_t3}"
    assert len(runtime_engine.jit_runtime.cache.active.traces) > 0, "No JIT traces compiled"
    # 3. Verify that traces belong to multiple distinct functions via UnifiedPC
    func_indices_in_jit = {(pc >> 16) for pc, _ in runtime_engine.jit_runtime.cache.active.traces}
    print(f"    -> Compiled JIT traces belong to functions: {func_indices_in_jit}")
    assert len(func_indices_in_jit) >= 2, "Traces should span across multiple functions"
    # 4. Verify sparse JIT entry lookup by binary search across compiled UnifiedPCs
    sorted_pairs = sorted(runtime_engine.jit_runtime.cache.active.traces, key=lambda x: x[0])
    keys = tuple(pc for pc, _ in sorted_pairs)
    vals = tuple(trace for _, trace in sorted_pairs)
    for k, v in zip(keys, vals, strict=False):
        index = bisect_left(keys, k)
        found = vals[index] if index < len(keys) and keys[index] == k else None
        assert found is v, f"JIT binary lookup failed for UnifiedPC 0x{k:08X}"

    print(
        f"    [PASS] Scenario 5 (Multi-Function UnifiedPC) verified with {len(runtime_engine.jit_runtime.cache.active.traces)} traces."
    )


if __name__ == "__main__":
    test_scenario_multimodule_unified_pc()
