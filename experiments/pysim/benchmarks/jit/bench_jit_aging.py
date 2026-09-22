"""
experiments/pysim/benchmarks/jit/bench_jit_aging.py
JIT Card Aging Benchmark: cold-function pollution under JIT cache pressure.
Conforms to docs/components/tier3_executer/benchmarks/jit_aging_bench_spec.md (BENCHMARK-AGING-01 ~ BENCHMARK-AGING-03).

Workload (a few hot functions, many cold ones):
  * A small set of hot functions runs a short arithmetic loop on every pass. Their
    traces fit the 3-bank JIT cache, so a perfect cache compiles each of them once.
  * A large set of cold functions is visited a few at a time, round-robin. Every cold
    function is therefore executed only once in a long while, but *ever more than
    once* over the whole run. Without aging, its card stays EXECUTED forever, the
    next visit makes it HOT, and its trace is compiled and evicts a hot trace.
The benchmark reports compiles, evictions, rotations and the JIT execution share
for "aging off" and several ({FB_CONF_JIT_AGING_STEP_UNITS}, {FB_CONF_JIT_AGING_STEP_SCAN_BYTES})
settings, plus a hot-only run as the ideal reference.
"""

from __future__ import annotations

import statistics
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

from config import FB_CONF_JIT_AGING_STEP_SCAN_BYTES, FB_CONF_JIT_AGING_STEP_UNITS
from tier3_executer.interpreter.interpreter import Interpreter, InterpreterBindings
from runtime_engine import RuntimeEngine
from tier3_executer.jit.jit_cache import JITTrace
from tier3_executer.jit.jit_manager import JITRuntimeManager
from wasm_module import WasmOperand
from wasm_reader import parse
from tier3_executer.jit.x64_jit import TraceCompiler

try:
    import wasmtime
except ImportError:  # optional: the workload module is written as WAT text
    wasmtime = None


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


class _CountingCompiler:
    """Counts compile requests in front of the real trace compiler."""

    def __init__(self) -> None:
        self.inner = TraceCompiler()
        self.compiles = 0
        self.compile_ns = 0

    def compile_trace(
        self,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_types: Sequence[int],
    ) -> JITTrace | None:
        self.compiles += 1
        start = time.perf_counter_ns()
        trace = self.inner.compile_trace(
            head_pc, instructions, next_pc, loops_to, byte_span, local_types
        )
        self.compile_ns += time.perf_counter_ns() - start
        return trace


class _RotationHook:
    """Counts bank rotations and, when aging is on, runs the aging step from them."""

    def __init__(self, engine: RuntimeEngine, aging: bool) -> None:
        self.engine = engine
        self.aging = aging
        self.rotations = 0
        self.aging_ns = 0

    def __call__(self) -> int:
        self.rotations += 1
        if not self.aging:
            return 0
        start = time.perf_counter_ns()
        decayed = self.engine.age_step()
        self.aging_ns += time.perf_counter_ns() - start
        return decayed


@dataclass
class AgingResult:
    label: str
    time_ms: float  # total run time
    compile_ms: float  # time inside the trace compiler
    aging_ms: float  # time inside the aging sweep
    compiles: int
    purged: int
    rotations: int
    promotions: int
    jit_share_pct: float
    aging_steps: int
    checksum: int


class JITAgingBenchmark:
    """Cold-function pollution of the JIT cache, with and without the aging sweep."""

    def __init__(
        self,
        hot_functions: int = 8,
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

    def run_variant(
        self,
        label: str,
        aging: bool,
        cold_per_pass: int,
        units: int | None = None,
        scan_bytes: int | None = None,
    ) -> AgingResult:
        assert wasmtime is not None
        wasm = bytes(wasmtime.wat2wasm(build_workload_wat(self.hot_functions, self.cold_functions)))
        module = parse(wasm)
        compiler = _CountingCompiler()
        settings: dict[str, int] = {}
        if units is not None:
            settings["aging_step_units"] = units
        if scan_bytes is not None:
            settings["aging_scan_bytes"] = scan_bytes
        engine = RuntimeEngine(
            jit_runtime=JITRuntimeManager(
                jit_compiler=compiler, yield_threshold=16, **settings
            )
        )
        engine.register_module_blocks(module)
        hook = _RotationHook(engine, aging)
        engine.jit_runtime.cache.on_rotate = hook
        interp = Interpreter(module, InterpreterBindings.empty())
        hot = [module.export_func_index(f"h{i}") for i in range(self.hot_functions)]
        cold = [module.export_func_index(f"c{i}") for i in range(self.cold_functions)]

        checksum = 0
        t0 = time.perf_counter()
        for pass_index in range(self.passes):
            for func in hot:
                checksum += engine.run(interp, func, [self.hot_iterations])[0]
            for j in range(cold_per_pass):
                func = cold[(pass_index * cold_per_pass + j) % len(cold)]
                checksum += engine.run(interp, func, [pass_index])[0]
        time_ms = (time.perf_counter() - t0) * 1000

        jit = engine.stat_jit_invocations
        total = jit + engine.stat_interp_steps
        compile_ms = compiler.compile_ns / 1e6
        aging_ms = hook.aging_ns / 1e6
        assert compile_ms + aging_ms <= time_ms, "nested timings cannot exceed the total"
        return AgingResult(
            label=label,
            time_ms=time_ms,
            compile_ms=compile_ms,
            aging_ms=aging_ms,
            compiles=compiler.compiles,
            purged=engine.jit_runtime.cache.evictions,
            rotations=hook.rotations,
            promotions=engine.jit_runtime.cache.promotions,
            jit_share_pct=100.0 * jit / total if total > 0 else 0.0,
            aging_steps=engine.jit_runtime.aging_steps,
            checksum=checksum & 0xFFFF_FFFF,
        )

    def run_repeated(
        self,
        label: str,
        aging: bool,
        cold_per_pass: int,
        units: int | None = None,
        scan_bytes: int | None = None,
    ) -> AgingResult:
        """Run a variant `repeats` times: counters must repeat exactly, times are medians."""
        runs = [
            self.run_variant(label, aging, cold_per_pass, units, scan_bytes)
            for _ in range(self.repeats)
        ]
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
        default_label = (
            f"aging U={FB_CONF_JIT_AGING_STEP_UNITS} O={FB_CONF_JIT_AGING_STEP_SCAN_BYTES}"
            " (config default)"
        )
        results = [
            self.run_repeated("hot only (ideal)", False, 0),
            self.run_repeated("no aging", False, self.cold_per_pass),
            self.run_repeated("aging U=1 O=4", True, self.cold_per_pass, 1, 4),
            self.run_repeated(default_label, True, self.cold_per_pass),
            self.run_repeated("aging U=8 O=32", True, self.cold_per_pass, 8, 32),
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
        for r in results:
            if r.label.startswith("aging"):
                assert r.aging_steps == r.rotations, "one aging step per bank rotation"
            else:
                assert r.aging_steps == 0, "the aging sweep only runs when enabled"
        default = next(r for r in results if r.label.endswith("(config default)"))
        assert default.compiles <= no_aging.compiles, "aging must not compile more than no aging"


def main() -> None:
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
        f"  Timing: median of {bench.repeats} runs per variant (counters are deterministic; times vary by host)"
    )
    print(
        f"  {'Variant':<30}{'Compiles':>9}{'Purged':>8}{'Rotations':>10}{'JIT share':>10}"
        f"{'Time(ms)':>10}{'Compile(ms)':>12}{'Aging(ms)':>10}"
    )
    for r in results:
        print(
            f"  {r.label:<30}{r.compiles:>9}{r.purged:>8}{r.rotations:>10}{r.jit_share_pct:>9.1f}%"
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
            f"  * Aging cost (config default):            {default.aging_ms * 1000 / default.aging_steps:.1f} us/step, "
            f"{100.0 * default.aging_ms / default.time_ms:.2f}% of the run"
        )
    print("=" * 80)
    print("[PASS] JIT Card Aging benchmark completed successfully.")


if __name__ == "__main__":
    main()
