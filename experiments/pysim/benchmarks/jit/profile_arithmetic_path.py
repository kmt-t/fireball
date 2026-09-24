"""Run one arithmetic-loop execution path for native profiling and cycle counts."""

from __future__ import annotations

import argparse
import os
import sys
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
from runtime_engine import RuntimeEngine
from tier3_executer.interpreter.interpreter import Interpreter, InterpreterBindings
from tier3_executer.jit.jit_manager import JITRuntimeManager
from wasm_reader import parse

LOOP_COUNT = 100_000
PROFILE_REPETITIONS = {
    "python-handler": 1,
    "native-interpreter": 512,
    "hybrid-jit": 1200,
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
    parser.add_argument("--perf-control-fifo")
    parser.add_argument("--perf-ack-fifo")
    args = parser.parse_args()

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
    interpreter_steps = 0
    native_loop_calls = 0

    if args.path == "python-handler":
        interpreter = Interpreter(module, InterpreterBindings.empty())
        warmup_result = benchmark._run_python_interpreter(interpreter, function_index, 1_000)
        assert int(warmup_result[0]) == 499_500
        perf_stat.set_enabled(True)
        try:
            for _ in range(repetitions):
                result = benchmark._run_python_interpreter(interpreter, function_index, LOOP_COUNT)
        finally:
            perf_stat.set_enabled(False)
        observed_result = int(result[0])
    elif args.path == "native-interpreter":
        interpreter = Interpreter(module, InterpreterBindings.empty())
        warmup_result = interpreter.call(function_index, [1_000])
        assert int(warmup_result[0]) == 499_500
        arguments = [LOOP_COUNT]
        perf_stat.set_enabled(True)
        try:
            for _ in range(repetitions):
                result = interpreter.call(function_index, arguments)
        finally:
            perf_stat.set_enabled(False)
        observed_result = int(result[0])
    else:
        runtime_engine = RuntimeEngine(
            jit_runtime=JITRuntimeManager(jit_compiler=benchmark.compiler, yield_threshold=16)
        )
        runtime_engine.register_module_blocks(module)
        interpreter = Interpreter(module, InterpreterBindings.empty())
        warmup_result = runtime_engine.run(interpreter, function_index, [100])
        assert int(warmup_result[0]) == 4_950
        runtime_engine.idle_hook(budget=10)

        arguments = [LOOP_COUNT]
        perf_stat.set_enabled(True)
        try:
            for _ in range(repetitions):
                result = runtime_engine.run(interpreter, function_index, arguments)
        finally:
            perf_stat.set_enabled(False)
        observed_result = int(result[0])

        jit_invocations = runtime_engine.stat_jit_invocations
        interpreter_steps = runtime_engine.stat_interp_steps
        native_loop_calls = runtime_engine.stat_native_loop_calls
        assert jit_invocations > 0
        assert native_loop_calls > 0, "Hybrid JIT did not enter the C++ loop path"

    assert observed_result == expected_result
    print(f"profile_path={args.path}")
    print(f"iterations_per_call={LOOP_COUNT}")
    print(f"repetitions={repetitions}")
    print(f"wasm_dynamic_instructions_per_call={_dynamic_wasm_instruction_count(LOOP_COUNT)}")
    print(f"wasm_dynamic_instructions={_dynamic_wasm_instruction_count(LOOP_COUNT) * repetitions}")
    print(f"result={observed_result}")
    if args.path == "hybrid-jit":
        print(f"jit_trace_invocations={jit_invocations}")
        print(f"interpreter_steps={interpreter_steps}")
        print(f"native_loop_calls={native_loop_calls}")
    print("[PASS] arithmetic result verified")


if __name__ == "__main__":
    main()
