"""Measure one native build with reproducible AO-Bench input and output hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import TypedDict

_BENCH_DIR = Path(__file__).resolve().parents[1]
_PYSIM_DIR = _BENCH_DIR.parent
sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths, reserve_native_region

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from ipc_router import FB_URI_HAL_STDOUT
from runtime_driver import run_runtime_call
from system import System
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.wasm.reader import parse_standalone
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.wasi.context import WasiHostContext
from tier3_plugins.jit.jit_manager import JITRuntimeManager


class NativeBuildMeasurement(TypedDict):
    engine: str
    width: int
    height: int
    warmup: int
    repeats: int
    yield_threshold: int
    samples_ms: list[float]
    median_ms: float
    frame_bytes: int
    frame_sha256: str
    wasm_sha256: str
    runner_sha256: str
    interpreter_library_sha256: str
    jit_library_sha256: str
    python: str
    platform: str


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(
    engine: str,
    width: int,
    warmup: int,
    repeats: int,
    yield_threshold: int = 4096,
    height_override: int | None = None,
) -> NativeBuildMeasurement:
    """Time guest execution after loading and warming the selected native path."""
    assert engine == "interpreter" or engine == "jit"
    assert width > 0 and warmup > 0 and repeats > 0
    assert 1 <= yield_threshold <= 0xFFFF_FFFF
    height = width * 9 // 16 if height_override is None else height_override
    assert height > 0
    wasm_path = Path(__file__).with_name("aobench.wasm")
    module = parse_standalone(memoryview(wasm_path.read_bytes()))
    system = System()
    system.transport = StreamTransport(1 << 20)
    wasi = WasiHostContext.create_default(system)
    system.start_hal_driver(
        DummyDriver(system.pool, transport=system.transport), FB_URI_HAL_STDOUT
    ).unwrap()
    functions = wasi.build_interpreter_host_functions(module)
    module.init_memory_data(wasi.guest_memory, ())
    runtime = RuntimeEngine()
    runtime.register_module_blocks(module)
    plugin = JITRuntimeManager(reserve_native_region) if engine == "jit" else None
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(wasi.guest_memory, functions),
        bump_allocator=runtime.bump_allocator,
        execution_plugin=plugin,
        yield_threshold=yield_threshold,
    )
    function_index = module.export_func_index("main")
    samples: list[float] = []
    expected_hash = ""
    try:
        for frame_index in range(warmup + repeats):
            if frame_index == warmup and plugin is not None:
                runtime.idle_hook()
            started = time.perf_counter_ns()
            run_runtime_call(runtime, interpreter, function_index, [width, height]).unwrap()
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            frame = system.transport.drain_output()
            assert len(frame) == (width + 1) * height
            frame_hash = hashlib.sha256(frame).hexdigest()
            if frame_index == 0:
                expected_hash = frame_hash
            assert frame_hash == expected_hash
            if frame_index >= warmup:
                samples.append(elapsed_ms)
    finally:
        if plugin is not None:
            plugin.close()
    return NativeBuildMeasurement(
        engine=engine,
        width=width,
        height=height,
        warmup=warmup,
        repeats=repeats,
        yield_threshold=yield_threshold,
        samples_ms=samples,
        median_ms=statistics.median(samples),
        frame_bytes=(width + 1) * height,
        frame_sha256=expected_hash,
        wasm_sha256=_file_hash(wasm_path),
        runner_sha256=_file_hash(Path(__file__)),
        interpreter_library_sha256=_file_hash(
            _PYSIM_DIR / "tier2_runtime/interpreter/libnative_interpreter.so"
        ),
        jit_library_sha256=_file_hash(_PYSIM_DIR / "tier3_plugins/jit/libtrace_compiler.so"),
        python=platform.python_version(),
        platform=platform.platform(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("interpreter", "jit"), required=True)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--yield-threshold", type=int, default=4096)
    parser.add_argument("--height", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            measure(
                args.engine,
                args.width,
                args.warmup,
                args.repeats,
                args.yield_threshold,
                args.height,
            )
        )
    )
