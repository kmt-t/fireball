"""Compare warmed x64 JIT runtime for baseline and fused local-pair stencils."""

from __future__ import annotations

import argparse
import ctypes
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parents[4]
PYSIM_DIR = REPO_ROOT / "experiments/pysim"
BENCHMARK_DIR = PYSIM_DIR / "benchmarks"
LOOP_ITERATIONS = 250_000
WARMUP_ITERATIONS = 2_000
WARMUP_CALLS = 3
SAMPLES = 9
INTERPRETER_YIELD_THRESHOLD = 64
LOOP_WAT = """
(module
  (func (export "sum") (param i32) (result i32) (local i32 i32)
    i32.const 0 local.set 1
    i32.const 0 local.set 2
    block
      loop
        local.get 2 local.get 1 i32.add local.set 2
        local.get 1 i32.const 1 i32.add local.set 1
        local.get 1 local.get 0 i32.lt_u br_if 0
      end
    end
    local.get 2))
"""


def expected_sum(iterations: int) -> int:
    return (iterations * (iterations - 1) // 2) & 0xFFFF_FFFF


def run_worker(compiler_library: Path, iterations: int, warmup_calls: int) -> None:
    original_pydll = ctypes.PyDLL

    def selected_pydll(
        name: str | os.PathLike[str] | None,
        mode: int = ctypes.DEFAULT_MODE,
        handle: int | None = None,
        use_errno: bool = False,
        use_last_error: bool = False,
    ):
        if name is not None and Path(name).name in ("libjit_probe.so", "libtrace_compiler.so"):
            name = compiler_library
        return original_pydll(
            name,
            mode=mode,
            handle=handle,
            use_errno=use_errno,
            use_last_error=use_last_error,
        )

    ctypes.PyDLL = selected_pydll
    sys.path.insert(0, str(BENCHMARK_DIR))
    from _bootstrap import configure_import_paths

    configure_import_paths(PYSIM_DIR, BENCHMARK_DIR)

    from qa.shared.helpers import wat_to_wasm
    from qa.shared.runtime_support import make_runtime_engine
    from qa.shared.x64_jit import TraceCompiler
    from system_containers import StaticVector
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
    from tier2_runtime.wasm.reader import parse

    module = parse(memoryview(wat_to_wasm(LOOP_WAT)))
    function_index = module.export_func_index("sum")
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    bindings = InterpreterBindings.with_memory_and_functions(
        bytearray(65536), StaticVector(capacity=0)
    )
    interpreter = NativeInterpreter(
        module,
        bindings,
        bump_allocator=engine.bump_allocator,
        yield_threshold=INTERPRETER_YIELD_THRESHOLD,
    )
    expected = expected_sum(iterations)

    for _ in range(warmup_calls):
        warmup_result = engine.call(interpreter, function_index, [WARMUP_ITERATIONS])
        assert int(warmup_result[0]) == expected_sum(WARMUP_ITERATIONS)

    loop_block = next(block for block in module.blocks if block.loops_to is not None)
    assert engine.jit_runtime is not None
    loop_trace = engine.jit_runtime.cache.find_trace(loop_block.head_pc)
    assert loop_trace is not None, "warmup did not compile the hot loop"
    loop_trace_before = loop_trace.size_bytes
    assert loop_trace_before > 0
    assert loop_trace.raw_addr is not None

    engine.reset_stats()
    start_ns = time.perf_counter_ns()
    result = engine.call(interpreter, function_index, [iterations])
    elapsed_ns = time.perf_counter_ns() - start_ns
    assert int(result[0]) == expected
    loop_trace = engine.jit_runtime.cache.find_trace(loop_block.head_pc)
    assert loop_trace is not None, "timed call lost the compiled hot loop"
    loop_trace_after = loop_trace.size_bytes
    assert loop_trace_after > 0
    assert loop_trace.raw_addr is not None
    assert loop_trace_after == loop_trace_before, "timed call compiled the hot loop"
    print(
        f"RESULT elapsed_ns={elapsed_ns} jit_invocations={engine.stat_jit_invocations} "
        f"loop_trace_bytes={loop_trace_after} result={int(result[0])}"
    )


def run_worker_subprocess(
    compiler_library: Path,
    iterations: int,
    warmup_calls: int,
) -> tuple[int, int, int, int]:
    output = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--compiler-library",
            str(compiler_library.resolve()),
            "--iterations",
            str(iterations),
            "--warmup-calls",
            str(warmup_calls),
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
    ).stdout
    result_line = next(line for line in output.splitlines() if line.startswith("RESULT "))
    values: dict[str, int] = {}
    for field in result_line.split()[1:]:
        key, value = field.split("=", maxsplit=1)
        values[key] = int(value)
    return (
        values["elapsed_ns"],
        values["jit_invocations"],
        values["loop_trace_bytes"],
        values["result"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler-library", type=Path)
    parser.add_argument("--baseline-library", type=Path)
    parser.add_argument("--iterations", type=int, default=LOOP_ITERATIONS)
    parser.add_argument("--warmup-calls", type=int, default=WARMUP_CALLS)
    parser.add_argument("--samples", type=int, default=SAMPLES)
    parser.add_argument("--worker", action="store_true")
    arguments = parser.parse_args()

    if arguments.worker:
        assert arguments.compiler_library is not None
        run_worker(
            arguments.compiler_library,
            arguments.iterations,
            arguments.warmup_calls,
        )
        return

    assert arguments.compiler_library is not None
    assert arguments.baseline_library is not None
    assert arguments.iterations > 0 and arguments.samples > 0 and arguments.warmup_calls > 0
    measurements: dict[str, list[int]] = {"baseline": [], "candidate": []}
    counters: dict[str, list[tuple[int, int, int]]] = {"baseline": [], "candidate": []}
    libraries = {
        "baseline": arguments.baseline_library,
        "candidate": arguments.compiler_library,
    }
    for sample in range(arguments.samples):
        order = ("baseline", "candidate") if sample % 2 == 0 else ("candidate", "baseline")
        for variant in order:
            elapsed_ns, invocations, trace_bytes, result = run_worker_subprocess(
                libraries[variant], arguments.iterations, arguments.warmup_calls
            )
            assert result == expected_sum(arguments.iterations)
            measurements[variant].append(elapsed_ns)
            counters[variant].append((invocations, trace_bytes, result))

    print(f"host={platform.machine()} iterations_per_call={arguments.iterations}")
    print(f"samples={arguments.samples} warmup_calls={arguments.warmup_calls}")
    print(f"interpreter_yield_threshold={INTERPRETER_YIELD_THRESHOLD}")
    print(f"baseline_library={arguments.baseline_library.resolve()}")
    print(f"candidate_library={arguments.compiler_library.resolve()}")
    print("sample baseline_ms candidate_ms")
    for index in range(arguments.samples):
        baseline_ms = measurements["baseline"][index] / 1_000_000
        candidate_ms = measurements["candidate"][index] / 1_000_000
        print(f"{index + 1:6d} {baseline_ms:10.3f} {candidate_ms:12.3f}")

    summaries: dict[str, tuple[float, float, float]] = {}
    for variant in ("baseline", "candidate"):
        milliseconds = [elapsed / 1_000_000 for elapsed in measurements[variant]]
        summaries[variant] = (median(milliseconds), min(milliseconds), max(milliseconds))
        invocation_counts = [record[0] for record in counters[variant]]
        trace_sizes = [record[1] for record in counters[variant]]
        assert len(set(invocation_counts)) == 1
        assert len(set(trace_sizes)) == 1
        print(
            f"{variant}_median_ms={summaries[variant][0]:.3f} "
            f"range_ms={summaries[variant][1]:.3f}..{summaries[variant][2]:.3f} "
            f"jit_invocations={invocation_counts[0]} loop_trace_bytes={trace_sizes[0]}"
        )

    assert counters["candidate"][0][1] > 0, "candidate did not compile the hot loop"

    baseline_median = summaries["baseline"][0]
    candidate_median = summaries["candidate"][0]
    faster_percent = (baseline_median - candidate_median) / baseline_median * 100
    print(f"candidate_speedup_percent={faster_percent:.2f}")
    print(
        "Timing covers a warmed native WASM JIT call on this x64 host; module loading, "
        "trace compilation and warmup are outside the timed interval."
    )


if __name__ == "__main__":
    main()
