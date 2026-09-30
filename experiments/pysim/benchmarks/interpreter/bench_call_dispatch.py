"""Measure direct and indirect WASM function calls in PySIM."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import TypedDict

_PYSIM_DIR = Path(__file__).resolve()
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

import wasmtime
from tier3_executer.interpreter.interpreter import (
    Interpreter,
    InterpreterBindings,
    NativeInterpreter,
)
from wasm_reader import parse

WAT = r"""(module
  (type $unary (func (param i32) (result i32)))
  (table 1 funcref)
  (elem (i32.const 0) $leaf)
  (func $leaf (type $unary) (param $x i32) (result i32)
    (i32.add (local.get $x) (i32.const 1)))
  (func (export "bench_baseline") (param $n i32) (result i32)
    (local $i i32) (local $sum i32)
    (block $exit
      (loop $loop
        (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $sum (i32.add (local.get $sum) (i32.add (local.get $i) (i32.const 1))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $loop)))
    (local.get $sum))
  (func (export "bench_direct") (param $n i32) (result i32)
    (local $i i32) (local $sum i32)
    (block $exit
      (loop $loop
        (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $sum (i32.add (local.get $sum) (call $leaf (local.get $i))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $loop)))
    (local.get $sum))
  (func (export "bench_indirect") (param $n i32) (result i32)
    (local $i i32) (local $sum i32)
    (block $exit
      (loop $loop
        (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $sum (i32.add (local.get $sum)
          (call_indirect (type $unary) (local.get $i) (i32.const 0))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $loop)))
    (local.get $sum)))"""


class Measurement(TypedDict):
    variant: str
    workload: str
    samples_ms: list[float]
    median_ms: float
    min_ms: float
    max_ms: float
    ns_per_loop_iteration: float
    calls: int
    ns_per_guest_call: float | None


class HostInfo(TypedDict):
    platform: str
    machine: str
    processor: str
    python: str


class BenchmarkReport(TypedDict):
    host: HostInfo
    iterations: int
    repeats: int
    expected_sum: int
    results: list[Measurement]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variant", choices=("python", "native", "both"), default="both")
    parser.add_argument(
        "--workload", choices=("baseline", "direct", "indirect", "all"), default="all"
    )
    args = parser.parse_args()
    assert 0 < args.iterations <= 65_535
    assert args.repeats > 0

    wasm = bytes(wasmtime.wat2wasm(WAT))
    module = parse(wasm)
    expected = args.iterations * (args.iterations + 1) // 2
    funcs = {
        name: module.export_func_index(f"bench_{name}")
        for name in ("baseline", "direct", "indirect")
    }
    variants = ("python", "native") if args.variant == "both" else (args.variant,)
    workloads = ("baseline", "direct", "indirect") if args.workload == "all" else (args.workload,)
    result: BenchmarkReport = {
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "iterations": args.iterations,
        "repeats": args.repeats,
        "expected_sum": expected,
        "results": [],
    }

    for variant in variants:
        interpreter_type = Interpreter if variant == "python" else NativeInterpreter
        interpreter = interpreter_type(module, InterpreterBindings.empty())
        for workload in workloads:
            function_index = funcs[workload]
            # The untimed warmup validates the same result for every measured call.
            assert interpreter.call(function_index, [args.iterations]) == [expected]
            samples_ms: list[float] = []
            for _ in range(args.repeats):
                start_ns = time.perf_counter_ns()
                value = interpreter.call(function_index, [args.iterations])
                elapsed_ns = time.perf_counter_ns() - start_ns
                assert value == [expected]
                samples_ms.append(elapsed_ns / 1_000_000)
            median_ms = statistics.median(samples_ms)
            calls = args.iterations if workload != "baseline" else 0
            result["results"].append(
                {
                    "variant": variant,
                    "workload": workload,
                    "samples_ms": samples_ms,
                    "median_ms": median_ms,
                    "min_ms": min(samples_ms),
                    "max_ms": max(samples_ms),
                    "ns_per_loop_iteration": median_ms * 1_000_000 / args.iterations,
                    "calls": calls,
                    "ns_per_guest_call": median_ms * 1_000_000 / calls if calls else None,
                }
            )

    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
