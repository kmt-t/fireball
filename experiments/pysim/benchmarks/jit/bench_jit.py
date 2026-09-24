"""
experiments/pysim/benchmarks/jit/bench_jit.py
JIT Compiler & Runtime Performance Benchmark.
Conforms to docs/components/tier3_executer/benchmarks/jit_runtime_bench_spec.md (BENCHMARK-JIT-01 ~ BENCHMARK-JIT-05).
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path
from statistics import median

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

import wasm_opcodes as op
from control_flow import extract_basic_blocks, iter_block_ops
from execution_context import WASMContext
from runtime_engine import RuntimeEngine
from system_containers import ReadOnlyFlatMapView, StaticVector
from tier3_executer.interpreter.interpreter import (
    Interpreter,
    InterpreterBindings,
    WasmNumber,
)
from tier3_executer.jit.jit_cache import HotspotBitmap
from tier3_executer.jit.jit_manager import JITRuntimeManager
from tier3_executer.jit.x64_jit import TraceCompiler
from wasm_module import I32, LocalWidthMap
from wasm_reader import parse


class JITCompilerBenchmark:
    """Measures Copy-and-Patch compilation latency, sparse lookup, and JIT speedup."""

    def __init__(self):
        self.compiler = TraceCompiler()

    def run_all(self, iterations: int = 100_000) -> dict[str, float | int]:
        results: dict[str, float | int] = {}

        # 3.1 Copy-and-Patch Compilation Throughput (Arithmetic Basic Block) --
        # Real WASM bytecode. The loader-owned BasicBlock is the shared
        # instruction source for both interpreter and JIT paths.
        code = bytes([op.LOCAL_GET, 0, op.I32_CONST, 1, op.I32_ADD, op.LOCAL_SET, 0])
        head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
        t0 = time.perf_counter()
        compile_count = 10_000
        for _ in range(compile_count):
            _trace = self.compiler.compile_trace(
                head_pc,
                iter_block_ops(code, head_pc & 0xFFFF, byte_span),
                next_pc,
                loops_to,
                byte_span,
                LocalWidthMap((I32,)),
            )
        t1 = time.perf_counter()
        results["jit_compile_traces_per_sec"] = compile_count / (t1 - t0)
        results["jit_compile_latency_us"] = (t1 - t0) / compile_count * 1e6
        results["jit_compile_ns_per_insn"] = (t1 - t0) / (compile_count * 4) * 1e9

        # 3.2 2-Bit Card Marking BitView O(1) Check
        bitmap = HotspotBitmap(card_shift=2, code_lengths=(256,))
        t0 = time.perf_counter()
        for i in range(iterations):
            _state = bitmap.get_state(pc=(i * 4) & 0xFC)
        t1 = time.perf_counter()
        results["card_marking_check_mops"] = iterations / (t1 - t0) / 1e6
        results["card_marking_check_ns"] = (t1 - t0) / iterations * 1e9

        # 3.3 Sparse sorted JIT entry lookup
        keys = [(idx << 16) | (idx * 16) for idx in range(64)]
        values = list(range(64))
        jit_entries = ReadOnlyFlatMapView(tuple(zip(keys, values, strict=True)))
        t0 = time.perf_counter()
        for i in range(iterations):
            pc = ((i % 64) << 16) | ((i % 64) * 16)
            _ = jit_entries.find(pc)
        t1 = time.perf_counter()
        results["jit_entry_lookup_mops"] = iterations / (t1 - t0) / 1e6
        results["jit_entry_lookup_ns"] = (t1 - t0) / iterations * 1e9

        # 3.4 Execution Throughput: Heavy Computation (100,000 Loop Iterations)
        loop_wasm = self._create_heavy_loop_binary()
        module = parse(loop_wasm)
        fn_idx = module.export_func_index("heavy_loop")
        LOOP_COUNT = 100_000

        # Keep the Python handler loop as the cross-platform reference baseline.
        # Interpreter.call() can use _interpreter_native, so measure that path
        # separately instead of silently changing the baseline when the extension
        # happens to be installed on one host.
        python_interpreter = Interpreter(module, InterpreterBindings.empty())
        python_warmup = self._run_python_interpreter(python_interpreter, fn_idx, 1_000)
        assert python_warmup[0] == 499_500

        python_times_ms: list[float] = []
        native_times_ms: list[float] = []
        jit_times_ms: list[float] = []
        python_results: list[int] = []
        native_results: list[int] = []
        jit_results: list[int] = []
        last_runtime_engine: RuntimeEngine | None = None

        for _ in range(3):
            interp_python = Interpreter(module, InterpreterBindings.empty())
            t0 = time.perf_counter()
            res_python = self._run_python_interpreter(interp_python, fn_idx, LOOP_COUNT)
            t1 = time.perf_counter()
            python_times_ms.append((t1 - t0) * 1000)
            python_results.append(int(res_python[0]))

            # Measure the Clang-built C++ threaded interpreter extension as a
            # separate baseline from the Python handlers above.
            interp_native = Interpreter(module, InterpreterBindings.empty())
            t0 = time.perf_counter()
            res_native = interp_native.call(fn_idx, [LOOP_COUNT])
            t1 = time.perf_counter()
            native_times_ms.append((t1 - t0) * 1000)
            native_results.append(int(res_native[0]))

            runtime_engine = RuntimeEngine(
                jit_runtime=JITRuntimeManager(jit_compiler=self.compiler, yield_threshold=16)
            )
            runtime_engine.register_module_blocks(module)
            interp_jit = Interpreter(module, InterpreterBindings.empty())

            # Warm up and compile the hot traces before measuring native execution.
            runtime_engine.run(interp_jit, fn_idx, [100])
            runtime_engine.idle_hook(budget=10)

            t0 = time.perf_counter()
            res_jit = runtime_engine.run(interp_jit, fn_idx, [LOOP_COUNT])
            t1 = time.perf_counter()
            jit_times_ms.append((t1 - t0) * 1000)
            jit_results.append(int(res_jit[0]))
            last_runtime_engine = runtime_engine

        assert python_results[0] == python_results[1] == python_results[2]
        assert native_results[0] == native_results[1] == native_results[2]
        assert jit_results[0] == jit_results[1] == jit_results[2]
        assert python_results[0] == native_results[0] == jit_results[0]
        assert last_runtime_engine is not None
        assert last_runtime_engine.stat_jit_invocations > 0, "JIT benchmark did not execute a trace"
        assert last_runtime_engine.stat_native_loop_calls > 0, (
            "JIT benchmark did not enter the native loop path"
        )

        python_time_ms = median(python_times_ms)
        native_time_ms = median(native_times_ms)
        jit_time_ms = median(jit_times_ms)
        results["interp_python_loop_time_ms"] = python_time_ms
        results["interp_native_loop_time_ms"] = native_time_ms
        results["jit_loop_time_ms"] = jit_time_ms
        results["jit_speedup_vs_python_ratio"] = (
            python_time_ms / jit_time_ms if jit_time_ms > 0 else 1.0
        )
        results["jit_speedup_vs_native_ratio"] = (
            native_time_ms / jit_time_ms if jit_time_ms > 0 else 1.0
        )
        results["interp_python_loop_result"] = python_results[0]
        results["interp_native_loop_result"] = native_results[0]
        results["jit_loop_result"] = jit_results[0]
        results["jit_loop_trace_invocations"] = last_runtime_engine.stat_jit_invocations
        results["jit_loop_native_calls"] = last_runtime_engine.stat_native_loop_calls
        results["jit_loop_interpreter_steps"] = last_runtime_engine.stat_interp_steps
        results["jit_loop_chain_hits"] = last_runtime_engine.stat_chain_hits

        # 3.5 PIC trace-header-owned helper tail dispatch.  This is the
        # terminal boundary used when a complex operation is implemented by C:
        # the common machine code loads the target from the trace header,
        # restores its frame, and tail-jumps.  The callback is a C ABI supplied
        # by ctypes in pysim; the measured result includes the simulator's
        # Python callback cost and must not be presented as embedded C speed.
        helper_type = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )

        def helper(
            _ctx: ctypes.c_void_p,
            _sp: ctypes.c_void_p,
            local_base: ctypes.c_void_p,
            _tos: int,
        ) -> None:
            locals_ptr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_uint32))
            locals_ptr[0] += 1

        helper_fn = helper_type(helper)
        helper_ctx = WASMContext()
        helper_ctx.locals = (0,)
        helper_addr = ctypes.cast(helper_fn, ctypes.c_void_p).value or 0
        helper_trace = self.compiler.compile_trace(
            0xF000,
            ((op.LOCAL_GET, 0), (op.LOCAL_SET, 0)),
            None,
            None,
            4,
            LocalWidthMap((I32,)),
            tail_context_helper=True,
            helper_target_addr=helper_addr,
        )
        assert helper_trace is not None
        helper_iterations = max(1, iterations // 10)
        t0 = time.perf_counter()
        for _ in range(helper_iterations):
            helper_trace.invoke(helper_ctx)
        t1 = time.perf_counter()
        assert helper_ctx.locals[0] == helper_iterations
        results["context_helper_tail_mops"] = helper_iterations / (t1 - t0) / 1e6
        results["context_helper_tail_ns"] = (t1 - t0) / helper_iterations * 1e9
        results["context_helper_tail_invocations"] = helper_iterations

        return results

    @staticmethod
    def _run_python_interpreter(
        interpreter: Interpreter, func_index: int, iteration_count: int
    ) -> StaticVector[WasmNumber]:
        """Run Python opcode handlers directly, bypassing the native step extension."""
        call_state = interpreter.start(func_index, [iteration_count])
        while not call_state.finished:
            call_state = interpreter._step(call_state, stop_at_boundary=False)
        assert call_state.trap is None, call_state.trap
        assert call_state.results is not None
        return call_state.results

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
        f"  * Sparse JIT Entry Binary Search:      {res['jit_entry_lookup_mops']:.2f} M ops/s  ({res['jit_entry_lookup_ns']:.1f} ns/lookup)"
    )
    print(
        f"  * Arithmetic Loop (100,000 iters):    Python: {res['interp_python_loop_time_ms']:.2f} ms | Native interp: {res['interp_native_loop_time_ms']:.2f} ms | JIT: {res['jit_loop_time_ms']:.2f} ms"
    )
    print(
        f"  * Differential Result Check:          Python={res['interp_python_loop_result']:,} | Native interp={res['interp_native_loop_result']:,} | JIT={res['jit_loop_result']:,} (MATCH)"
    )
    if res["jit_speedup_vs_python_ratio"] >= 1.0:
        print(
            f"  * JIT vs Python handlers:              "
            f"{res['jit_speedup_vs_python_ratio']:.2f}x faster"
        )
    else:
        print(
            f"  * JIT vs Python handlers:              "
            f"{1.0 / res['jit_speedup_vs_python_ratio']:.2f}x slower"
        )
    if res["jit_speedup_vs_native_ratio"] >= 1.0:
        print(
            f"  * JIT vs native interpreter:           "
            f"{res['jit_speedup_vs_native_ratio']:.2f}x faster"
        )
    else:
        print(
            f"  * JIT vs native interpreter:           "
            f"{1.0 / res['jit_speedup_vs_native_ratio']:.2f}x slower"
        )
    print(
        f"  * JIT Execution Coverage:             "
        f"{res['jit_loop_trace_invocations']:,} trace invocations, "
        f"{res['jit_loop_chain_hits']:,} chain hits, "
        f"{res['jit_loop_interpreter_steps']:,} interpreter steps, "
        f"{res['jit_loop_native_calls']:,} C++ loop calls"
    )
    print(
        f"  * PIC Trace-Header Helper Tail Jump:  {res['context_helper_tail_mops']:.2f} M ops/s  ({res['context_helper_tail_ns']:.1f} ns/dispatch; {res['context_helper_tail_invocations']:,} calls)"
    )
    print("=" * 80)
    print("[PASS] JIT Compiler benchmark completed successfully.")


if __name__ == "__main__":
    main()
