"""Measure the longest observed yield-boundary response for REQ-020 workloads."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(os.environ.get("FIREBALL_ROOT", Path(__file__).resolve().parents[6])).resolve()
_PYSIM = _ROOT / "experiments/pysim"
_BENCH = _PYSIM / "benchmarks"
for _path in (_ROOT, _PYSIM, _BENCH):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from _bootstrap import configure_import_paths, reserve_native_region

configure_import_paths(_PYSIM, _BENCH)

from config import FB_CONF_INTERPRETER_YIELD_THRESHOLD
from qa.shared.runtime_stats import RuntimeStatsEngine
from tier2_runtime.interpreter.interpreter import (
    InterpreterBindings,
    InterpreterCall,
    NativeInterpreter,
    WasmNumber,
)
from tier2_runtime.runtime.engine import RuntimeBoundaryResult
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse
from tier3_plugins.jit.jit_manager import JITRuntimeManager


class TimedRuntime(RuntimeStatsEngine):
    """Collect host-observed time around each scheduler boundary."""

    __slots__ = ("boundary_ns", "yield_ns")

    def __init__(self, manager: JITRuntimeManager, module: Module) -> None:
        super().__init__(
            jit_runtime=manager, collect_runtime_stats=False, bump_allocator=module.allocator
        )
        self.boundary_ns: list[int] = []
        self.yield_ns: list[int] = []

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        started = time.perf_counter_ns()
        result = super()._run_bound(interp, call_state, idle_budget)
        elapsed = time.perf_counter_ns() - started
        self.boundary_ns.append(elapsed)
        if result.yield_requested:
            self.yield_ns.append(elapsed)
        return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_call(
    engine: TimedRuntime,
    interpreter: NativeInterpreter,
    function_index: int,
    arguments: list[WasmNumber],
    idle_budget: int,
) -> None:
    engine.call(interpreter, function_index, arguments, idle_budget=idle_budget).unwrap()


def _new_runtime(module: Module) -> tuple[JITRuntimeManager, TimedRuntime, NativeInterpreter]:
    manager = JITRuntimeManager(region_provider=reserve_native_region)
    engine = TimedRuntime(manager, module)
    engine.register_module_blocks(module)
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        bump_allocator=engine.bump_allocator,
        execution_plugin=manager,
        yield_threshold=FB_CONF_INTERPRETER_YIELD_THRESHOLD,
    )
    return manager, engine, interpreter


def _measure_arithmetic() -> dict[str, object]:
    from bench_jit import JITCompilerBenchmark

    module = parse(memoryview(JITCompilerBenchmark._create_heavy_loop_binary()))
    function_index = module.export_func_index("heavy_loop")
    manager, engine, interpreter = _new_runtime(module)
    try:
        warmup = engine.call(interpreter, function_index, [100], idle_budget=0).unwrap()
        assert int(warmup[0]) == 4_950
        engine.idle_hook(budget=10)
        engine.boundary_ns.clear()
        engine.yield_ns.clear()
        result = engine.call(interpreter, function_index, [100_000], idle_budget=0).unwrap()
        total = (100_000 * 99_999 // 2) & 0xFFFF_FFFF
        expected = total - 0x1_0000_0000 if total & 0x8000_0000 else total
        assert int(result[0]) == expected
        assert engine.yield_ns
        return {
            "workload": "arithmetic-100k",
            "threshold": FB_CONF_INTERPRETER_YIELD_THRESHOLD,
            "boundary_calls": len(engine.boundary_ns),
            "yield_count": len(engine.yield_ns),
            "max_yield_return_ms": max(engine.yield_ns) / 1_000_000,
            "median_yield_return_ms": statistics.median(engine.yield_ns) / 1_000_000,
            "max_boundary_ms": max(engine.boundary_ns) / 1_000_000,
        }
    finally:
        manager.close()


def _measure_aobench() -> dict[str, object]:
    from ipc_router import FB_URI_HAL_STDOUT
    from system import System
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.hal.stream import StreamTransport
    from tier3_platform.drivers.wasi.context import WasiHostContext

    wasm_path = _BENCH / "aobench" / "aobench.wasm"
    wasm = wasm_path.read_bytes()
    module = parse(memoryview(wasm))
    system = System()
    system.transport = StreamTransport(1 << 20)
    wasi = WasiHostContext(system)
    system.start_hal_driver(
        DummyDriver(system.pool, transport=system.transport), FB_URI_HAL_STDOUT
    ).unwrap()
    functions = wasi.build_interpreter_host_functions(module)
    module.init_memory_data(wasi.guest_memory, ())
    manager = JITRuntimeManager(region_provider=reserve_native_region)
    engine = TimedRuntime(manager, module)
    engine.register_module_blocks(module)
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi.guest_memory, functions),
        bump_allocator=engine.bump_allocator,
        execution_plugin=manager,
        yield_threshold=FB_CONF_INTERPRETER_YIELD_THRESHOLD,
    )
    function_index = module.export_func_index("main")
    frame_hash = ""
    try:
        for frame_index in range(10):
            _run_call(engine, interpreter, function_index, [32, 16], 4 if frame_index < 3 else 0)
            frame = system.transport.drain_output()
            assert len(frame) == 528
            current_hash = hashlib.sha256(frame).hexdigest()
            if not frame_hash:
                frame_hash = current_hash
            assert current_hash == frame_hash
            if frame_index == 2:
                engine.boundary_ns.clear()
                engine.yield_ns.clear()
        assert engine.yield_ns
        return {
            "workload": "aobench-32x16",
            "threshold": FB_CONF_INTERPRETER_YIELD_THRESHOLD,
            "warmup_frames": 3,
            "measured_frames": 7,
            "boundary_calls": len(engine.boundary_ns),
            "yield_count": len(engine.yield_ns),
            "max_yield_return_ms": max(engine.yield_ns) / 1_000_000,
            "median_yield_return_ms": statistics.median(engine.yield_ns) / 1_000_000,
            "max_boundary_ms": max(engine.boundary_ns) / 1_000_000,
            "frame_bytes": 528,
            "frame_sha256": frame_hash,
            "wasm_sha256": hashlib.sha256(wasm).hexdigest(),
        }
    finally:
        manager.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=("arithmetic", "aobench"))
    args = parser.parse_args()
    binary_hashes = {
        path: _sha256(_PYSIM / path)
        for path in (
            "qa/private/libinterpreter_probe.so",
            "qa/private/libjit_probe.so",
        )
    }
    source_hashes = {
        path: _sha256(_ROOT / path)
        for path in (
            "experiments/pysim/qa/shared/jit_cache.py",
            "experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx",
            "experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx",
            "experiments/pysim/benchmarks/jit/bench_jit.py",
            "experiments/pysim/benchmarks/aobench/bench_native_build.py",
        )
    }
    measurement = _measure_arithmetic() if args.workload == "arithmetic" else _measure_aobench()
    measurement.update(
        {
            "source_revision": os.environ.get("FIREBALL_REVISION", "unspecified"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_affinity": sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else [],
            "build": "Clang C++23 -O3 -flto=thin --qa, assertions enabled",
            "runner_sha256": _sha256(Path(__file__)),
            "binary_sha256": binary_hashes,
            "source_sha256": source_hashes,
        }
    )
    print(json.dumps(measurement, ensure_ascii=False))


if __name__ == "__main__":
    main()
