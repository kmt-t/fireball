"""Record per-residency trace usage under hot loops and one-shot cold functions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_BENCH_DIR.parent, _BENCH_DIR)

from bench_jit_aging import build_workload_wat
from bump_allocator import BumpAllocator
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.wasm.reader import parse

try:
    import wasmtime
except ImportError:
    wasmtime = None


@dataclass(frozen=True)
class TraceExecutionRecord:
    head_pc: int
    exec_count: int
    evicted: bool


def record_usage(output: Path, hot_functions: int, cold_functions: int, iterations: int) -> None:
    assert wasmtime is not None
    assert hot_functions > 0 and cold_functions > 0 and iterations > 0
    allocator = BumpAllocator()
    module = parse(
        bytes(wasmtime.wat2wasm(build_workload_wat(hot_functions, cold_functions))), allocator
    )
    records: list[TraceExecutionRecord] = []

    def on_retire(pc: int, count: int) -> None:
        # No replacement or flush in this workload: every retirement is a real eviction.
        records.append(TraceExecutionRecord(pc, count, True))

    manager = JITRuntimeManager(jit_compiler=TraceCompiler(), retire_observer=on_retire)
    engine = RuntimeEngine(jit_runtime=manager, bump_allocator=allocator)
    engine.register_module_blocks(module)
    interpreter = NativeInterpreter(module, InterpreterBindings.empty())
    for cold_index in range(cold_functions):
        for hot_index in range(hot_functions):
            expected = sum((k * (3 + hot_index)) ^ 7 for k in range(iterations))
            result = engine.call(
                interpreter, module.export_func_index(f"h{hot_index}"), [iterations]
            )
            assert result == [expected]
        cold = module.export_func_index(f"c{cold_index}")
        expected_cold = cold_index * (5 + cold_index) + cold_index
        # Qualify a cold trace with two visits, then never call the function again.
        for _ in range(2):
            assert engine.call(interpreter, cold, [cold_index]) == [expected_cold]
        engine.idle_hook()
    for block in module.blocks:
        trace = manager.cache.find_trace(block.head_pc)
        if trace is not None:
            records.append(TraceExecutionRecord(block.head_pc, trace.exec_count, False))
    evicted = [row for row in records if row.evicted]
    zero_evicted = sum(row.exec_count == 0 for row in evicted)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "workload": "hot loops plus twice-called cold functions",
                "hot_functions": hot_functions,
                "cold_functions": cold_functions,
                "iterations": iterations,
                "counter_max": 0xFFFF_FFFF,
                "retirement_counts_are_lower_bounds": True,
                "native_source_sha256": hashlib.sha256(
                    (
                        _BENCH_DIR.parent
                        / "native/tier2_runtime/interpreter/native_interpreter.cxx"
                    ).read_bytes()
                ).hexdigest(),
                "native_library_sha256": hashlib.sha256(
                    (
                        _BENCH_DIR.parent / "tier2_runtime/interpreter/libnative_interpreter.so"
                    ).read_bytes()
                ).hexdigest(),
                "runtime_stats_enabled": engine.collect_runtime_stats,
                "hotspot_profiling_enabled": manager.hotspot_profiling_enabled,
                "evicted_traces": len(evicted),
                "zero_execution_evictions": zero_evicted,
                "resident_traces": len(records) - len(evicted),
                "records": [asdict(row) for row in records],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Recorded {len(records)} traces: {len(evicted)} evicted, {zero_evicted} unused evictions"
    )
    print(output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hot-functions", type=int, default=4)
    parser.add_argument("--cold-functions", type=int, default=120)
    parser.add_argument("--iterations", type=int, default=8)
    args = parser.parse_args()
    if wasmtime is None:
        print("SKIP: wasmtime is unavailable")
    else:
        record_usage(args.output, args.hot_functions, args.cold_functions, args.iterations)
