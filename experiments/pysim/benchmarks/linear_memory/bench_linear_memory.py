"""
experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py
Linear Memory Benchmark (Guest RAM Fast Path, Single-Comparison Bounds, Widths).
Conforms to docs/components/tier2_runtime/benchmarks/linear_memory_bench_spec.md (BENCH-MEM-01 ~ BENCH-MEM-05).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent
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


class LinearMemoryBenchmark:
    """Measures Guest RAM Linear Access, Fast Bypass (Bit 31 == 0), and Bound Checks."""

    def __init__(self, ram_size: int = 65536):
        self.ram_size = ram_size
        self.ram = bytearray(ram_size)
        self.vmmio = VMMIOController(guest_ram_size=ram_size)

    def run_all(self, iterations: int = 250_000) -> dict[str, float]:
        results = {}

        # 1.1 Direct Raw RAM Access (Baseline 32-bit R/W)
        t0 = time.perf_counter()
        for i in range(iterations):
            addr = (i * 4) & (self.ram_size - 4)
            self.ram[addr : addr + 4] = b"\x12\x34\x56\x78"
            _ = self.ram[addr : addr + 4]
        t1 = time.perf_counter()
        results["raw_bytearray_mops"] = (iterations * 2) / (t1 - t0) / 1e6
        results["raw_bytearray_ns"] = (t1 - t0) / (iterations * 2) * 1e9

        # 1.2 Memory Access Widths (8-bit, 16-bit, 32-bit)
        t0 = time.perf_counter()
        for i in range(iterations):
            addr = i & (self.ram_size - 1)
            self.ram[addr] = 0x55
            _ = self.ram[addr]
        t1 = time.perf_counter()
        results["mem_8bit_mops"] = (iterations * 2) / (t1 - t0) / 1e6

        t0 = time.perf_counter()
        for i in range(iterations):
            addr = (i * 2) & (self.ram_size - 2)
            self.ram[addr : addr + 2] = b"\x12\x34"
            _ = self.ram[addr : addr + 2]
        t1 = time.perf_counter()
        results["mem_16bit_mops"] = (iterations * 2) / (t1 - t0) / 1e6

        # 1.3 Single Comparison Bound Check (CMP addr, mem_size)
        t0 = time.perf_counter()
        mem_size = self.ram_size
        for i in range(iterations):
            addr = (i * 4) & (self.ram_size - 4)
            if addr >= mem_size:
                raise ValueError("OOB")
            self.ram[addr] = 0xAA
        t1 = time.perf_counter()
        results["bound_check_mops"] = iterations / (t1 - t0) / 1e6
        results["bound_check_ns"] = (t1 - t0) / iterations * 1e9

        # 1.4 vMMIO Linear Bypass Access (Bit 31 == 0 filter + access)
        t0 = time.perf_counter()
        vmmio = self.vmmio
        for i in range(iterations):
            addr = (i * 4) & (self.ram_size - 4)
            vmmio.access(addr, is_write=True)
            _ = vmmio.access(addr, is_write=False)
        t1 = time.perf_counter()
        results["vmmio_linear_bypass_mops"] = (iterations * 2) / (t1 - t0) / 1e6
        results["vmmio_linear_bypass_ns"] = (t1 - t0) / (iterations * 2) * 1e9
        results["vmmio_linear_throughput_mb"] = (iterations * 2 * 4) / (t1 - t0) / (1024 * 1024)

        return results


def main():
    print("=" * 80)
    print("      [Benchmark 1/4] Linear Memory & Guest RAM Access Performance      ")
    print("=" * 80)
    bench = LinearMemoryBenchmark(ram_size=65536)
    res = bench.run_all(iterations=250_000)

    print(
        f"  * Raw Bytearray 32-bit R/W (Baseline): {res['raw_bytearray_mops']:.2f} M ops/s  ({res['raw_bytearray_ns']:.1f} ns/op)"
    )
    print(f"  * 8-bit Byte R/W Throughput:          {res['mem_8bit_mops']:.2f} M ops/s")
    print(f"  * 16-bit Half-Word R/W Throughput:     {res['mem_16bit_mops']:.2f} M ops/s")
    print(
        f"  * Single-CMP Bound Check Overhead:    {res['bound_check_mops']:.2f} M ops/s  ({res['bound_check_ns']:.1f} ns/op)"
    )
    print(
        f"  * vMMIO Fast Bypass (Bit 31 == 0):    {res['vmmio_linear_bypass_mops']:.2f} M ops/s  ({res['vmmio_linear_bypass_ns']:.1f} ns/op)"
    )
    print(f"  * Linear RAM Bandwidth:               {res['vmmio_linear_throughput_mb']:.2f} MB/s")
    print("=" * 80)
    print("[PASS] Linear Memory benchmark completed successfully.")


if __name__ == "__main__":
    main()
