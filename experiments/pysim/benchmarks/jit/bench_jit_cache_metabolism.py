"""
experiments/pysim/benchmarks/jit/bench_jit_cache_metabolism.py
JIT Code Cache Metabolism, Hit-Rate, In-place Execution & Corner Cases Benchmark.
Conforms strictly to docs/components/tier3_plugins/benchmarks/jit_cache_metabolism_bench_spec.md (BENCHMARK-METAB-01..BENCHMARK-METAB-05).

Every trace is compiled from a loader block of a parsed module, and every cache is the
`manager.cache` of a runtime that registered that module. No synthetic traces are used.
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

import wasmtime
from bump_allocator import BumpAllocator
from config import (
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CACHE_BANK_ENTRY_CAPACITY,
)
from qa.shared.jit_cache import JITTrace
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.runtime_support import compile_module_block
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse

LOADER_BLOCK_BODY = "(block i32.const 1 drop) "
# The host benchmark parses modules of up to 5000 loader blocks; the runtime arena is not sized for it.
HOST_LOADER_ARENA_BYTES = 64 * 1024 * 1024

# Then and else arms both fall into the join block; the arms are at depth 1, the join at depth 0.
IF_ELSE_JOIN_WAT = (
    '(module (func (export "f") (param i32) (result i32) (if (result i32) (local.get 0) '
    "(then (i32.const 1) (drop) (i32.const 3)) (else (i32.const 2) (drop) (i32.const 4))) "
    "(drop) (i32.const 7)))"
)


def _loader_module(block_count: int) -> Module:
    """Parse a one-function module with `block_count` identical loader blocks."""
    wasm = wasmtime.wat2wasm(
        f'(module (func (export "f") {LOADER_BLOCK_BODY * (block_count + 1)}))'
    )
    return parse(memoryview(wasm), BumpAllocator(capacity=HOST_LOADER_ARENA_BYTES))


def _instruction_blocks(module: Module) -> tuple:
    """Return the nonempty loader blocks of `module` in code order."""
    blocks = tuple(block for block in module.blocks if block.byte_span > 0)
    assert blocks, "module has no nonempty basic blocks"
    return blocks


def _parse_wat(wat: str) -> Module:
    """Parse one WAT module with the host loader arena."""
    wasm = wasmtime.wat2wasm(wat)
    return parse(memoryview(wasm), BumpAllocator(capacity=HOST_LOADER_ARENA_BYTES))


def _bound_manager(module: Module, bank_capacity: int) -> JITRuntimeManager:
    """Return a runtime whose cache binds the loader metadata of `module`.

    The caller keeps the manager alive: its cache borrows the manager's native storage.
    """
    manager = JITRuntimeManager(bank_capacity=bank_capacity)
    manager.register_module(module)
    return manager


def _compile(compiler: TraceCompiler, module: Module, count: int) -> list[JITTrace]:
    """Compile the first `count` instruction blocks of `module` with one compiler."""
    traces = [
        compile_module_block(compiler, module, block) for block in _instruction_blocks(module)[:count]
    ]
    assert all(trace is not None for trace in traces)
    return traces


class JITCacheMetabolismBenchmark:
    """Corner-case and metabolism benchmarks for the 3-bank JIT code cache."""

    def __init__(self, bank_capacity: int = JIT_CACHE_BANK_CAPACITY_BYTES):
        assert bank_capacity == JIT_CACHE_BANK_CAPACITY_BYTES, "the benchmark uses the product bank"
        self.bank_capacity = bank_capacity

    def run_all(self) -> dict[str, float | int | bool]:
        results: dict[str, float | int | bool] = {}
        compiler = TraceCompiler()

        # ----------------------------------------------------------------------
        # 1. BENCHMARK-METAB-01: Warm and Oldest hits preserve code residency ({JIT_OldestExecuteOnly}).
        # ----------------------------------------------------------------------
        module = _loader_module(3)
        manager = _bound_manager(module, self.bank_capacity)
        cache = manager.cache
        t_oldest, t_warm, t_active = _compile(compiler, module, 3)

        # Place traces in specific banks via rotation
        cache.insert(t_oldest)  # in Active
        cache.rotate()  # t_oldest moves to Warm
        cache.insert(t_warm)  # in Active
        cache.rotate()  # t_oldest moves to Oldest, t_warm moves to Warm
        cache.insert(t_active)  # in Active

        state = (cache.resident_bytes, cache.generation)
        warm_address = t_warm.raw_addr
        oldest_address = t_oldest.raw_addr
        hit_warm = cache.lookup(t_warm.head_pc)
        assert hit_warm is t_warm and hit_warm.raw_addr == warm_address
        assert (cache.resident_bytes, cache.generation) == state
        results["warm_hit_keeps_address"] = True

        hit_oldest = cache.lookup(t_oldest.head_pc)
        assert hit_oldest is t_oldest and hit_oldest.raw_addr == oldest_address
        assert (cache.resident_bytes, cache.generation) == state
        assert cache.lookup(t_oldest.head_pc) is hit_oldest
        assert hit_oldest.raw_addr == oldest_address
        results["oldest_hit_keeps_address"] = True
        results["generation_hits_keep_code"] = True

        # ----------------------------------------------------------------------
        # 2. BENCHMARK-METAB-02: Working Set Scalability & Cache Hit-Rate
        # ----------------------------------------------------------------------
        # Benchmark 3 working set profiles:
        # A: Small (N=8 traces) <= Active bank capacity
        # B: Medium (N=24 traces) <= 3-bank total capacity (72 entries)
        # C: Large (N=100 traces) >> 3-bank total capacity (Thrashing)
        for profile_name, n_traces in [
            ("small_ws_8", 8),
            ("medium_ws_24", 24),
            ("large_ws_100", 100),
        ]:
            module = _loader_module(n_traces)
            manager = _bound_manager(module, self.bank_capacity)
            cache_bench = manager.cache
            traces = _compile(compiler, module, n_traces)
            by_pc = {trace.head_pc: trace for trace in traces}
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
                    # Refill cache on miss with the loader-compiled trace of that block
                    cache_bench.insert(by_pc[target_pc])

            t1 = time.perf_counter()
            hit_rate = (hits / access_iters) * 100.0
            results[f"{profile_name}_hit_rate_pct"] = hit_rate
            results[f"{profile_name}_lookup_mops"] = access_iters / (t1 - t0) / 1e6
            results[f"{profile_name}_evictions"] = cache_bench.evictions
        assert results["small_ws_8_hit_rate_pct"] == 100.0, "a local working set must always hit"
        assert results["medium_ws_24_hit_rate_pct"] >= 90.0, "a medium working set must hit 90%"

        # ----------------------------------------------------------------------
        # 3. BENCHMARK-METAB-03: Cache Metabolism & Eviction Throughput (Churn Test)
        # ----------------------------------------------------------------------
        # 5000 insertions cycle over 1000 loader traces. The cycle is longer than the resident
        # capacity, so each insertion finds its trace absent.
        churn_traces = 5_000
        churn_block_count = 1_000
        resident_capacity = JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_ENTRY_CAPACITY
        assert churn_block_count > resident_capacity
        module = _loader_module(churn_block_count)
        manager = _bound_manager(module, self.bank_capacity)
        cache_churn = manager.cache
        churn = _compile(compiler, module, churn_block_count)
        t0 = time.perf_counter()
        for i in range(churn_traces):
            inserted = cache_churn.insert(churn[i % churn_block_count])
            assert inserted
        t1 = time.perf_counter()

        metabolism_time_s = t1 - t0
        assert metabolism_time_s > 0
        resident = [trace for trace in churn if cache_churn.find_trace(trace.head_pc) is trace]
        # No trace leaks: every insertion either stays resident or was evicted.
        assert churn_traces == cache_churn.evictions + cache_churn.resident_count
        assert cache_churn.resident_count == len(resident) <= resident_capacity
        # Resident bytes equal the sum of the sizes of the resident traces.
        assert cache_churn.resident_bytes == sum(trace.size_bytes for trace in resident)
        eviction_rate = cache_churn.evictions / metabolism_time_s
        results["churn_total_inserted"] = churn_traces
        results["churn_total_evicted"] = cache_churn.evictions
        results["churn_resident_count"] = cache_churn.resident_count
        results["churn_eviction_rate_per_sec"] = eviction_rate
        results["churn_rotations"] = cache_churn.rotations
        assert cache_churn.rotations > 0

        # ----------------------------------------------------------------------
        # 4. BENCHMARK-METAB-04: Local Chaining & Bounded Dangling Chain Unlinking
        # ----------------------------------------------------------------------
        # The if/else join is the Oldest-candidate target; both arms fall into it.
        if_module = _parse_wat(IF_ELSE_JOIN_WAT)
        function_index = if_module.export_func_index("f")
        blocks = [
            block
            for block in if_module.blocks
            if block.func_index == function_index and block.byte_span > 0
        ]
        then_block, else_block, join_block = blocks[1], blocks[2], blocks[3]
        assert then_block.next_pc == join_block.head_pc
        assert else_block.next_pc == join_block.head_pc
        manager = _bound_manager(if_module, self.bank_capacity)
        cache_chain = manager.cache
        trace_b = compile_module_block(compiler, if_module, join_block)
        trace_a = compile_module_block(compiler, if_module, then_block)
        new_source = compile_module_block(compiler, if_module, else_block)
        assert trace_a is not None and trace_b is not None and new_source is not None

        cache_chain.insert(trace_b)
        cache_chain.rotate()  # B moves to Warm; A will survive B's purge.
        cache_chain.insert(trace_a)
        assert trace_a.chain_next == trace_b.head_pc, "Trace A must chain into Warm trace B!"

        cache_chain.rotate()  # B moves to Oldest; A moves to Warm.
        assert trace_a.chain_next == trace_b.head_pc, "Existing chains must survive Warm -> Oldest!"
        cache_chain.insert(new_source)
        assert new_source.chain_next is None, "New chains must not target Oldest!"

        cache_chain.rotate()  # B is purged; A remains resident in Oldest.
        assert cache_chain.find_trace(trace_a.head_pc) is not None
        assert trace_a.chain_next is None, "Purging B must detach surviving inbound source A!"
        assert trace_a.header.chain_target_addr == 0
        assert new_source.chain_next is None
        results["chain_unlinking_safety_passed"] = True

        # ----------------------------------------------------------------------
        # 5. BENCHMARK-METAB-05: Module-scoped Code-section PC isolation
        # ----------------------------------------------------------------------
        # Three modules with the same block layout share every head PC. Each cache must answer
        # a PC only with the trace of its own module.
        module_count = 3
        blocks_per_module = 8
        managers = []
        own_traces = []
        for _ in range(module_count):
            module = _loader_module(blocks_per_module)
            manager = _bound_manager(module, self.bank_capacity)
            traces = _compile(compiler, module, blocks_per_module)
            for trace in traces:
                inserted = manager.cache.insert(trace)
                assert inserted
            managers.append(manager)
            own_traces.append(traces)
        correct_hits = 0
        incorrect_hits = 0
        lookups = 0
        for owner, traces in enumerate(own_traces):
            for trace in traces:
                found = managers[owner].cache.lookup(trace.head_pc)
                lookups += 1
                if found is trace:
                    correct_hits += 1
                elif found is not None:
                    incorrect_hits += 1
        expected_correct = module_count * blocks_per_module
        assert lookups == expected_correct
        assert incorrect_hits == 0, "a module-scoped PC cache must not hit another module"
        assert correct_hits == expected_correct, "each module must hit its own PCs"
        results["module_pc_incorrect_hits"] = incorrect_hits
        results["module_pc_correct_hit_rate_pct"] = correct_hits / expected_correct * 100.0
        results["module_pc_isolation_passed"] = True

        return results


def main():
    print("=" * 80)
    print("      [Benchmark] JIT Cache Metabolism & Corner Cases Performance       ")
    print("=" * 80)
    bench = JITCacheMetabolismBenchmark()
    res = bench.run_all()

    print("\n[Section 1: In-place Execution ({JIT_OldestExecuteOnly})]")
    print("-" * 80)
    print(f"  * Warm Hit Keeps Code Address:        {res['warm_hit_keeps_address']}")
    print(f"  * Oldest Hit Keeps Code Address:      {res['oldest_hit_keeps_address']}")
    print(
        f"  * In-place Execution Status:          [PASS] (Passed={res['generation_hits_keep_code']})"
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
    print(f"  * Resident Traces After Churn:        {res['churn_resident_count']} traces")

    print("\n[Section 4: Safety & Multi-Module Invariants]")
    print("-" * 80)
    print(
        f"  * Dangling Chain Unlinking Safety:    [PASS] (Status={res['chain_unlinking_safety_passed']})"
    )
    print(
        f"  * Module-scoped Code-section PC:      [PASS] "
        f"(Isolation={res['module_pc_isolation_passed']}, "
        f"incorrect hits={res['module_pc_incorrect_hits']}, "
        f"correct hit rate={res['module_pc_correct_hit_rate_pct']:.2f}%)"
    )
    print("=" * 80)
    print("[PASS] JIT Cache Metabolism benchmark completed successfully.")


if __name__ == "__main__":
    main()
