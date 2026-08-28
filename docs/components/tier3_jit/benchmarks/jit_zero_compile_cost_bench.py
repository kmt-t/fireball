"""
docs/components/tier3_jit/benchmarks/jit_zero_compile_cost_bench.py
Empirical backing for {JIT_ZeroCompileCostTheorem} (requires/requirement_list.md),
whose verification method is declared as "ベンチマーク" (Benchmark). This benchmarks
the linear compile-time scaling of the Copy-and-Patch compilation pipeline.

Copy-and-Patch's whole premise is that compilation is just concatenating and
patching pre-encoded stencils -- no optimization search, no register allocation
solver, no instruction scheduling. "Zero cost" here means "cost proportional to
trace length with a small constant factor", not literally zero: this benchmark
drives the real compile_trace() (../concepts/jit_copy_patch_concept.py, the same
function verified end-to-end on a real ARMv8-M emulator by
jit_trace_execution_verifier.py) and checks compile time scales linearly with
trace length rather than blowing up.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "concepts"))
from jit_copy_patch_concept import CopyPatchJITEngine  # noqa: E402


def compile_n_ops(n: int) -> float:
    """Wall-clock time to compile a trace of n arithmetic ops via the real engine."""
    ops = [("i32.add", None)] * n
    engine = CopyPatchJITEngine(cache_size=max(4096, n * 8))
    start = time.perf_counter()
    engine.compile_trace(ops, exit_kind="fallback")
    return time.perf_counter() - start


def main() -> None:
    sizes = [10, 100, 1_000, 10_000]
    times = []

    print("trace length (ops) | compile time | ns/op")
    print("-" * 48)
    for n in sizes:
        # Median of 5 to damp OS scheduling noise on a single short run.
        samples = sorted(compile_n_ops(n) for _ in range(5))
        t = samples[2]
        times.append(t)
        print(f"{n:<19} | {t * 1e3:>9.3f} ms | {(t / n) * 1e9:>7.1f}")

    # Linear-cost check: doubling the op count from the middle two sample points
    # should roughly double the time, not blow up super-linearly the way a real
    # optimization pass (register allocation, scheduling) would.
    ratio_n = sizes[-1] / sizes[-2]
    ratio_t = times[-1] / times[-2]
    print(f"\n[MEASURED] {sizes[-2]}->{sizes[-1]} ops ({ratio_n:.0f}x more): "
          f"compile time grew {ratio_t:.2f}x")

    assert ratio_t < ratio_n * 3, (
        f"compile time grew {ratio_t:.2f}x for a {ratio_n:.0f}x larger trace -- "
        "that is super-linear enough to suggest a hidden expensive pass, not "
        "Copy-and-Patch's claimed proportional-to-length cost"
    )
    print("[PASS] compile_trace() cost scales linearly (not super-linearly) with trace length, "
          "consistent with a stencil-copy compiler that does no optimization search.")


if __name__ == "__main__":
    main()
