"""Measure QA-only JIT chain construction and runtime exit reasons."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[4]
_REPO_ROOT = _PYSIM_DIR.parents[1]
_BENCH_DIR = _PYSIM_DIR / "benchmarks"
for import_path in (_BENCH_DIR, _BENCH_DIR / "jit", _BENCH_DIR / "profile"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from qa.private.jit_native_abi import RUNTIME_PRINT, RUNTIME_RESET_CHAIN_MEASUREMENTS
from qa.shared.jit_abi import make_qa_runtime_dispatch_entry
from qa.shared.jit_manager import JITRuntimeManager as QaJitRuntimeManager
from qa.shared.runtime_stats import RuntimeStatsEngine
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.wasm.reader import parse

_REASONS = (
    "linked",
    "backedge",
    "self_loop",
    "loop_target",
    "result_words",
    "oldest_bank",
    "frame_depth_mismatch",
    "successor_not_resident",
    "successor_block_index_mismatch",
    "unsupported_control_flow",
)


class ChainMeasurementEngine(RuntimeStatsEngine):
    """Use the QA dispatcher extension while retaining real native JIT execution."""

    def _activate_interpreter(self, interp: NativeInterpreter) -> None:
        super()._activate_interpreter(interp)
        interp._native_dispatcher = make_qa_runtime_dispatch_entry(self.collect_runtime_stats)


def _new_engine(
    module,
    manager: QaJitRuntimeManager,
    bindings: InterpreterBindings | None = None,
) -> tuple[ChainMeasurementEngine, NativeInterpreter]:
    engine = ChainMeasurementEngine(
        jit_runtime=manager,
        bump_allocator=module.allocator,
        collect_runtime_stats=True,
    )
    engine.register_module_blocks(module)
    interpreter = NativeInterpreter(
        module,
        bindings if bindings is not None else InterpreterBindings.empty(),
        bump_allocator=engine.bump_allocator,
        execution_plugin=manager,
    )
    return engine, interpreter


def _new_manager() -> QaJitRuntimeManager:
    return QaJitRuntimeManager(jit_compiler=TraceCompiler())


def _run_metadata() -> dict[str, object]:
    source_paths = (
        "experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx",
        "experiments/pysim/native/tier3_plugins/jit/jit_runtime.hxx",
        "experiments/pysim/native/tier3_plugins/jit/jit_chain_diagnostics.hxx",
        "experiments/pysim/qa/private/jit_measurements.hxx",
        "experiments/pysim/qa/private/native_jit_probe.cxx",
        "experiments/pysim/qa/private/jit_native_abi.py",
        "experiments/pysim/qa/shared/jit_abi.py",
        "experiments/pysim/benchmarks/results/20261014/req020_phase4/measure_chain_reasons.py",
    )
    source_hashes = {
        path: hashlib.sha256((_REPO_ROOT / path).read_bytes()).hexdigest() for path in source_paths
    }
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "baseline_head": head,
        "measurement_architecture": platform.machine(),
        "qa_build": "Clang C++23 -O3 -flto=thin --qa --block-counters",
        "source_sha256": source_hashes,
    }


def _read_measurements(manager: QaJitRuntimeManager) -> dict[str, int]:
    from tier2_runtime.observability.logger import LogDictionary
    from tier3_platform.drivers.printk import PrintkBuffer, PrintkSink

    capture = PrintkBuffer()
    sink = PrintkSink(capture, LogDictionary().decode_record)
    assert manager._native.pointer is not None
    printed = RUNTIME_PRINT(manager._native.pointer, ctypes.byref(sink._raw_writer))
    assert printed == 1
    values: dict[str, int] = {}
    for line in capture.drain_output().decode().splitlines():
        key, separator, raw_value = line.partition("=")
        if separator and key.startswith("jit.chain_"):
            values[key.removeprefix("jit.")] = int(raw_value)
    expected = {
        "chain_construction_attempts",
        "chain_runtime_transitions",
        "chain_runtime_unclassified",
        "chain_runtime_unobserved_trace_exits",
        *(f"chain_construction.{reason}" for reason in _REASONS),
        *(f"chain_runtime.{reason}" for reason in _REASONS),
    }
    assert values.keys() >= expected, sorted(expected - values.keys())
    assert values["chain_runtime_unclassified"] == 0
    assert values["chain_runtime_unobserved_trace_exits"] == 0
    return values


def _reset_runtime_counts(manager: QaJitRuntimeManager) -> None:
    assert manager._native.pointer is not None
    RUNTIME_RESET_CHAIN_MEASUREMENTS(manager._native.pointer)


def _measure_arithmetic() -> dict[str, object]:
    from bench_jit import JITCompilerBenchmark

    module = parse(memoryview(JITCompilerBenchmark._create_heavy_loop_binary()))
    manager = _new_manager()
    engine, interpreter = _new_engine(module, manager)
    function_index = module.export_func_index("heavy_loop")
    try:
        assert engine.call(interpreter, function_index, [100]).unwrap() == [4950]
        engine.idle_hook(16)
        _reset_runtime_counts(manager)
        result = engine.call(interpreter, function_index, [100_000]).unwrap()
        assert result == [704_982_704]
        values = _read_measurements(manager)
        return {
            "workload": "arithmetic_loop",
            "iterations": 100_000,
            "result": int(result[0]),
            "jit_body_count": engine.stat_jit_invocations,
            "measurements": values,
        }
    finally:
        manager.close()


def _measure_aobench(width: int, height: int) -> dict[str, object]:
    from bench_vtune_workload import AO_WASM_PATH, _check_ao_frame
    from ipc_router import FB_URI_HAL_STDOUT
    from system import System
    from tier2_runtime.wasm.reader import parse
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.hal.stream import StreamTransport
    from tier3_platform.drivers.wasi.context import WasiHostContext

    module = parse(AO_WASM_PATH.read_bytes())
    system = System()
    system.transport = StreamTransport((width + 1) * height)
    wasi_context = WasiHostContext(system)
    system.start_hal_driver(
        DummyDriver(system.pool, transport=system.transport), FB_URI_HAL_STDOUT
    ).unwrap()
    module.init_memory_data(wasi_context.guest_memory, ())
    bindings = InterpreterBindings.with_memory_and_functions(
        wasi_context.guest_memory,
        wasi_context.build_interpreter_host_functions(module),
    )
    manager = _new_manager()
    engine = ChainMeasurementEngine(
        jit_runtime=manager,
        bump_allocator=module.allocator,
        collect_runtime_stats=True,
    )
    engine.register_module_blocks(module)
    interpreter = NativeInterpreter(
        module,
        bindings,
        bump_allocator=engine.bump_allocator,
        execution_plugin=manager,
    )
    function_index = module.export_func_index("main")
    try:
        engine.call(interpreter, function_index, [8, 4])
        system.logger.flush()
        system.transport.drain_output()
        engine.idle_hook(16)
        _reset_runtime_counts(manager)
        engine.call(interpreter, function_index, [width, height])
        system.logger.flush()
        frame = system.transport.drain_output()
        _check_ao_frame(frame, width, height)
        measurements = _read_measurements(manager)
        assert measurements["chain_runtime.linked"] > 0
        return {
            "workload": "aobench",
            "width": width,
            "height": height,
            "result_sha256": hashlib.sha256(frame).hexdigest(),
            "output_bytes": len(frame),
            "jit_body_count": engine.stat_jit_invocations,
            "measurements": measurements,
        }
    finally:
        manager.close()


def _measure_suite() -> dict[str, object]:
    import wasmtime
    from bench_vtune_workload import KERNEL_UNITS, SUITE_WASM_PATH, _new_guest

    wasm = SUITE_WASM_PATH.read_bytes()
    oracle_engine = wasmtime.Engine()
    oracle_module = wasmtime.Module(oracle_engine, wasm)
    oracle_store = wasmtime.Store(oracle_engine)
    oracle_exports = wasmtime.Instance(oracle_store, oracle_module, []).exports(oracle_store)
    module, bindings = _new_guest()
    manager = _new_manager()
    engine, interpreter = _new_engine(module, manager, bindings)
    rows: list[dict[str, int | str]] = []
    try:
        for name in KERNEL_UNITS:
            function_index = module.export_func_index(name)
            engine.call(interpreter, function_index, [1])
        engine.idle_hook(16)
        _reset_runtime_counts(manager)
        checksum = 0
        for name, units in KERNEL_UNITS.items():
            function_index = module.export_func_index(name)
            result = (
                int(engine.call(interpreter, function_index, [units]).unwrap()[0]) & 0xFFFF_FFFF
            )
            expected = int(oracle_exports[name](oracle_store, units)) & 0xFFFF_FFFF
            assert result == expected, (name, units, result, expected)
            rows.append({"kernel": name, "units": units, "result": result})
            checksum = (checksum + result) & 0xFFFF_FFFF
        return {
            "workload": "kernel_suite",
            "kernel_count": len(rows),
            "kernels": rows,
            "result_checksum": checksum,
            "jit_body_count": engine.stat_jit_invocations,
            "measurements": _read_measurements(manager),
        }
    finally:
        manager.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workload",
        choices=("all", "arithmetic", "ao32", "ao320", "suite"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.workload == "all":
        result: dict[str, object] = {
            "metadata": _run_metadata(),
            "runs": [
                _measure_arithmetic(),
                _measure_aobench(32, 16),
                _measure_aobench(320, 180),
                _measure_suite(),
            ],
        }
    elif args.workload == "arithmetic":
        result = _measure_arithmetic()
    elif args.workload == "ao32":
        result = _measure_aobench(32, 16)
    elif args.workload == "ao320":
        result = _measure_aobench(320, 180)
    else:
        result = _measure_suite()
    if args.workload != "all":
        result = {"metadata": _run_metadata(), **result}
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
