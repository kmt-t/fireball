"""Collect temporary control-frame push/pop counters from AO-Bench."""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BENCH = ROOT / "experiments/pysim/benchmarks/aobench/bench_native_build.py"
LIBRARY = ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so"
COUNTERS = (
    "push_block_loop_if",
    "pop_else",
    "pop_end",
    "pop_branch",
    "pop_static_prune",
    "pop_function_return",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    assert os.sched_getaffinity(0) == {2}
    spec = importlib.util.spec_from_file_location("call_bench", BENCH)
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    library = ctypes.CDLL(str(LIBRARY))
    reset = library.fb_control_frame_reset
    reset.argtypes = []
    reset.restype = None
    read = library.fb_control_frame_read
    read.argtypes = [ctypes.c_uint32]
    read.restype = ctypes.c_uint64

    original_call = bench.NativeInterpreter.call
    frame_counters: list[dict[str, int]] = []

    def observed_call(self, *call_args, **call_kwargs):
        reset()
        result = original_call(self, *call_args, **call_kwargs)
        frame_counters.append({name: int(read(i)) for i, name in enumerate(COUNTERS)})
        return result

    bench.NativeInterpreter.call = observed_call
    result = bench.measure("interpreter", args.width, args.warmup, args.repeats, 4096)
    result["affinity"] = sorted(os.sched_getaffinity(0))
    result["frame_counters"] = frame_counters
    result["diagnostic_build"] = True
    print(json.dumps(result))


if __name__ == "__main__":
    main()
