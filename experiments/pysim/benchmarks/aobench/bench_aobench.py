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
    Interpreter,
    InterpreterBindings,
    NativeInterpreter,
)
from tier3_executer.jit.jit_manager import JITRuntimeManager
from tier3_executer.jit.jit_runtime import JITInterpreter
from tier3_executer.jit.x64_jit import TraceCompiler
from tier3_executer.runtime_engine import RuntimeEngine
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

    main_fn = module.export_func_index("main")

    # Keep the Python reference and strict C++ interpreter timings distinct.
    sysv_python = System()
    wasi_python = WasiHostContext(sysv_python)
    sysv_python.start_hal_driver(
        DummyDriver(transport=sysv_python.transport), sysv_python.wasi_hal_bindings.stdout_uri
    )
    funcs_python = wasi_python.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_python.guest_memory, ())
    interp_python = Interpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi_python.guest_memory, funcs_python),
    )
    t0_python = time.perf_counter()
    interp_python.call(main_fn, [WIDTH, HEIGHT])
    t1_python = time.perf_counter()
    python_output = sysv_python.transport.drain_output().decode("utf-8", errors="replace")
    python_time_ms = (t1_python - t0_python) * 1000

    sysv_native = System()
    wasi_native = WasiHostContext(sysv_native)
    sysv_native.start_hal_driver(
        DummyDriver(transport=sysv_native.transport), sysv_native.wasi_hal_bindings.stdout_uri
    )
    funcs_native = wasi_native.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_native.guest_memory, ())
    interp_native = NativeInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi_native.guest_memory, funcs_native),
    )
    t0_native = time.perf_counter()
    interp_native.call(main_fn, [WIDTH, HEIGHT])
    t1_native = time.perf_counter()
    native_output = sysv_native.transport.drain_output().decode("utf-8", errors="replace")
    native_time_ms = (t1_native - t0_native) * 1000

    hit_pixels = sum(1 for ch in native_output if ch in (".", ":", "+", "#", "@"))
    total_rays = (WIDTH * HEIGHT) + (hit_pixels * AO_SAMPLES)

    # 2. Tier 3 JIT Hybrid Execution
    sysv_t3 = System()
    wasi_ctx_t3 = WasiHostContext(sysv_t3)
    sysv_t3.start_hal_driver(
        DummyDriver(transport=sysv_t3.transport), sysv_t3.wasi_hal_bindings.stdout_uri
    )
    funcs_t3 = wasi_ctx_t3.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_ctx_t3.guest_memory, ())
    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(
        jit_runtime=JITRuntimeManager(jit_compiler=trace_compiler),
        debug=debug,
    )
    runtime_engine.register_module_blocks(module)
    interp_t3 = JITInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi_ctx_t3.guest_memory, funcs_t3),
        runtime_engine,
    )

    t0_t3 = time.perf_counter()
    runtime_engine.call(interp_t3, main_fn, [WIDTH, HEIGHT])
    t1_t3 = time.perf_counter()
    render_output_t3 = sysv_t3.transport.drain_output().decode("utf-8", errors="replace")
    t3_time_ms = (t1_t3 - t0_t3) * 1000
    t3_rays_per_sec = total_rays / (t3_time_ms / 1000.0) if t3_time_ms > 0 else 0
    speedup_ratio = native_time_ms / t3_time_ms if t3_time_ms > 0 else 1.0

    # Differential Check
    assert python_output == native_output == render_output_t3, (
        "Python, C++ interpreter, and hybrid JIT outputs must match byte-for-byte: "
        f"lengths={len(python_output)}/{len(native_output)}/{len(render_output_t3)}, "
        f"prefixes={python_output[:80]!r}/{native_output[:80]!r}/{render_output_t3[:80]!r}"
    )
    output_bytes = len(native_output.encode("utf-8"))
    assert output_bytes == (WIDTH + 1) * HEIGHT

    # Collect diagnostic path counters after the timed run so profiling code
    # does not contribute to the reported execution time.
    diagnostic_engine = RuntimeEngine(
        jit_runtime=JITRuntimeManager(jit_compiler=TraceCompiler()),
        debug=debug,
        collect_runtime_stats=True,
    )
    diagnostic_engine.register_module_blocks(module)
    sysv_diagnostic = System()
    wasi_diagnostic = WasiHostContext(sysv_diagnostic)
    sysv_diagnostic.start_hal_driver(
        DummyDriver(transport=sysv_diagnostic.transport),
        sysv_diagnostic.wasi_hal_bindings.stdout_uri,
    )
    funcs_diagnostic = wasi_diagnostic.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_diagnostic.guest_memory, ())
    interp_diagnostic = JITInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(
            wasi_diagnostic.guest_memory, funcs_diagnostic
        ),
        diagnostic_engine,
    )
    interp_diagnostic.call(main_fn, [WIDTH, HEIGHT])
    diagnostic_output = sysv_diagnostic.transport.drain_output().decode("utf-8", errors="replace")
    assert diagnostic_output == render_output_t3

    result: dict[str, int | float] = {
        "width": WIDTH,
        "height": HEIGHT,
        "total_rays": total_rays,
        "hit_pixels": hit_pixels,
        "output_bytes": output_bytes,
        "python_interpreter_time_ms": python_time_ms,
        "native_interpreter_time_ms": native_time_ms,
        "t2_time_ms": native_time_ms,
        "t3_time_ms": t3_time_ms,
        "python_interpreter_rays_per_sec": (
            total_rays / (python_time_ms / 1000.0) if python_time_ms > 0 else 0
        ),
        "native_interpreter_rays_per_sec": (
            total_rays / (native_time_ms / 1000.0) if native_time_ms > 0 else 0
        ),
        "t2_rays_per_sec": (total_rays / (native_time_ms / 1000.0) if native_time_ms > 0 else 0),
        "t3_rays_per_sec": t3_rays_per_sec,
        "speedup_ratio": speedup_ratio,
        "runtime_profile_stats_enabled": 1,
        "compiled_traces": len(runtime_engine.jit_runtime.cache.active.traces),
    }
    result.update(
        {
            "interp_blocks": diagnostic_engine.stat_interp_steps,
            "jit_invocations": diagnostic_engine.stat_jit_invocations,
            "native_dispatch_trace_transitions": (
                diagnostic_engine.stat_native_dispatch_trace_transitions
            ),
            "trace_exits_to_interp": diagnostic_engine.stat_trace_exits_to_interp,
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
    print(f"  * Python reference interpreter: {res['python_interpreter_time_ms']:.2f} ms")
    print(
        f"  * C++ threaded interpreter:    {res['native_interpreter_time_ms']:.2f} ms  "
        f"({res['native_interpreter_rays_per_sec']:,.0f} Rays / Sec)"
    )
    print(
        f"  * Tier 3 (Hybrid + JIT):    {res['t3_time_ms']:.2f} ms  ({res['t3_rays_per_sec']:,.0f} Rays / Sec)"
    )
    if res["speedup_ratio"] >= 1.0:
        print(f"  * JIT vs C++ interpreter:   {res['speedup_ratio']:.2f}x faster")
    else:
        print(f"  * JIT vs C++ interpreter:   {1.0 / res['speedup_ratio']:.2f}x slower")
    if res["runtime_profile_stats_enabled"]:
        print(f"  * JIT trace transitions: {res['native_dispatch_trace_transitions']:,}")
    else:
        print("  * JIT trace transitions: runtime stats not collected")
    print(f"  * Active JIT Traces:        {res['compiled_traces']} compiled traces")
    print("=" * 80)
    print("[PASS] 3D Ambient Occlusion benchmark completed successfully.")


if __name__ == "__main__":
    main()
