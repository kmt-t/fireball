"""保存した AO-Bench runner へ診断またはプロファイル境界を付けて実行する。"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BENCH = ROOT / "experiments/pysim/benchmarks/aobench/bench_native_build.py"
COUNTERS = (
    "block_search_calls",
    "block_search_hits",
    "block_search_misses",
    "control_block_loop_constructs",
    "control_if_constructs",
    "runtime_call_entries",
    "call_frame_constructs",
    "dispatch_iterations",
    "jit_lookup_calls",
    "runtime_if_entries",
    "runtime_block_loop_entries",
    "native_dispatch_entries",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=("interpreter", "jit"))
    parser.add_argument("--width", required=True, type=int)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("cost_bench", BENCH)
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    assert os.sched_getaffinity(0) == {2}
    libraries = [
        ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so",
        ROOT / "experiments/pysim/tier3_plugins/jit/libtrace_compiler.so",
    ]
    readers = []
    if args.diagnostic:
        for path in libraries[: 2 if args.engine == "jit" else 1]:
            library = ctypes.CDLL(str(path))
            reset = library.fb_cost_reset
            reset.argtypes = []
            reset.restype = None
            read = library.fb_cost_read
            read.argtypes = [ctypes.c_uint32]
            read.restype = ctypes.c_uint64
            readers.append((reset, read))
    controller = None
    if args.profile:
        controller = ctypes.CDLL("/opt/AMDuProf_5.3-521/lib/x64/shared/libAMDProfileController.so")
        controller.amdProfileResumeImpl.restype = ctypes.c_bool
        controller.amdProfilePauseImpl.restype = ctypes.c_bool
    original_call = bench.NativeInterpreter.call
    frames: list[dict[str, int]] = []
    frame_index = 0

    def observed_call(self, *call_args, **call_kwargs):
        nonlocal frame_index
        for reset, _ in readers:
            reset()
        if controller is not None and frame_index >= 3:
            assert controller.amdProfileResumeImpl()
        result = original_call(self, *call_args, **call_kwargs)
        if controller is not None and frame_index >= 3:
            assert controller.amdProfilePauseImpl()
        if readers:
            frames.append(
                {
                    name: sum(read(index) for _, read in readers)
                    for index, name in enumerate(COUNTERS)
                }
            )
        frame_index += 1
        return result

    bench.NativeInterpreter.call = observed_call
    result = bench.measure(args.engine, args.width, 3, args.repeats, 4096)
    result["affinity"] = sorted(os.sched_getaffinity(0))
    result["diagnostic"] = args.diagnostic
    result["profiled"] = args.profile
    result["frame_counters"] = frames
    print(json.dumps(result))


if __name__ == "__main__":
    main()
