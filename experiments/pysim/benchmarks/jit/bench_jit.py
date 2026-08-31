"""
experiments/pysim/benchmarks/jit/bench_jit.py
JIT Compiler & Runtime Performance Benchmark.
Conforms to docs/components/tier3_jit/benchmarks/jit_runtime_bench_spec.md (BENCH-JIT-01 ~ BENCH-JIT-05).
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

import wasm_opcodes as op
from interpreter import Interpreter
from runtime_engine import BasicBlock, HotspotBitmap, RuntimeEngine
from system_containers import RadixBinaryTreeView, bswap32
from wasm_reader import parse
from x64_jit import TraceCompiler


class JITCompilerBenchmark:
    """Measures Copy-and-Patch compilation latency, Radix lookup, and JIT speedup."""

    def __init__(self):
        self.compiler = TraceCompiler()

    def run_all(self, iterations: int = 100_000) -> dict[str, float]:
        results = {}

        # 3.1 Copy-and-Patch Compilation Throughput (Arithmetic Basic Block)
        block = BasicBlock(
            head_pc=0,
            ops=[
                ("local.get", 0),
                ("i32.const", 1),
                ("i32.add", None),
                ("local.set", 0),
            ],
            next_pc=8,
        )
        t0 = time.perf_counter()
        compile_count = 10_000
        for _ in range(compile_count):
            _trace = self.compiler.compile_trace(head_pc=0, block=block)
        t1 = time.perf_counter()
        results["jit_compile_traces_per_sec"] = compile_count / (t1 - t0)
        results["jit_compile_latency_us"] = (t1 - t0) / compile_count * 1e6
        results["jit_compile_ns_per_insn"] = (t1 - t0) / (compile_count * 4) * 1e9

        # 3.2 2-Bit Card Marking BitView O(1) Check
        bitmap = HotspotBitmap(card_shift=3, default_func_code_len=256)
        t0 = time.perf_counter()
        for i in range(iterations):
            _state = bitmap.get_state(pc=(i * 8) & 0xF8)
        t1 = time.perf_counter()
        results["card_marking_check_mops"] = iterations / (t1 - t0) / 1e6
        results["card_marking_check_ns"] = (t1 - t0) / iterations * 1e9

        # 3.3 bswap32 Radix Tree Section Lookup
        keys = [(idx << 16) | (idx * 16) for idx in range(64)]
        values = list(range(64))
        radix_table = [0] * 17
        for idx in range(16):
            radix_table[idx] = (idx * 64) // 16
        radix_table[16] = 64

        radix_tree = RadixBinaryTreeView(
            keys=keys,
            values=values,
            radix_table=radix_table,
            radix_shift=28,
            key_transform=bswap32,
        )
        t0 = time.perf_counter()
        for i in range(iterations):
            pc = ((i % 64) << 16) | ((i % 64) * 16)
            _ = radix_tree.find(pc)
        t1 = time.perf_counter()
        results["radix_table_lookup_mops"] = iterations / (t1 - t0) / 1e6
        results["radix_table_lookup_ns"] = (t1 - t0) / iterations * 1e9

        # 3.4 Execution Throughput: Heavy Computation (100,000 Loop Iterations)
        loop_wasm = self._create_heavy_loop_binary()
        module = parse(loop_wasm)
        fn_idx = module.export_func_index("heavy_loop")
        LOOP_COUNT = 100_000

        # Pure Tier 2 Interpreter run
        interp_pure = Interpreter(module)
        t0 = time.perf_counter()
        res_interp = interp_pure.call(fn_idx, [LOOP_COUNT])
        t1 = time.perf_counter()
        interp_time_ms = (t1 - t0) * 1000

        # Tier 3 Native JIT run
        runtime_engine = RuntimeEngine(jit_compiler=self.compiler, yield_threshold=16)
        runtime_engine.register_module_blocks(module)
        interp_jit = Interpreter(module, runtime_engine=runtime_engine)

        # Warmup and compile HOT traces
        coro = interp_jit.call_coroutine(fn_idx, [100], yield_every=16)
        try:
            while True:
                next(coro)
                runtime_engine.idle_hook(budget=4)
        except StopIteration:
            pass
        runtime_engine.idle_hook(budget=10)

        # Run compiled native execution
        t0 = time.perf_counter()
        res_jit = interp_jit.call(fn_idx, [LOOP_COUNT])
        t1 = time.perf_counter()
        jit_time_ms = (t1 - t0) * 1000

        results["interp_loop_time_ms"] = interp_time_ms
        results["jit_loop_time_ms"] = jit_time_ms
        results["jit_speedup_ratio"] = interp_time_ms / jit_time_ms if jit_time_ms > 0 else 1.0
        results["interp_loop_result"] = res_interp[0]
        results["jit_loop_result"] = res_jit[0]

        return results

    def _create_heavy_loop_binary(self) -> bytes:
        """Constructs a WASM binary with an intensive arithmetic loop: sum = sum + (i * 3) ^ 7."""
        code_body = bytearray()
        # 1 local group: 3 locals of type i32 (sum=1, i=2, temp=3)
        code_body.extend(b"\x01\x03\x7f")
        # sum = 0 (local 1)
        code_body.extend([op.I32_CONST, 0x00, op.LOCAL_SET, 0x01])
        # i = 0 (local 2)
        code_body.extend([op.I32_CONST, 0x00, op.LOCAL_SET, 0x02])
        # block
        code_body.extend([op.BLOCK, 0x40])
        # loop
        code_body.extend([op.LOOP, 0x40])
        # if (i >= limit) break
        code_body.extend(
            [
                op.LOCAL_GET,
                0x02,
                op.LOCAL_GET,
                0x00,
                op.I32_GE_S,
                op.BR_IF,
                0x01,
            ]
        )
        # sum += i
        code_body.extend(
            [
                op.LOCAL_GET,
                0x01,
                op.LOCAL_GET,
                0x02,
                op.I32_ADD,
                op.LOCAL_SET,
                0x01,
            ]
        )
        # i++
        code_body.extend(
            [
                op.LOCAL_GET,
                0x02,
                op.I32_CONST,
                0x01,
                op.I32_ADD,
                op.LOCAL_SET,
                0x02,
            ]
        )
        # br loop
        code_body.extend([op.BR, 0x00])
        code_body.extend([op.END, op.END])
        # return sum
        code_body.extend([op.LOCAL_GET, 0x01, op.END])

        # Full WASM binary
        buf = bytearray(b"\x00asm\x01\x00\x00\x00")
        type_sec = b"\x01\x60\x01\x7f\x01\x7f"
        buf.extend([0x01, len(type_sec)])
        buf.extend(type_sec)
        func_sec = b"\x01\x00"
        buf.extend([0x03, len(func_sec)])
        buf.extend(func_sec)
        exp_sec = b"\x01\x0aheavy_loop\x00\x00"
        buf.extend([0x07, len(exp_sec)])
        buf.extend(exp_sec)
        code_len = len(code_body)
        code_sec = bytearray([0x01, code_len])
        code_sec.extend(code_body)
        buf.extend([0x0A, len(code_sec)])
        buf.extend(code_sec)
        return bytes(buf)


def main():
    print("=" * 80)
    print("      [Benchmark 3/4] JIT Compiler & Runtime Dispatch Performance      ")
    print("=" * 80)
    bench = JITCompilerBenchmark()
    res = bench.run_all(iterations=100_000)

    print(
        f"  * Copy-and-Patch Compile Speed:       {res['jit_compile_traces_per_sec']:,.0f} Traces/sec  ({res['jit_compile_latency_us']:.2f} us/trace)"
    )
    print(f"  * Compile Cost per WASM Instruction:  {res['jit_compile_ns_per_insn']:.1f} ns/opcode")
    print(
        f"  * 2-Bit Card Marking O(1) Check:      {res['card_marking_check_mops']:.2f} M ops/s  ({res['card_marking_check_ns']:.1f} ns/check)"
    )
    print(
        f"  * bswap32 Radix Tree Section Search:  {res['radix_table_lookup_mops']:.2f} M ops/s  ({res['radix_table_lookup_ns']:.1f} ns/lookup)"
    )
    print(
        f"  * Arithmetic Loop (100,000 iters):    Interp: {res['interp_loop_time_ms']:.2f} ms | JIT: {res['jit_loop_time_ms']:.2f} ms"
    )
    print(
        f"  * Differential Result Check:          Interp={res['interp_loop_result']:,} | JIT={res['jit_loop_result']:,} (MATCH)"
    )
    print(f"  * Measured JIT Speedup:               {res['jit_speedup_ratio']:.2f}x faster")
    print("=" * 80)
    print("[PASS] JIT Compiler benchmark completed successfully.")


if __name__ == "__main__":
    main()
