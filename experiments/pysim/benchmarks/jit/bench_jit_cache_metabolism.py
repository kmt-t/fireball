"""
experiments/pysim/benchmarks/jit/bench_jit_cache_metabolism.py
JIT Code Cache Metabolism, Hit-Rate, Oldest-Only Promotion & Corner Cases Benchmark.
Conforms strictly to docs/components/tier3_plugins/benchmarks/jit_cache_metabolism_bench_spec.md (BENCHMARK-METAB-01 ~ BENCHMARK-METAB-05).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from qa.shared.jit_cache import (
    JitRuntimeBoundary,
    JITTrace,
)


class JITCacheMetabolismBenchmark:
    """Corner-case and metabolism benchmarks for the 3-bank JIT code cache."""

    def __init__(self, bank_capacity: int = 1024):
        # 1024 bytes per bank (holds ~16 traces of 64 bytes each)
        self.bank_capacity = bank_capacity

    def run_all(self) -> dict[str, float | int | bool]:
        results = {}

        # ----------------------------------------------------------------------
        # 1. Corner Case 1: Oldest-Only Promotion ({JIT_OldestOnly_Promote})
        # ----------------------------------------------------------------------
        cache = JitRuntimeBoundary(bank_capacity=self.bank_capacity)
        t_active = JITTrace(head_pc=0x1000, size_bytes=64)
        t_warm = JITTrace(head_pc=0x2000, size_bytes=64)
        t_oldest = JITTrace(head_pc=0x3000, size_bytes=64)

        # Place traces in specific banks via rotation
        cache.insert(t_oldest)  # in Active
        cache.rotate()  # t_oldest moves to Warm
        cache.insert(t_warm)  # in Active
        cache.rotate()  # t_oldest moves to Oldest, t_warm moves to Warm
        cache.insert(t_active)  # in Active

        # Warm hit test: should hit with 0 promotions
        prom_before = cache.promotions
        hit_warm = cache.lookup(0x2000)
        prom_after_warm = cache.promotions
        assert hit_warm is not None
        assert prom_after_warm == prom_before, "Warm hit must NEVER trigger promotion!"
        results["warm_hit_promotions"] = prom_after_warm - prom_before

        # Oldest hit test: should hit and trigger immediate promotion to Active bank
        hit_oldest = cache.lookup(0x3000)
        prom_after_oldest = cache.promotions
        assert hit_oldest is not None
        assert prom_after_oldest == prom_before + 1, "Oldest hit MUST trigger promotion!"
        assert cache.lookup(0x3000) is hit_oldest
        assert cache.promotions == prom_after_oldest, "promotion must not repeat"
        results["oldest_hit_promotions"] = prom_after_oldest - prom_after_warm
        results["oldest_only_promote_passed"] = True

        # ----------------------------------------------------------------------
        # 2. Corner Case 2: Working Set Scalability & Cache Hit-Rate
        # ----------------------------------------------------------------------
        # Benchmark 3 working set profiles:
        # A: Small (N=8 traces) <= Active bank capacity
        # B: Medium (N=24 traces) <= 3-bank total capacity (48 traces)
        # C: Large (N=100 traces) >> 3-bank total capacity (Thrashing)
        for profile_name, n_traces in [
            ("small_ws_8", 8),
            ("medium_ws_24", 24),
            ("large_ws_100", 100),
        ]:
            cache_bench = JitRuntimeBoundary(bank_capacity=self.bank_capacity)
            traces = [JITTrace(head_pc=0x1000 + i * 16, size_bytes=64) for i in range(n_traces)]
            # Preload traces
            for t in traces:
                cache_bench.insert(t)

            # Access pattern: 80% of accesses hit top 20% hot traces (Zipfian/Hot-loop), 20% uniform
            access_iters = 50_000
            hits = 0
            misses = 0
            hot_count = max(1, n_traces // 5)

            t0 = time.perf_counter()
            for i in range(access_iters):
                if (i % 10) < 8:
                    # Hot access
                    target_pc = traces[i % hot_count].head_pc
                else:
                    # Cold/Uniform access
                    target_pc = traces[i % n_traces].head_pc

                found = cache_bench.lookup(target_pc)
                if found is not None:
                    hits += 1
                else:
                    misses += 1
                    # Refill cache on miss
                    cache_bench.insert(JITTrace(head_pc=target_pc, size_bytes=64))

            t1 = time.perf_counter()
            hit_rate = (hits / access_iters) * 100.0
            results[f"{profile_name}_hit_rate_pct"] = hit_rate
            results[f"{profile_name}_lookup_mops"] = access_iters / (t1 - t0) / 1e6
            results[f"{profile_name}_evictions"] = cache_bench.evictions

        # ----------------------------------------------------------------------
        # 3. Corner Case 3: Cache Metabolism & Eviction Throughput (Churn Test)
        # ----------------------------------------------------------------------
        cache_churn = JitRuntimeBoundary(bank_capacity=512)  # small 512B banks (~8 traces/bank)
        churn_traces = 5_000
        t0 = time.perf_counter()
        for i in range(churn_traces):
            t = JITTrace(head_pc=0x5000 + i * 16, size_bytes=64)
            cache_churn.insert(t)
        t1 = time.perf_counter()

        metabolism_time_s = t1 - t0
        eviction_rate = cache_churn.evictions / metabolism_time_s if metabolism_time_s > 0 else 0
        results["churn_total_inserted"] = churn_traces
        results["churn_total_evicted"] = cache_churn.evictions
        results["churn_eviction_rate_per_sec"] = eviction_rate
        results["churn_rotations"] = cache_churn.rotations

        # ----------------------------------------------------------------------
        # 4. Corner Case 4: Local Chaining & Bounded Dangling Chain Unlinking
        # ----------------------------------------------------------------------
        cache_chain = JitRuntimeBoundary(bank_capacity=256)
        # Trace A (head 0x100) chains into Trace B (head 0x200)
        trace_b = JITTrace(head_pc=0x200, size_bytes=64)
        trace_a = JITTrace(head_pc=0x100, size_bytes=64, next_pc=0x200)

        cache_chain.insert(trace_b)
        cache_chain.rotate()  # B moves to Warm; A will survive B's purge.
        cache_chain.insert(trace_a)
        assert trace_a.chain_next == 0x200, "Trace A must chain into Warm trace B!"

        cache_chain.rotate()  # B moves to Oldest; A moves to Warm.
        assert trace_a.chain_next == 0x200, "Existing chains must survive Warm -> Oldest!"
        new_source = JITTrace(head_pc=0x300, size_bytes=64, next_pc=0x200)
        cache_chain.insert(new_source)
        assert new_source.chain_next is None, "New chains must not target Oldest!"

        cache_chain.rotate()  # B is purged; A remains resident in Oldest.
        assert cache_chain.find_trace(0x100) is not None
        assert trace_a.chain_next is None, "Purging B must detach surviving inbound source A!"
        assert trace_a.header.chain_target_addr == 0
        assert new_source.chain_next is None
        results["chain_unlinking_safety_passed"] = True

        # ----------------------------------------------------------------------
        # 5. Corner Case 5: Module-scoped Code-section PC isolation
        # ----------------------------------------------------------------------
        module_pc = 0x0010
        cache_module0 = JitRuntimeBoundary(bank_capacity=self.bank_capacity)
        cache_module1 = JitRuntimeBoundary(bank_capacity=self.bank_capacity)
        trace_module0 = JITTrace(head_pc=module_pc, size_bytes=64)
        trace_module1 = JITTrace(head_pc=module_pc, size_bytes=64)

        cache_module0.insert(trace_module0)
        cache_module1.insert(trace_module1)

        result_module0 = cache_module0.lookup(module_pc)
        result_module1 = cache_module1.lookup(module_pc)

        assert result_module0 is trace_module0
        assert result_module1 is trace_module1
        assert result_module0 is not result_module1, "module-scoped PC caches must be isolated"
        results["module_pc_isolation_passed"] = True

        return results


def main():
    print("=" * 80)
    print("      [Benchmark] JIT Cache Metabolism & Corner Cases Performance       ")
    print("=" * 80)
    bench = JITCacheMetabolismBenchmark(bank_capacity=1024)
    res = bench.run_all()

    print("\n[Section 1: Oldest-Only Promotion Invariant ({JIT_OldestOnly_Promote})]")
    print("-" * 80)
    print(
        f"  * Warm Bank Hit Promotions:           {res['warm_hit_promotions']} (Zero-copy invariant verified)"
    )
    print(
        f"  * Oldest Bank Hit Promotions:         {res['oldest_hit_promotions']} (Promoted to Active bank on demand)"
    )
    print(
        f"  * Promotion Invariant Status:         [PASS] (Passed={res['oldest_only_promote_passed']})"
    )

    print("\n[Section 2: Working Set Scalability & Cache Hit-Rates]")
    print("-" * 80)
    print(
        f"  * Small Working Set (N=8 <= Active):  Hit Rate = {res['small_ws_8_hit_rate_pct']:.2f}%  ({res['small_ws_8_lookup_mops']:.2f} M lookups/s)"
    )
    print(
        f"  * Medium Working Set (N=24 <= 3Bank): Hit Rate = {res['medium_ws_24_hit_rate_pct']:.2f}%  ({res['medium_ws_24_lookup_mops']:.2f} M lookups/s)"
    )
    print(
        f"  * Large Working Set (N=100 Thrash):   Hit Rate = {res['large_ws_100_hit_rate_pct']:.2f}%  ({res['large_ws_100_lookup_mops']:.2f} M lookups/s)"
    )

    print("\n[Section 3: Cache Metabolism & Churn Dynamics]")
    print("-" * 80)
    print(
        f"  * Total Traces Churned:               {res['churn_total_inserted']:,} traces inserted"
    )
    print(f"  * Total Clean Evictions:              {res['churn_total_evicted']:,} traces purged")
    print(
        f"  * Cache Metabolism Rate:              {res['churn_eviction_rate_per_sec']:,.0f} Evictions / Sec"
    )
    print(f"  * Total Cache Rotations:              {res['churn_rotations']} generations")

    print("\n[Section 4: Safety & Multi-Module Invariants]")
    print("-" * 80)
    print(
        f"  * Dangling Chain Unlinking Safety:    [PASS] (Status={res['chain_unlinking_safety_passed']})"
    )
    print(
        f"  * Module-scoped Code-section PC:      [PASS] "
        f"(Isolation={res['module_pc_isolation_passed']})"
    )
    print("=" * 80)
    print("[PASS] JIT Cache Metabolism benchmark completed successfully.")


if __name__ == "__main__":
    main()
