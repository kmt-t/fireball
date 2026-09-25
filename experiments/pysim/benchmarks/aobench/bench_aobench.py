"""
experiments/pysim/benchmarks/aobench/bench_aobench.py
3D Raytracing Ambient Occlusion Benchmark (AO-Bench).
Conforms to docs/components/tier3_executer/benchmarks/aobench_spec.md (BENCHMARK-AO-01 ~ BENCHMARK-AO-04).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from system import System
from tier3_executer.interpreter.interpreter import (
    NATIVE_RUNTIME_PROFILE_STATS_ENABLED,
    Interpreter,
    InterpreterBindings,
)
from tier3_executer.jit.jit_manager import JITRuntimeManager
from tier3_executer.jit.runtime_engine import RuntimeEngine
from tier3_executer.jit.x64_jit import TraceCompiler
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.wasi.context import WasiHostContext
from wasm_reader import parse


def run_aobench(debug: bool = False) -> dict[str, int | float]:
    WIDTH = 32
    HEIGHT = 16
    AO_SAMPLES = 4

    wasm_path = Path(_PYSIM_DIR) / "aobench.wasm"
    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()

    module = parse(wasm_bytes)

    # 1. Tier 2 Reference Execution
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    sysv.start_hal_driver(DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport))
    funcs = wasi_ctx.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_ctx.guest_memory, ())
    interp = Interpreter(
        module, InterpreterBindings.with_memory_and_functions(wasi_ctx.guest_memory, funcs)
    )
    main_fn = module.export_func_index("main")

    t0_t2 = time.perf_counter()
    interp.call(main_fn, [WIDTH, HEIGHT])
    t1_t2 = time.perf_counter()
    render_output = sysv.transport.drain_output().decode("utf-8", errors="replace")
    t2_time_ms = (t1_t2 - t0_t2) * 1000
    hit_pixels = sum(1 for ch in render_output if ch in (".", ":", "+", "#", "@"))
    total_rays = (WIDTH * HEIGHT) + (hit_pixels * AO_SAMPLES)
    t2_rays_per_sec = total_rays / (t2_time_ms / 1000.0) if t2_time_ms > 0 else 0

    # 2. Tier 3 JIT Hybrid Execution
    sysv_t3 = System()
    wasi_ctx_t3 = WasiHostContext(sysv_t3)
    sysv_t3.start_hal_driver(
        DummyDriver(sysv_t3.wasi_hal_bindings.stdout_uri, transport=sysv_t3.transport)
    )
    funcs_t3 = wasi_ctx_t3.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_ctx_t3.guest_memory, ())
    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(
        jit_runtime=JITRuntimeManager(jit_compiler=trace_compiler),
        debug=debug,
    )
    runtime_engine.register_module_blocks(module)
    interp_t3 = Interpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi_ctx_t3.guest_memory, funcs_t3),
    )

    t0_t3 = time.perf_counter()
    runtime_engine.call(interp_t3, main_fn, [WIDTH, HEIGHT])
    t1_t3 = time.perf_counter()
    render_output_t3 = sysv_t3.transport.drain_output().decode("utf-8", errors="replace")
    t3_time_ms = (t1_t3 - t0_t3) * 1000
    t3_rays_per_sec = total_rays / (t3_time_ms / 1000.0) if t3_time_ms > 0 else 0
    speedup_ratio = t2_time_ms / t3_time_ms if t3_time_ms > 0 else 1.0

    # Differential Check
    assert render_output == render_output_t3, "Tier 3 output diverged from Tier 2!"

    # Collect diagnostic path counters after the timed run so profiling code
    # does not contribute to the reported execution time.
    if NATIVE_RUNTIME_PROFILE_STATS_ENABLED:
        runtime_engine.collect_runtime_stats = True
        runtime_engine.reset_stats()
        runtime_engine.call(interp_t3, main_fn, [WIDTH, HEIGHT])
        diagnostic_output = sysv_t3.transport.drain_output().decode("utf-8", errors="replace")
        assert diagnostic_output == render_output_t3

    result: dict[str, int | float] = {
        "width": WIDTH,
        "height": HEIGHT,
        "total_rays": total_rays,
        "hit_pixels": hit_pixels,
        "t2_time_ms": t2_time_ms,
        "t3_time_ms": t3_time_ms,
        "t2_rays_per_sec": t2_rays_per_sec,
        "t3_rays_per_sec": t3_rays_per_sec,
        "speedup_ratio": speedup_ratio,
        "runtime_profile_stats_enabled": int(NATIVE_RUNTIME_PROFILE_STATS_ENABLED),
        "compiled_traces": len(runtime_engine.jit_runtime.cache.active.traces),
    }
    if NATIVE_RUNTIME_PROFILE_STATS_ENABLED:
        result.update(
            {
                "interp_blocks": runtime_engine.stat_interp_steps,
                "jit_invocations": runtime_engine.stat_jit_invocations,
                "native_dispatch_trace_transitions": (
                    runtime_engine.stat_native_dispatch_trace_transitions
                ),
                "trace_exits_to_interp": runtime_engine.stat_trace_exits_to_interp,
            }
        )
    return result


def main():
    debug = "--debug" in sys.argv
    print("=" * 80)
    print("      [Benchmark 4/4] 3D Ambient Occlusion Raytracing (AO-Bench)      ")
    print("=" * 80)
    res = run_aobench(debug=debug)
    print(
        f"  * Resolution:               {res['width']} x {res['height']} ({res['total_rays']:,} total rays)"
    )
    print(
        f"  * Tier 3 Interpreter (Threaded CPS): {res['t2_time_ms']:.2f} ms  ({res['t2_rays_per_sec']:,.0f} Rays / Sec)"
    )
    print(
        f"  * Tier 3 (Hybrid + JIT):    {res['t3_time_ms']:.2f} ms  ({res['t3_rays_per_sec']:,.0f} Rays / Sec)"
    )
    print(f"  * Measured Speedup:         {res['speedup_ratio']:.2f}x faster")
    if res["runtime_profile_stats_enabled"]:
        print(
            f"  * JIT trace transitions: {res['native_dispatch_trace_transitions']:,}"
        )
    else:
        print("  * JIT trace transitions: runtime stats compiled out")
    print(f"  * Active JIT Traces:        {res['compiled_traces']} compiled traces")
    print("=" * 80)
    print("[PASS] 3D Ambient Occlusion benchmark completed successfully.")


if __name__ == "__main__":
    main()
