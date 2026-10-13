"""Measure current AO-Bench loader and ABI storage after the runtime cleanup."""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PYSIM = ROOT / "experiments" / "pysim"
sys.path.insert(0, str(PYSIM / "tier1_core"))
sys.path.insert(0, str(PYSIM))

from bump_allocator import BumpAllocator
from tier2_runtime.abi.interpreter_abi import (
    CallFrameNative,
    CallStackNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    ModuleExecutionViewNative,
)
from tier2_runtime.wasm.module import BasicBlock
from tier2_runtime.wasm.reader import parse


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    wasm_path = PYSIM / "benchmarks/aobench/aobench.wasm"
    arena_capacity = 131_072
    allocator = BumpAllocator(arena_capacity)
    module = parse(memoryview(wasm_path.read_bytes()), allocator)
    defined_function_count = len(module.functions)
    imported_function_count = len(module.imports)
    function_view_count = defined_function_count + imported_function_count
    code_bytes = sum(
        len(module.code_for(index + imported_function_count))
        for index in range(defined_function_count)
    )
    annotation_bytes = sum(
        len(function.interpreter_annotation_pool)
        for function in module.functions
        if function.interpreter_annotation_pool is not None
    )
    local_count = sum(
        function.local_layout_cache.count
        for function in module.functions
        if function.local_layout_cache is not None
    )
    control_map_native_bytes_before = code_bytes * 16
    search_entries_bytes_before = len(module.blocks) * 6 + 34
    old_loader_arena_bytes = 21_444
    loader_arena_bytes = allocator.offset
    removed_loader_bytes = old_loader_arena_bytes - loader_arena_bytes
    assert code_bytes == 937 and len(module.blocks) == 107 and local_count == 50
    assert control_map_native_bytes_before == 14_992
    assert search_entries_bytes_before == 676

    report = {
        "method": "parse AO-Bench through the current Loader with a 131072-byte BumpAllocator",
        "wasm_path": str(wasm_path.relative_to(ROOT)),
        "wasm_sha256": sha256(wasm_path),
        "loader_source_sha256": {
            path: sha256(ROOT / path)
            for path in (
                "experiments/pysim/tier2_runtime/wasm/module.py",
                "experiments/pysim/tier2_runtime/interpreter/control_flow.py",
                "experiments/pysim/tier2_runtime/abi/interpreter_abi.hxx",
                "experiments/pysim/tier2_runtime/abi/interpreter_abi.py",
            )
        },
        "module": {
            "defined_functions": defined_function_count,
            "imported_functions": imported_function_count,
            "code_bytes": code_bytes,
            "basic_blocks": len(module.blocks),
            "locals": local_count,
            "annotation_pool_bytes": annotation_bytes,
            "loader_arena_capacity_bytes": arena_capacity,
            "loader_arena_bytes_after": loader_arena_bytes,
            "loader_arena_bytes_before": old_loader_arena_bytes,
            "loader_arena_bytes_removed": removed_loader_bytes,
            "loader_arena_reduction_percent": (removed_loader_bytes * 100 / old_loader_arena_bytes),
        },
        "storage_comparison": {
            "native_control_map_bytes_before": control_map_native_bytes_before,
            "native_control_map_bytes_after": 0,
            "native_pc_index_bytes_before": search_entries_bytes_before,
            "native_pc_index_bytes_after": 0,
            "local_sizes_bytes_before": local_count,
            "local_sizes_bytes_after": 0,
            "python_block_storage": "retained for Module.get_block()",
        },
        "abi_x86_64": {
            "ExecutionContextABI_bytes_before": 168,
            "ExecutionContextABI_bytes_after": ctypes.sizeof(ExecutionContextABI),
            "FunctionExecutionViewNative_bytes_before": 88,
            "FunctionExecutionViewNative_bytes_after": ctypes.sizeof(FunctionExecutionViewNative),
            "FunctionExecutionViewNative_records": function_view_count,
            "ModuleExecutionViewNative_bytes_before": 128,
            "ModuleExecutionViewNative_bytes_after": ctypes.sizeof(ModuleExecutionViewNative),
            "BasicBlock_bytes_each": ctypes.sizeof(BasicBlock),
            "CallFrameNative_bytes_before": 48,
            "CallFrameNative_bytes_after": ctypes.sizeof(CallFrameNative),
            "CallStackNative_bytes_before": 1_544,
            "CallStackNative_bytes_after": ctypes.sizeof(CallStackNative),
            "CallStackNative_capacity": 32,
            "ControlStackNative_bytes_before": 520,
            "ControlStackNative_bytes_after": 0,
        },
        "calculated_bytes_removed": {
            "native_control_map": control_map_native_bytes_before,
            "native_pc_index": search_entries_bytes_before,
            "control_stack": 520,
            "execution_context": 168 - ctypes.sizeof(ExecutionContextABI),
            "function_views": (88 - ctypes.sizeof(FunctionExecutionViewNative))
            * function_view_count,
            "module_view": 128 - ctypes.sizeof(ModuleExecutionViewNative),
            "call_stack": 1_544 - ctypes.sizeof(CallStackNative),
            "local_sizes": local_count,
        },
    }
    report["calculated_bytes_removed"]["total"] = sum(
        value for key, value in report["calculated_bytes_removed"].items() if key != "total"
    )
    assert report["calculated_bytes_removed"]["total"] == 16_654
    output = Path(__file__).with_name("storage_after.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
