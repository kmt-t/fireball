"""Measure loader-owned bytes for the compact Interpreter annotation ABI."""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path

_RESULTS = Path(__file__).resolve().parent
_ROOT = _RESULTS.parents[4]
_PYSIM = _ROOT / "experiments" / "pysim"
_BENCH = _PYSIM / "benchmarks"
sys.path.insert(0, str(_BENCH))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM, _BENCH)

from bump_allocator import BumpAllocator
from tier2_runtime.abi.interpreter_abi import (
    CallFrameNative,
    CallStackNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    ModuleExecutionViewNative,
)
from tier2_runtime.wasm.module import BasicBlock, Module
from tier2_runtime.wasm.reader import parse

# JSON output is limited to concrete scalar, sequence, and mapping values.
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measure(path: Path, arena_capacity: int, baseline: dict[str, int]) -> dict[str, JsonValue]:
    wasm_bytes = path.read_bytes()
    allocator = BumpAllocator(arena_capacity)
    module: Module = parse(memoryview(wasm_bytes), allocator)
    annotation_bytes = sum(
        len(function.interpreter_annotation_pool)
        for function in module.functions
        if function.interpreter_annotation_pool is not None
    )
    block_count = len(module.blocks)
    index_entries = block_count
    radix_entries = 0 if module.block_radix_table is None else len(module.block_radix_table)
    index_bytes = 0 if module.block_index_entries is None else len(module.block_index_entries)
    radix_bytes = 0 if module.block_radix_table is None else len(module.block_radix_table) * 2
    metadata = {
        "functions_defined": len(module.functions),
        "functions_imported": len(module.imports),
        "basic_block_count": block_count,
        "code_bytes": sum(
            len(module.code_for(index + len(module.imports)))
            for index in range(len(module.functions))
        ),
        "annotation_pool_bytes": annotation_bytes,
        "function_annotation_pool_bytes": [
            len(function.interpreter_annotation_pool)
            for function in module.functions
            if function.interpreter_annotation_pool is not None
        ],
        "basic_block_record_bytes_each": ctypes.sizeof(BasicBlock),
        "basic_block_records_bytes": block_count * ctypes.sizeof(BasicBlock),
        "block_index_entries": index_entries,
        "block_index_entry_bytes_each": 6,
        "block_index_entries_bytes": index_bytes,
        "radix_entries": radix_entries,
        "radix_entry_bytes_each": 2,
        "radix_table_bytes": radix_bytes,
        "loader_arena_bytes": allocator.offset,
        "loader_arena_capacity_bytes": arena_capacity,
    }
    old_values = {
        "annotation_pool_bytes": baseline["annotation_pool_bytes"],
        "function_annotation_pool_bytes": baseline["function_annotation_pool_bytes"],
        "basic_block_record_bytes_each": baseline["basic_block_record_bytes_each"],
        "basic_block_records_bytes": block_count * baseline["basic_block_record_bytes_each"],
        "block_index_entry_bytes_each": 8,
        "block_index_entries_bytes": block_count * 8,
        "radix_entry_bytes_each": baseline["radix_entry_bytes_each"],
        "radix_table_bytes": radix_entries * baseline["radix_entry_bytes_each"],
        "loader_arena_bytes": baseline["loader_arena_bytes"],
    }
    delta = {
        key: metadata[key] - old_values[key]
        for key in (
            "annotation_pool_bytes",
            "basic_block_records_bytes",
            "block_index_entries_bytes",
            "radix_table_bytes",
            "loader_arena_bytes",
        )
    }
    itemized_delta = sum(
        delta[key]
        for key in (
            "annotation_pool_bytes",
            "basic_block_records_bytes",
            "block_index_entries_bytes",
            "radix_table_bytes",
        )
    )
    return {
        "wasm_path": str(path.relative_to(_ROOT)),
        "wasm_sha256": _sha256(path),
        "baseline_after_block_index_change": old_values,
        "measured_current": metadata,
        "delta_current_minus_baseline": delta,
        "loader_arena_delta_not_explained_by_itemized_fields": (
            delta["loader_arena_bytes"] - itemized_delta
        ),
        "annotation_pool_saved_percent": (
            (old_values["annotation_pool_bytes"] - annotation_bytes)
            * 100
            / old_values["annotation_pool_bytes"]
        ),
        "block_index_entries_saved_percent": (
            (old_values["block_index_entries_bytes"] - index_bytes)
            * 100
            / old_values["block_index_entries_bytes"]
        ),
        "loader_arena_saved_percent": (
            (old_values["loader_arena_bytes"] - allocator.offset)
            * 100
            / old_values["loader_arena_bytes"]
        ),
    }


def main() -> None:
    ao = _PYSIM / "benchmarks" / "aobench" / "aobench.wasm"
    suite = _PYSIM / "benchmarks" / "profile" / "guest" / "suite.wasm"
    report: dict[str, JsonValue] = {
        "method": "Loader parse with arena allocation; no timing instrumentation",
        "abi_sizes_bytes": {
            "BasicBlock": ctypes.sizeof(BasicBlock),
            "FunctionExecutionViewNative": ctypes.sizeof(FunctionExecutionViewNative),
            "ModuleExecutionViewNative": ctypes.sizeof(ModuleExecutionViewNative),
            "ExecutionContextABI": ctypes.sizeof(ExecutionContextABI),
            "CallFrameNative": ctypes.sizeof(CallFrameNative),
            "CallStackNative": ctypes.sizeof(CallStackNative),
            "JitTraceExitNative": 20,
        },
        "jit_cache_records": {
            "trace_header_bytes_before_after": [40, 32],
            "trace_exit_bytes_before_after": [24, 20],
            "bytes_saved_per_trace": 12,
            "jit_cache_entry_bytes_before_after": [8, 12],
            "jit_cache_entry_count": 72,
            "jit_cache_entry_bytes_before_after_total": [576, 864],
            "jit_cache_entry_total_delta_bytes": 288,
            "function_execution_view_bytes_before_after": [112, 88],
            "aobench_defined_function_count": 6,
            "aobench_function_execution_view_delta_bytes": -144,
        },
        "modules": [
            _measure(
                ao,
                131_072,
                {
                    "annotation_pool_bytes": 2_944,
                    "function_annotation_pool_bytes": [28, 60, 366, 394, 588, 1_508],
                    "basic_block_record_bytes_each": 36,
                    "radix_entry_bytes_each": 4,
                    "loader_arena_bytes": 27_100,
                },
            ),
            _measure(
                suite,
                2_000_000,
                {
                    "annotation_pool_bytes": 36_090,
                    "function_annotation_pool_bytes": [
                        1_175,
                        730,
                        2_549,
                        715,
                        724,
                        1_511,
                        366,
                        1_859,
                        795,
                        970,
                        586,
                        314,
                        1_366,
                        204,
                        1_411,
                        456,
                        1_162,
                        847,
                        733,
                        1_891,
                        11,
                        16,
                        15,
                        11,
                        11,
                        15,
                        11,
                        25,
                    ],
                    "basic_block_record_bytes_each": 36,
                    "radix_entry_bytes_each": 4,
                    "loader_arena_bytes": 366_520,
                },
            ),
        ],
    }
    output = _RESULTS / "loader_memory_impact.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
