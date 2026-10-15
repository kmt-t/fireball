"""Measure native PC-to-BasicBlock lookup with a 10,000+ block module."""

from __future__ import annotations

import argparse
import ctypes
import statistics
import sys
import time
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parents[1]
_PYSIM_DIR = _BENCH_DIR.parent
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from bump_allocator import BumpAllocator
from tier2_runtime.abi.native_abi import RUN_DISPATCH, NativeResult
from tier2_runtime.interpreter.interpreter import (
    InterpreterBindings,
    InterpreterCall,
    NativeInterpreter,
)
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse


def _encode_u32(value: int) -> bytes:
    assert 0 <= value <= 0xFFFF_FFFF
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _encode_s32(value: int) -> bytes:
    assert -(1 << 31) <= value < (1 << 31)
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        done = (value == 0 and (byte & 0x40) == 0) or (value == -1 and (byte & 0x40) != 0)
        encoded.append(byte if done else byte | 0x80)
        if done:
            return bytes(encoded)


def _section(section_id: int, payload: bytes) -> bytes:
    return bytes((section_id,)) + _encode_u32(len(payload)) + payload


def _build_module(iterations: int, block_repetitions: int) -> tuple[bytes, int]:
    # Keep the hot loop's back edge at one BasicBlock while adding over 10,000
    # static blocks after its exit path. The workload returns the accumulator
    # to zero, then branches directly to the function end over those cold blocks.
    hot_prefix = (
        b"\x41" + _encode_s32(iterations) + b"\x21\x00"  # i32.const iterations; local.set 0
    )
    loop_head_ip = len(hot_prefix) + 2  # the loop label targets its body
    loop = (
        b"\x03\x40"  # loop void
        b"\x20\x00\x45\x0d\x01"  # local.get 0; eqz; br_if function
        b"\x20\x00\x41\x01\x6b\x21\x00"  # decrement local 0
        b"\x0c\x00"  # br loop
        b"\x0b"  # end loop
    )
    cold_blocks = b"\x02\x40\x0c\x00\x0b" * block_repetitions
    code = hot_prefix + loop + cold_blocks + b"\x0b"
    body = b"\x01\x01\x7f" + code  # one i32 local
    type_payload = b"\x01\x60\x00\x00"  # one () -> () type
    function_payload = b"\x01\x00"
    code_payload = b"\x01" + _encode_u32(len(body)) + body
    wasm = (
        b"\x00asm\x01\x00\x00\x00"
        + _section(1, type_payload)
        + _section(3, function_payload)
        + _section(10, code_payload)
    )
    return wasm, loop_head_ip


def _run_once(interpreter: NativeInterpreter) -> InterpreterCall:
    call_state = interpreter.start(0, ())
    while not call_state.finished:
        call_state = interpreter.step_native_boundary(call_state).call_state
    assert call_state.trap is None
    assert call_state.results is not None and len(call_state.results) == 0
    return call_state


def _dispatcher_from_library(path: Path) -> tuple[ctypes.PyDLL, ctypes._CFuncPtr]:
    library = ctypes.PyDLL(str(path))
    entry = library.fb_native_run_dispatch
    entry.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
    entry.restype = ctypes.c_int
    return library, entry


def _measure(
    module: Module,
    no_cache_dispatcher: ctypes._CFuncPtr,
    warmups: int,
    samples: int,
    yield_threshold: int,
) -> tuple[list[float], list[float]]:
    cached_interpreter = NativeInterpreter.with_module_allocator(
        module,
        InterpreterBindings.empty(),
        native_dispatcher=RUN_DISPATCH,
        yield_threshold=yield_threshold,
    )
    uncached_interpreter = NativeInterpreter.with_module_allocator(
        module,
        InterpreterBindings.empty(),
        native_dispatcher=no_cache_dispatcher,
        yield_threshold=yield_threshold,
    )
    for warmup in range(warmups):
        interpreters = (
            (cached_interpreter, uncached_interpreter)
            if warmup % 2 == 0
            else (uncached_interpreter, cached_interpreter)
        )
        for interpreter in interpreters:
            _run_once(interpreter)

    cached_ms: list[float] = []
    uncached_ms: list[float] = []
    for sample in range(samples):
        interpreters = (
            ((cached_interpreter, cached_ms), (uncached_interpreter, uncached_ms))
            if sample % 2 == 0
            else ((uncached_interpreter, uncached_ms), (cached_interpreter, cached_ms))
        )
        for interpreter, durations in interpreters:
            started = time.perf_counter_ns()
            _run_once(interpreter)
            durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return cached_ms, uncached_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=250_000)
    parser.add_argument("--block-repetitions", type=int, default=3_334)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--yield-threshold", type=int, default=4_096)
    parser.add_argument("--no-cache-library", type=Path, required=True)
    args = parser.parse_args()
    assert args.iterations > 0 and args.block_repetitions > 0
    assert args.warmups >= 0 and args.samples > 0 and args.yield_threshold > 0

    wasm, loop_head_ip = _build_module(args.iterations, args.block_repetitions)
    module = parse(memoryview(wasm), BumpAllocator(capacity=2 * 1024 * 1024))
    assert len(module.blocks) > 10_000, f"fixture has only {len(module.blocks)} blocks"
    function = module.functions[0]
    hot_pc = function.code_pc_offset + loop_head_ip
    hot_block = module.get_block(hot_pc)
    assert hot_block is not None and hot_block.head_pc == hot_pc

    no_cache_library, no_cache_dispatcher = _dispatcher_from_library(args.no_cache_library)
    del no_cache_library  # The function pointer keeps its library owner alive.
    cached, uncached = _measure(
        module,
        no_cache_dispatcher,
        args.warmups,
        args.samples,
        args.yield_threshold,
    )
    cached_median = statistics.median(cached)
    uncached_median = statistics.median(uncached)
    print(f"basic_blocks={len(module.blocks)}")
    print(f"loop_iterations={args.iterations}")
    print(f"cold_blocks={args.block_repetitions}")
    print(f"yield_threshold={args.yield_threshold}")
    print(f"cached_ms={','.join(f'{value:.3f}' for value in cached)}")
    print(f"uncached_ms={','.join(f'{value:.3f}' for value in uncached)}")
    print(f"cached_median_ms={cached_median:.3f}")
    print(f"uncached_median_ms={uncached_median:.3f}")
    print(f"cache_speedup={uncached_median / cached_median:.3f}x")


if __name__ == "__main__":
    main()
