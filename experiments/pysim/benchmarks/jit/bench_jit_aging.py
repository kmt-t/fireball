"""
experiments/pysim/benchmarks/jit/bench_jit_aging.py
JIT Card Aging Benchmark: cold-function pollution under JIT cache pressure.
Conforms to docs/components/tier3_plugins/benchmarks/jit_aging_bench_spec.md (BENCHMARK-AGING-01..BENCHMARK-AGING-04).

Workload (a few hot functions, many cold ones):
  * A small set of hot functions runs a short arithmetic loop on every pass. Their
    traces fit the 3-bank JIT cache, so a perfect cache compiles each of them once.
  * A large set of cold functions is visited a few at a time, round-robin. Every cold
    function is therefore executed only once in a long while, but *ever more than
    once* over the whole run. Without aging, its card stays EXECUTED forever, the
    next visit makes it HOT, and its trace is compiled and evicts a hot trace.

Variants. The aging sweep parameters are build-time configuration, so each aging
setting is a QA probe library built by native/tier3_plugins/jit/build_native.sh:
  build_native.sh --qa --aging-units=0 --output=<dir>/u0/libjit_probe.so          (no aging)
  build_native.sh --qa --aging-units=1 --aging-scan-bytes=4 --output=<dir>/u1-o4/libjit_probe.so
  build_native.sh --qa --aging-units=8 --aging-scan-bytes=32 --output=<dir>/u8-o32/libjit_probe.so
The product configuration (the default QA probe library) is the config default. The hot-only
reference runs the config default without the cold functions. Each variant runs in its own
process that loads its library through FB_QA_JIT_PROBE_LIBRARY.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from bump_allocator import BumpAllocator
from config import FB_CONF_JIT_AGING_STEP_SCAN_BYTES, FB_CONF_JIT_AGING_STEP_UNITS
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.runtime_stats import RuntimeStatsEngine
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.wasm.reader import parse

try:
    import wasmtime
except ImportError:  # optional: the workload module is written as WAT text
    wasmtime = None

VARIANT_LIBRARY_DIR = _PYSIM_DIR / "build" / "aging-variants"
DEFAULT_LABEL = (
    f"aging U={FB_CONF_JIT_AGING_STEP_UNITS} O={FB_CONF_JIT_AGING_STEP_SCAN_BYTES} (config default)"
)


def build_workload_wat(hot_functions: int, cold_functions: int) -> str:
    """WAT text: hot functions run a short loop, cold functions are straight-line code."""
    parts = ["(module"]
    for i in range(hot_functions):
        parts.append(
            f'(func (export "h{i}") (param $n i32) (result i32) (local $s i32) (local $k i32)'
            f"(block $x (loop $t"
            f"(br_if $x (i32.ge_s (local.get $k) (local.get $n)))"
            f"(local.set $s (i32.add (local.get $s)"
            f" (i32.xor (i32.mul (local.get $k) (i32.const {3 + i})) (i32.const 7))))"
            f"(local.set $k (i32.add (local.get $k) (i32.const 1)))"
            f"(br $t)))"
            f"(local.get $s))"
        )
    for i in range(cold_functions):
        parts.append(
            f'(func (export "c{i}") (param $n i32) (result i32)'
            f"(i32.add (i32.mul (local.get $n) (i32.const {5 + i})) (i32.const {i})))"
        )
    parts.append(")")
    return "".join(parts)


@dataclass
class AgingResult:
    label: str
    time_ms: float  # total run time
    compile_ms: float  # time inside the trace compiler
    aging_ms: float  # time inside the aging sweep
    compiles: int
    purged: int
    rotations: int
    jit_share_pct: float | None
    aging_steps: int
    aging_units: int
    aging_bytes_scanned: int
    checksum: int


def run_workload(
    label: str,
    cold_per_pass: int,
    passes: int,
    hot_functions: int,
    cold_functions: int,
    hot_iterations: int,
) -> AgingResult:
    """Run one variant in this process, against the QA probe library the process loaded."""
    assert wasmtime is not None
    wasm = bytes(wasmtime.wat2wasm(build_workload_wat(hot_functions, cold_functions)))
    runtime_allocator = BumpAllocator()
    module = parse(memoryview(wasm), runtime_allocator)
    engine = RuntimeStatsEngine(
        jit_runtime=JITRuntimeManager(),
        collect_runtime_stats=True,
        bump_allocator=runtime_allocator,
    )
    engine.register_module_blocks(module)
    cache = engine.jit_runtime.cache
    interp = NativeInterpreter(
        module, InterpreterBindings.empty(), bump_allocator=runtime_allocator
    )
    hot = [module.export_func_index(f"h{i}") for i in range(hot_functions)]
    cold = [module.export_func_index(f"c{i}") for i in range(cold_functions)]

    checksum = 0
    t0 = time.perf_counter()
    for pass_index in range(passes):
        for func in hot:
            checksum += engine.call(interp, func, [hot_iterations]).unwrap()[0]
        for j in range(cold_per_pass):
            func = cold[(pass_index * cold_per_pass + j) % len(cold)]
            checksum += engine.call(interp, func, [pass_index]).unwrap()[0]
    time_ms = (time.perf_counter() - t0) * 1000

    jit = engine.stat_jit_invocations
    total = jit + engine.stat_interp_steps
    compile_ms = cache.compile_ns / 1e6
    aging_ms = cache.aging_ns / 1e6
    assert compile_ms + aging_ms <= time_ms, "nested timings cannot exceed the total"
    return AgingResult(
        label=label,
        time_ms=time_ms,
        compile_ms=compile_ms,
        aging_ms=aging_ms,
        compiles=cache.compile_attempts,
        purged=cache.evictions,
        rotations=cache.rotations,
        jit_share_pct=(100.0 * jit / total if total > 0 else 0.0)
        if engine.collect_runtime_stats
        else None,
        aging_steps=engine.jit_runtime.aging_steps,
        aging_units=engine.jit_runtime.aging_units_processed,
        aging_bytes_scanned=engine.jit_runtime.aging_bytes_scanned,
        checksum=checksum & 0xFFFF_FFFF,
    )


class JITAgingBenchmark:
    """Cold-function pollution of the JIT cache, with and without the aging sweep."""

    def __init__(
        self,
        hot_functions: int = 4,
        cold_functions: int = 200,
        passes: int = 200,
        hot_iterations: int = 8,
        cold_per_pass: int = 8,
        repeats: int = 3,
    ):
        assert repeats >= 1
        self.repeats = repeats
        self.hot_functions = hot_functions
        self.cold_functions = cold_functions
        self.passes = passes
        self.hot_iterations = hot_iterations
        self.cold_per_pass = cold_per_pass

    def run_in_process(self, label: str, library: Path | None, cold_per_pass: int) -> AgingResult:
        """Run one repetition of a variant in a fresh process that loads `library`."""
        env = dict(os.environ)
        if library is None:
            env.pop("FB_QA_JIT_PROBE_LIBRARY", None)
        else:
            assert library.exists(), f"build the variant library first: {library}"
            env["FB_QA_JIT_PROBE_LIBRARY"] = str(library)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--label",
            label,
            "--cold-per-pass",
            str(cold_per_pass),
            "--passes",
            str(self.passes),
            "--hot-functions",
            str(self.hot_functions),
            "--cold-functions",
            str(self.cold_functions),
            "--hot-iterations",
            str(self.hot_iterations),
        ]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        return AgingResult(**json.loads(completed.stdout.strip().splitlines()[-1]))

    def run_repeated(self, label: str, library: Path | None, cold_per_pass: int) -> AgingResult:
        """Run a variant `repeats` times: counters must repeat exactly, times are medians."""
        runs = [self.run_in_process(label, library, cold_per_pass) for _ in range(self.repeats)]
        first = runs[0]
        for run in runs[1:]:
            assert (run.compiles, run.purged, run.rotations, run.aging_steps, run.checksum) == (
                first.compiles,
                first.purged,
                first.rotations,
                first.aging_steps,
                first.checksum,
            ), "the counters of a variant are deterministic"
        return replace(
            first,
            time_ms=statistics.median(run.time_ms for run in runs),
            compile_ms=statistics.median(run.compile_ms for run in runs),
            aging_ms=statistics.median(run.aging_ms for run in runs),
        )

    def run_all(self) -> list[AgingResult] | None:
        """Return the variants' results, or None when the optional WAT compiler is absent."""
        if wasmtime is None:
            return None
        results = [
            self.run_repeated("hot only (ideal)", None, 0),
            self.run_repeated(
                "no aging", VARIANT_LIBRARY_DIR / "u0" / "libjit_probe.so", self.cold_per_pass
            ),
            self.run_repeated(
                "aging U=1 O=4",
                VARIANT_LIBRARY_DIR / "u1-o4" / "libjit_probe.so",
                self.cold_per_pass,
            ),
            self.run_repeated(DEFAULT_LABEL, None, self.cold_per_pass),
            self.run_repeated(
                "aging U=8 O=32",
                VARIANT_LIBRARY_DIR / "u8-o32" / "libjit_probe.so",
                self.cold_per_pass,
            ),
        ]
        self._check(results)
        return results

    @staticmethod
    def _check(results: list[AgingResult]) -> None:
        ideal = next(r for r in results if r.label == "hot only (ideal)")
        assert ideal.purged == 0, "the hot traces must fit the 3-bank cache"
        no_aging = next(r for r in results if r.label == "no aging")
        polluted = [r for r in results if r.label != "hot only (ideal)"]
        assert len({r.checksum for r in polluted}) == 1, "aging must not change program results"
        # The no-aging library runs the sweep at each rotation with zero units: it scans nothing.
        assert no_aging.aging_units == 0 and no_aging.aging_bytes_scanned == 0
        for r in results:
            if r.label.startswith("aging") or r.label.endswith("(config default)"):
                assert r.aging_steps == r.rotations, "one aging step per bank rotation"
        default = next(r for r in results if r.label.endswith("(config default)"))
        assert default.compiles <= no_aging.compiles, "aging must not compile more than no aging"


def _worker(args: argparse.Namespace) -> None:
    result = run_workload(
        args.label,
        args.cold_per_pass,
        args.passes,
        args.hot_functions,
        args.cold_functions,
        args.hot_iterations,
    )
    print(json.dumps(asdict(result)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--cold-per-pass", type=int, default=0)
    parser.add_argument("--passes", type=int, default=200)
    parser.add_argument("--hot-functions", type=int, default=4)
    parser.add_argument("--cold-functions", type=int, default=200)
    parser.add_argument("--hot-iterations", type=int, default=8)
    args = parser.parse_args()
    if args.worker:
        _worker(args)
        return
    print("=" * 80)
    print("      [Benchmark] JIT Card Aging under Cache Pressure (Cold-Function Pollution)")
    print("=" * 80)
    bench = JITAgingBenchmark()
    results = bench.run_all()
    if results is None:
        print("  [SKIP] wasmtime (WAT compiler) is not installed.")
        return
    print(
        f"  Workload: {bench.hot_functions} hot functions ({bench.hot_iterations} iterations/call), "
        f"{bench.cold_functions} cold functions ({bench.cold_per_pass} calls/pass), "
        f"{bench.passes} passes"
    )
    print(
        f"  Timing: median of {bench.repeats} runs per variant "
        "(counters are deterministic; times vary by host)"
    )
    print(
        f"  {'Variant':<30}{'Compiles':>9}{'Purged':>8}{'Rotations':>10}{'JIT share':>10}"
        f"{'Time(ms)':>10}{'Compile(ms)':>12}{'Aging(ms)':>10}"
    )
    for r in results:
        jit_share = f"{r.jit_share_pct:.1f}%" if r.jit_share_pct is not None else "N/A"
        print(
            f"  {r.label:<30}{r.compiles:>9}{r.purged:>8}{r.rotations:>10}{jit_share:>10}"
            f"{r.time_ms:>10.0f}{r.compile_ms:>12.1f}{r.aging_ms:>10.2f}"
        )
    no_aging = next(r for r in results if r.label == "no aging")
    default = next(r for r in results if r.label.endswith("(config default)"))
    saved = 100.0 * (no_aging.compiles - default.compiles) / max(1, no_aging.compiles)
    print(f"  * Compiles saved by the config default:   {saved:.1f}%")
    faster = 100.0 * (no_aging.time_ms - default.time_ms) / no_aging.time_ms
    print(f"  * Run time change (config default):       {faster:+.1f}% faster than no aging")
    if default.aging_steps > 0:
        print(
            "  * Aging cost (config default):            "
            f"{default.aging_ms * 1000 / default.aging_steps:.1f} us/step, "
            f"{100.0 * default.aging_ms / default.time_ms:.2f}% of the run"
        )
    print("=" * 80)
    print("[PASS] JIT Card Aging benchmark completed successfully.")


if __name__ == "__main__":
    main()
