"""
docs/components/tier1_interface/benchmarks/low_latency_lookup_bench.py
Empirical backing for {LowLatencyLookup} (requires/requirement_list.md),
whose verification method is declared as "ベンチマーク" (Benchmark).

{LowLatencyLookup}'s claim is specifically about the sorted-array + binary-search
mechanism (flat_map_view / system_containers.md), not about ipc_router_concept.py's
registry lookup. This measures the real mechanism the claim rests on directly:
../../tier1_core/concepts/flat_view_concept.py's FlatMapView, imported rather than
reimplemented, so this cannot silently drift from the real code.
"""

import os
import sys
import time

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "tier1_core", "concepts"
    ),
)
from flat_view_concept import FlatMapView  # noqa: E402


def linear_scan(keys: list[int], values: list[int], key: int):
    for k, v in zip(keys, values):
        if k == key:
            return v
    return None


def time_lookup(fn, n_calls: int) -> float:
    start = time.perf_counter()
    for _ in range(n_calls):
        fn()
    return time.perf_counter() - start


def main() -> None:
    sizes = [1_000, 10_000, 100_000, 1_000_000]
    n_calls = 2_000
    flat_times = []
    linear_times = []
    print("N        | flat_map_view (binary search) | linear scan   | speedup")
    print("-" * 72)
    for n in sizes:
        keys = list(range(n))
        values = [k * 2 for k in keys]
        view = FlatMapView(keys, values)
        probe = keys[n // 2]  # a worst-case-depth-representative middle key
        t_flat = time_lookup(lambda: view.find(probe), n_calls)
        t_linear = time_lookup(
            lambda: linear_scan(keys, values, probe), max(1, n_calls // 20)
        )
        # linear_scan is O(n) per call and gets very slow at large n; fewer calls,
        # normalized back to a fair per-call comparison below.
        t_linear_per_call = t_linear / max(1, n_calls // 20)
        t_flat_per_call = t_flat / n_calls
        flat_times.append(t_flat_per_call)
        linear_times.append(t_linear_per_call)
        speedup = (
            t_linear_per_call / t_flat_per_call if t_flat_per_call > 0 else float("inf")
        )
        print(
            f"{n:<8} | {t_flat_per_call * 1e6:>10.3f} us              | "
            f"{t_linear_per_call * 1e6:>10.3f} us | {speedup:>6.1f}x"
        )

    # Empirical O(log N) check: going from the smallest to the largest N (a 1000x
    # increase), flat_map_view's per-call time should grow far slower than linear --
    # log2(1000) ~= 10x growth would be generous for interpreter noise; linear scan
    # over the same range grows ~1000x by construction.
    growth_flat = flat_times[-1] / flat_times[0]
    growth_linear = linear_times[-1] / linear_times[0]
    print(
        f"\n[MEASURED] {sizes[0]}->{sizes[-1]} ({sizes[-1] // sizes[0]}x more keys): "
        f"flat_map_view grew {growth_flat:.1f}x, linear scan grew {growth_linear:.1f}x"
    )
    assert growth_flat < growth_linear / 5, (
        "flat_map_view's lookup time scaled with N too closely to linear scan's -- "
        "the O(log N) claim is not supported by this measurement"
    )
    print(
        "[PASS] flat_map_view's lookup cost grows far slower than linear scan's as N grows, "
        "consistent with O(log N)."
    )


if __name__ == "__main__":
    main()
