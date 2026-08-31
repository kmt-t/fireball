"""
experiments/pysim/benchmarks/vmmio/bench_vmmio.py
vMMIO Address Translation & Software TLB Benchmark.
Conforms to docs/components/tier2_runtime/benchmarks/vmmio_bench_spec.md (BENCH-VMMIO-01 ~ BENCH-VMMIO-06).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[2]
for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from vmmio import VMMIOController


class VMMIOBenchmark:
    """Measures TLB Hit O(1), FlatMap Walk O(log N), and FC=0xC/0xE/0xF dispatch."""

    def __init__(self):
        self.vmmio = VMMIOController(guest_ram_size=65536)
        # Register static device (FC=0xC)
        self.device_writes = 0

        def dummy_dev(addr, val, is_write):
            if is_write:
                self.device_writes += 1

        self.vmmio.map_static_device(vpn=0xC0000, handler=dummy_dev)

        # Register SHM pages (FC=0xE)
        for i in range(32):
            self.vmmio.map_shm_page(vpn=0xE0000 + i, phys_page=0x1000 + i, owner_id=1)

        # Register Passthrough pages (FC=0xF)
        for i in range(16):
            self.vmmio.map_passthrough_page(vpn=0xF0000 + i, phys_page=0x2000 + i)

    def run_all(self, iterations: int = 150_000) -> dict[str, float]:
        results = {}

        # 2.1 Direct-Mapped TLB Hit (O(1) Folding XOR Hash)
        # Access the same page repeatedly to guarantee 100% TLB Hit rate
        shm_addr = 0xE000_0010
        t0 = time.perf_counter()
        vmmio = self.vmmio
        for _ in range(iterations):
            _ = vmmio.access(shm_addr, is_write=False, current_task_id=1)
        t1 = time.perf_counter()
        results["tlb_hit_mops"] = iterations / (t1 - t0) / 1e6
        results["tlb_hit_latency_ns"] = (t1 - t0) / iterations * 1e9

        # 2.2 Folding XOR Hash Computation Only
        t0 = time.perf_counter()
        for i in range(iterations):
            vpn = 0xE0000 + (i & 0xFF)
            _idx = (vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15
        t1 = time.perf_counter()
        results["folding_xor_hash_mops"] = iterations / (t1 - t0) / 1e6
        results["folding_xor_hash_ns"] = (t1 - t0) / iterations * 1e9

        # 2.3 TLB Miss -> FlatMap Walk (O(log N) Lookup & Refill)
        # Cycle through 32 different pages to exceed 16-entry TLB capacity and induce misses
        t0 = time.perf_counter()
        for i in range(iterations):
            addr = 0xE000_0000 + ((i % 32) << 12)
            _ = vmmio.access(addr, is_write=False, current_task_id=1)
        t1 = time.perf_counter()
        results["tlb_miss_flatmap_mops"] = iterations / (t1 - t0) / 1e6
        results["tlb_miss_flatmap_ns"] = (t1 - t0) / iterations * 1e9

        # 2.4 Static Device Syscall Dispatch (FC=0xC)
        dev_addr = 0xC000_0000
        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = vmmio.access(dev_addr, is_write=True)
        t1 = time.perf_counter()
        results["static_device_dispatch_mops"] = iterations / (t1 - t0) / 1e6
        results["static_device_dispatch_ns"] = (t1 - t0) / iterations * 1e9

        # 2.5 Security Isolation Check (TRAP_OWNER_MISMATCH detection)
        mismatch_traps = 0
        t0 = time.perf_counter()
        for _ in range(iterations):
            res, _ = vmmio.access(shm_addr, is_write=False, current_task_id=2)
            if res == "TRAP_OWNER_MISMATCH":
                mismatch_traps += 1
        t1 = time.perf_counter()
        results["rbac_isolation_check_mops"] = iterations / (t1 - t0) / 1e6
        results["rbac_isolation_check_ns"] = (t1 - t0) / iterations * 1e9

        return results


def main():
    print("=" * 80)
    print("      [Benchmark 2/4] vMMIO Virtual Devices & Address Translation      ")
    print("=" * 80)
    bench = VMMIOBenchmark()
    res = bench.run_all(iterations=150_000)

    print(
        f"  * Direct-Mapped TLB Hit (O(1)):       {res['tlb_hit_mops']:.2f} M ops/s  ({res['tlb_hit_latency_ns']:.1f} ns/hit)"
    )
    print(
        f"  * Folding XOR Hash Calculation:       {res['folding_xor_hash_mops']:.2f} M ops/s  ({res['folding_xor_hash_ns']:.1f} ns/op)"
    )
    print(
        f"  * TLB Miss -> FlatMap Walk (O(logN)): {res['tlb_miss_flatmap_mops']:.2f} M ops/s  ({res['tlb_miss_flatmap_ns']:.1f} ns/walk)"
    )
    print(
        f"  * TLB Hit Acceleration Ratio:         {res['tlb_hit_mops'] / res['tlb_miss_flatmap_mops']:.2f}x faster than FlatMap walk"
    )
    print(
        f"  * Static Syscall Dispatch (FC=0xC):   {res['static_device_dispatch_mops']:.2f} M ops/s  ({res['static_device_dispatch_ns']:.1f} ns/dispatch)"
    )
    print(
        f"  * RBAC Task Isolation Verification:   {res['rbac_isolation_check_mops']:.2f} M ops/s  ({res['rbac_isolation_check_ns']:.1f} ns/check)"
    )
    print("=" * 80)
    print("[PASS] vMMIO benchmark completed successfully.")


if __name__ == "__main__":
    main()
