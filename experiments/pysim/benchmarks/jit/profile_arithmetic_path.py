"""Run one arithmetic-loop execution path for native profiling and cycle counts."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve()
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from bench_jit import JITCompilerBenchmark
from config import FB_CONF_RUNTIME_YIELD_THRESHOLD
from tier3_executer.interpreter.interpreter import (
    NATIVE_RUNTIME_PROFILE_STATS_ENABLED,
    Interpreter,
    InterpreterBindings,
)
from tier3_executer.jit.jit_manager import JITRuntimeManager
from tier3_executer.jit.jit_runtime import JITInterpreter
from tier3_executer.jit.runtime_engine import RuntimeEngine
from wasm_reader import parse

LOOP_COUNT = 100_000
PROFILE_REPETITIONS = {
    "python-handler": 1,
    "native-interpreter": 512,
    "hybrid-jit": 4,
}


class PerfStatControl:
    """Enable perf stat hardware counters only around the selected call batch."""

    __slots__ = ("_ack_fifo", "_control_fifo")

    def __init__(self, control_fifo: str | None, ack_fifo: str | None) -> None:
        assert (control_fifo is None) == (ack_fifo is None)
        self._control_fifo = control_fifo
        self._ack_fifo = ack_fifo

    def set_enabled(self, enabled: bool) -> None:
        if self._control_fifo is None or self._ack_fifo is None:
            return

        ack_fd = os.open(self._ack_fifo, os.O_RDONLY)
        control_fd = os.open(self._control_fifo, os.O_WRONLY)
        try:
            command = b"enable\n" if enabled else b"disable\n"
            assert os.write(control_fd, command) == len(command)
            response = bytearray()
            while len(response) < 5:
                chunk = os.read(ack_fd, 5 - len(response))
                assert chunk
                response.extend(chunk)
            assert response == b"ack\n\x00"
        finally:
            os.close(control_fd)
            os.close(ack_fd)


def _expected_result() -> int:
    """Return the signed WebAssembly i32 sum for the shared loop input."""
    wrapped_sum = (LOOP_COUNT * (LOOP_COUNT - 1) // 2) & 0xFFFF_FFFF
    return wrapped_sum - 0x1_0000_0000 if wrapped_sum & 0x8000_0000 else wrapped_sum


def _dynamic_wasm_instruction_count(iterations: int) -> int:
    """Count executed opcodes in the fixed heavy_loop WASM fixture."""
    # Four initialization ops, block/loop, four condition ops per test,
    # nine body ops per completed iteration, and result plus function end.
    return 4 + 2 + 4 * (iterations + 1) + 9 * iterations + 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", choices=tuple(PROFILE_REPETITIONS), required=True)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument(
        "--hotspot-profiling",
        choices=("enabled", "disabled"),
        default="enabled",
        help="keep dynamic JIT hotness observation enabled during measured calls",
    )
    parser.add_argument(
        "--collect-runtime-stats",
        action="store_true",
        help="enable diagnostic counters during the measured calls",
    )
    parser.add_argument("--perf-control-fifo")
    parser.add_argument("--perf-ack-fifo")
    args = parser.parse_args()
    assert not args.collect_runtime_stats or NATIVE_RUNTIME_PROFILE_STATS_ENABLED, (
        "runtime profile stats were compiled out; rebuild with "
        "FB_CONF_RUNTIME_PROFILE_STATS=True"
    )

    repetitions = (
        args.repetitions if args.repetitions is not None else PROFILE_REPETITIONS[args.path]
    )
    assert repetitions > 0
    perf_stat = PerfStatControl(args.perf_control_fifo, args.perf_ack_fifo)

    benchmark = JITCompilerBenchmark()
    module = parse(benchmark._create_heavy_loop_binary())
    function_index = module.export_func_index("heavy_loop")
    expected_result = _expected_result()
    observed_result = 0
    jit_invocations = 0
    native_dispatch_trace_transitions = 0
    interpreter_steps = 0
    trace_exits = 0
    elapsed_ms = 0.0

    if args.path == "python-handler":
        interpreter = Interpreter(module, InterpreterBindings.empty())
        warmup_result = benchmark._run_python_interpreter(interpreter, function_index, 1_000)
        assert int(warmup_result[0]) == 499_500
        perf_stat.set_enabled(True)
        batch_start = time.perf_counter()
        try:
            for _ in range(repetitions):
                result = benchmark._run_python_interpreter(interpreter, function_index, LOOP_COUNT)
        finally:
            elapsed_ms = (time.perf_counter() - batch_start) * 1000.0
            perf_stat.set_enabled(False)
        observed_result = int(result[0])
    elif args.path == "native-interpreter":
        interpreter = Interpreter(module, InterpreterBindings.empty())
        warmup_result = interpreter.call(function_index, [1_000])
        assert int(warmup_result[0]) == 499_500
        arguments = [LOOP_COUNT]
        perf_stat.set_enabled(True)
        batch_start = time.perf_counter()
        try:
            for _ in range(repetitions):
                result = interpreter.call(function_index, arguments)
        finally:
            elapsed_ms = (time.perf_counter() - batch_start) * 1000.0
            perf_stat.set_enabled(False)
        observed_result = int(result[0])
    else:
        runtime_engine = RuntimeEngine(
            jit_runtime=JITRuntimeManager(
                jit_compiler=benchmark.compiler,
                yield_threshold=FB_CONF_RUNTIME_YIELD_THRESHOLD,
            ),
            collect_runtime_stats=args.collect_runtime_stats,
        )
        runtime_engine.register_module_blocks(module)
        interpreter = JITInterpreter(
            module, InterpreterBindings.empty(), runtime_engine
        )
        warmup_result = interpreter.call(function_index, [100])
        assert int(warmup_result[0]) == 4_950
        runtime_engine.idle_hook(budget=10)
        runtime_engine.jit_runtime.set_hotspot_profiling_enabled(
            args.hotspot_profiling == "enabled"
        )
        runtime_engine.reset_stats()

        arguments = [LOOP_COUNT]
        perf_stat.set_enabled(True)
        batch_start = time.perf_counter()
        try:
            for _ in range(repetitions):
                result = interpreter.call(function_index, arguments)
        finally:
            elapsed_ms = (time.perf_counter() - batch_start) * 1000.0
            perf_stat.set_enabled(False)
        observed_result = int(result[0])

        if args.collect_runtime_stats:
            jit_invocations = runtime_engine.stat_jit_invocations
            native_dispatch_trace_transitions = (
                runtime_engine.stat_native_dispatch_trace_transitions
            )
            interpreter_steps = runtime_engine.stat_interp_steps
            trace_exits = runtime_engine.stat_trace_exits_to_interp
            assert jit_invocations > 0
        else:
            assert runtime_engine.jit_runtime.cache.active.traces or runtime_engine.jit_runtime.cache.warm.traces

    assert observed_result == expected_result
    print(f"profile_path={args.path}")
    print(f"iterations_per_call={LOOP_COUNT}")
    print(f"repetitions={repetitions}")
    print(f"loop_backedge_yield_threshold={FB_CONF_RUNTIME_YIELD_THRESHOLD}")
    if args.path == "hybrid-jit":
        print(f"hotspot_profiling={args.hotspot_profiling}")
    print(f"wasm_dynamic_instructions_per_call={_dynamic_wasm_instruction_count(LOOP_COUNT)}")
    print(f"wasm_dynamic_instructions={_dynamic_wasm_instruction_count(LOOP_COUNT) * repetitions}")
    print(f"execution_batch_ms={elapsed_ms:.3f}")
    print(f"result={observed_result}")
    if args.path == "hybrid-jit":
        print(f"runtime_stats={'enabled' if args.collect_runtime_stats else 'disabled'}")
        if args.collect_runtime_stats:
            print(f"jit_trace_invocations={jit_invocations}")
            print(f"jit_dispatch_trace_transitions={native_dispatch_trace_transitions}")
            print(f"trace_exits_to_runtime={trace_exits}")
            print(f"interpreter_steps={interpreter_steps}")
    print("[PASS] arithmetic result verified")


if __name__ == "__main__":
    main()
