"""Measure WAMR guest calls with the same AO-Bench frames as Fireball (Linux)."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import TypedDict


class WamrMeasurement(TypedDict):
    engine: str
    width: int
    height: int
    warmup: int
    repeats: int
    samples_ms: list[float]
    median_ms: float
    frame_bytes: int
    frame_sha256: str
    wasm_sha256: str
    runner_sha256: str
    library_sha256: str


def _bind(library_path: Path) -> ctypes.CDLL:
    """Bind the vendored wasm_export.h ABI; module handles remain borrowed."""
    library = ctypes.CDLL(str(library_path.resolve()))
    library.wasm_runtime_init.argtypes = []
    library.wasm_runtime_init.restype = ctypes.c_bool
    library.wasm_runtime_destroy.argtypes = []
    library.wasm_runtime_destroy.restype = None
    library.wasm_runtime_set_log_level.argtypes = [ctypes.c_int]
    library.wasm_runtime_set_log_level.restype = None
    library.wasm_runtime_load.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    library.wasm_runtime_load.restype = ctypes.c_void_p
    library.wasm_runtime_unload.argtypes = [ctypes.c_void_p]
    library.wasm_runtime_unload.restype = None
    library.wasm_runtime_set_wasi_args_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
    ]
    library.wasm_runtime_set_wasi_args_ex.restype = None
    library.wasm_runtime_instantiate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    library.wasm_runtime_instantiate.restype = ctypes.c_void_p
    library.wasm_runtime_deinstantiate.argtypes = [ctypes.c_void_p]
    library.wasm_runtime_deinstantiate.restype = None
    library.wasm_runtime_create_exec_env.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    library.wasm_runtime_create_exec_env.restype = ctypes.c_void_p
    library.wasm_runtime_destroy_exec_env.argtypes = [ctypes.c_void_p]
    library.wasm_runtime_destroy_exec_env.restype = None
    library.wasm_runtime_lookup_function.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    library.wasm_runtime_lookup_function.restype = ctypes.c_void_p
    library.wasm_runtime_call_wasm.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.wasm_runtime_call_wasm.restype = ctypes.c_bool
    library.wasm_runtime_get_exception.argtypes = [ctypes.c_void_p]
    library.wasm_runtime_get_exception.restype = ctypes.c_char_p
    return library


def measure(
    library_path: Path,
    width: int,
    warmup: int,
    repeats: int,
    height_override: int | None = None,
) -> WamrMeasurement:
    """Exclude init/load/instantiate and output verification from guest call timing."""
    assert width > 0 and warmup > 0 and repeats > 0
    height = width * 9 // 16 if height_override is None else height_override
    assert height > 0
    wasm_path = Path(__file__).with_name("aobench.wasm")
    wasm = wasm_path.read_bytes()
    wasm_buffer = ctypes.create_string_buffer(wasm)
    error = ctypes.create_string_buffer(512)
    library = _bind(library_path)
    initialized = library.wasm_runtime_init()
    assert initialized
    library.wasm_runtime_set_log_level(0)
    module = library.wasm_runtime_load(wasm_buffer, len(wasm), error, len(error))
    assert module, error.value
    output_fd = os.memfd_create("fireball-wamr-aobench", os.MFD_CLOEXEC)
    library.wasm_runtime_set_wasi_args_ex(
        module, None, 0, None, 0, None, 0, None, 0, -1, output_fd, -1
    )
    instance = library.wasm_runtime_instantiate(module, 65536, 0, error, len(error))
    assert instance, error.value
    exec_env = library.wasm_runtime_create_exec_env(instance, 65536)
    assert exec_env
    function = library.wasm_runtime_lookup_function(instance, b"main")
    assert function
    samples: list[float] = []
    expected_hash = ""
    try:
        for frame_index in range(warmup + repeats):
            os.ftruncate(output_fd, 0)
            os.lseek(output_fd, 0, os.SEEK_SET)
            arguments = (ctypes.c_uint32 * 2)(width, height)
            started = time.perf_counter_ns()
            succeeded = library.wasm_runtime_call_wasm(exec_env, function, 2, arguments)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            assert succeeded, library.wasm_runtime_get_exception(instance)
            frame_bytes = (width + 1) * height
            assert arguments[0] == frame_bytes
            frame = os.pread(output_fd, frame_bytes + 1, 0)
            assert len(frame) == frame_bytes
            frame_hash = hashlib.sha256(frame).hexdigest()
            if frame_index == 0:
                expected_hash = frame_hash
            assert frame_hash == expected_hash
            if frame_index >= warmup:
                samples.append(elapsed_ms)
    finally:
        library.wasm_runtime_destroy_exec_env(exec_env)
        library.wasm_runtime_deinstantiate(instance)
        library.wasm_runtime_unload(module)
        library.wasm_runtime_destroy()
        os.close(output_fd)
    return WamrMeasurement(
        engine="wamr",
        width=width,
        height=height,
        warmup=warmup,
        repeats=repeats,
        samples_ms=samples,
        median_ms=statistics.median(samples),
        frame_bytes=(width + 1) * height,
        frame_sha256=expected_hash,
        wasm_sha256=hashlib.sha256(wasm).hexdigest(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        library_sha256=hashlib.sha256(library_path.read_bytes()).hexdigest(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(measure(args.library, args.width, args.warmup, args.repeats, args.height)))
