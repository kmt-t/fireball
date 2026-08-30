"""
docs/components/tier3_jit/benchmarks/zero_runtime_overhead_bench.py
Empirical backing for {ZeroRuntimeOverhead} (requires/requirement_list.md, cited
from jit_assembler_constexpr.md), whose verification method is declared as
"ベンチマーク" (Benchmark).

"Zero-cost abstraction" here means the constexpr Thumb-2 stencil-variant machinery
(register-role selection, stack-top caching) costs ROM (extra pre-compiled variant
table entries) but not cycles: Copy-and-Patch selects the variant at compile time,
so none of that abstraction survives into the emitted instruction stream as runtime
work. This benchmark imports the real StackCachingCompiler from
../concepts/stack_cache_concept.py (the same one stack_cache_concept.py's own tests
already exercise) rather than reimplementing it, and reports the naive-vs-cached
instruction count reduction as the measured evidence, instead of only printing it
as a side effect of an unrelated test run.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "concepts")
)
from stack_cache_concept import LOOP_OPS, NAIVE, StackCachingCompiler  # noqa: E402


def main() -> None:
    listing, depth = StackCachingCompiler().compile_block(LOOP_OPS)
    naive_count = sum(NAIVE[op] for op, _ in LOOP_OPS)
    cached_count = len(listing)
    mem_ops = sum(1 for i in listing if i.split()[0] in ("PUSH", "POP"))
    reduction = 1 - (cached_count / naive_count)
    print(f"[MEASURED] workload: {len(LOOP_OPS)} WASM ops ({LOOP_OPS})")
    print(
        f"           naive (always-memory) stencils : {naive_count} native instructions"
    )
    print(
        f"           stack-cached (constexpr variant): {cached_count} native instructions "
        f"({naive_count / cached_count:.2f}x fewer)"
    )
    print(
        f"           spill/reload instructions emitted mid-block: {mem_ops} "
        f"(0 means the abstraction added no runtime memory traffic for this block)"
    )
    assert cached_count < naive_count, (
        "the variant-selecting compiler must beat the naive always-memory stencil set, "
        "or the zero-cost claim is false"
    )
    assert mem_ops == 0, (
        f"expected no mid-block spill for this workload, got {mem_ops} -- the abstraction "
        "is not actually free here"
    )
    print(
        f"[PASS] Stencil-variant selection reduced instruction count by {reduction:.0%} with "
        "zero mid-block memory traffic -- the abstraction cost is paid in ROM (variant table "
        "entries), not in emitted runtime instructions."
    )


if __name__ == "__main__":
    main()
