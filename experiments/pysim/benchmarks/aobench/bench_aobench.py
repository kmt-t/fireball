"""
experiments/pysim/benchmarks/aobench/bench_aobench.py
3D Raytracing Ambient Occlusion Benchmark (AO-Bench).
Conforms to docs/components/tier3_jit/benchmarks/aobench_spec.md (BENCH-AO-01 ~ BENCH-AO-04).
"""

from __future__ import annotations

import sys
import time
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

from interpreter import Interpreter
from runtime_engine import RuntimeEngine
from system import System
from wasi import WasiHostContext
from wasm_reader import parse
from x64_jit import TraceCompiler


def run_aobench() -> dict[str, float]:
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
    funcs = wasi_ctx.build_interpreter_host_functions(module)
    interp = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=funcs)
    main_fn = module.export_func_index("main")

    t0_t2 = time.perf_counter()
    interp.call(main_fn, [WIDTH, HEIGHT])
    t1_t2 = time.perf_counter()
    render_output = sysv.transport.drain().decode("utf-8", errors="replace")
    t2_time_ms = (t1_t2 - t0_t2) * 1000
    hit_pixels = sum(1 for ch in render_output if ch in (".", ":", "+", "#", "@"))
    total_rays = (WIDTH * HEIGHT) + (hit_pixels * AO_SAMPLES)
    t2_rays_per_sec = total_rays / (t2_time_ms / 1000.0) if t2_time_ms > 0 else 0

    # 2. Tier 3 JIT Hybrid Execution
    sysv_t3 = System()
    wasi_ctx_t3 = WasiHostContext(sysv_t3)
    funcs_t3 = wasi_ctx_t3.build_interpreter_host_functions(module)
    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(jit_compiler=trace_compiler, yield_threshold=16)
    runtime_engine.register_module_blocks(module)
    interp_t3 = Interpreter(module, memory=wasi_ctx_t3.guest_memory, host_functions=funcs_t3)

    t0_t3 = time.perf_counter()
    runtime_engine.run(interp_t3, main_fn, [WIDTH, HEIGHT], quantum=16)
    t1_t3 = time.perf_counter()
    render_output_t3 = sysv_t3.transport.drain().decode("utf-8", errors="replace")
    t3_time_ms = (t1_t3 - t0_t3) * 1000
    t3_rays_per_sec = total_rays / (t3_time_ms / 1000.0) if t3_time_ms > 0 else 0
    speedup_ratio = t2_time_ms / t3_time_ms if t3_time_ms > 0 else 1.0

    # Differential Check
    assert render_output == render_output_t3, "Tier 3 output diverged from Tier 2!"

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "total_rays": total_rays,
        "hit_pixels": hit_pixels,
        "t2_time_ms": t2_time_ms,
        "t3_time_ms": t3_time_ms,
        "t2_rays_per_sec": t2_rays_per_sec,
        "t3_rays_per_sec": t3_rays_per_sec,
        "speedup_ratio": speedup_ratio,
        "compiled_traces": len(runtime_engine.cache.active.traces),
    }


def main():
    print("=" * 80)
    print("      [Benchmark 4/4] 3D Ambient Occlusion Raytracing (AO-Bench)      ")
    print("=" * 80)
    res = run_aobench()
    print(
        f"  * Resolution:               {res['width']} x {res['height']} ({res['total_rays']:,} total rays)"
    )
    print(
        f"  * Tier 2 (Threaded CPS):    {res['t2_time_ms']:.2f} ms  ({res['t2_rays_per_sec']:,.0f} Rays / Sec)"
    )
    print(
        f"  * Tier 3 (Hybrid + JIT):    {res['t3_time_ms']:.2f} ms  ({res['t3_rays_per_sec']:,.0f} Rays / Sec)"
    )
    print(f"  * Measured Speedup:         {res['speedup_ratio']:.2f}x faster")
    print(f"  * Active JIT Traces:        {res['compiled_traces']} compiled traces")
    print("=" * 80)
    print("[PASS] 3D Ambient Occlusion benchmark completed successfully.")


if __name__ == "__main__":
    main()
