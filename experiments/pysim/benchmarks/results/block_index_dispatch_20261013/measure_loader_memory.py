"""Measure AO-Bench Loader arena and block-index metadata sizes."""

from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path

from bump_allocator import BumpAllocator
from jit_native_abi import NativeTraceExit
from tier2_runtime.abi.interpreter_abi import (
    CallFrameNative,
    CallStackNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
)
from tier2_runtime.wasm.module import BasicBlock
from tier2_runtime.wasm.reader import parse

# JSON output is limited to concrete scalar, sequence, and mapping values.
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    pysim_dir = Path(__file__).resolve().parents[3]
    result_dir = Path(__file__).resolve().parent
    repository = pysim_dir.parents[1]
    wasm_path = pysim_dir / "benchmarks/aobench/aobench.wasm"
    baseline_path = (
        pysim_dir / "benchmarks/results/interpreter_contracts_v3_20261012/pool_step0.json"
    )
    baseline = json.loads(baseline_path.read_text())
    allocator = BumpAllocator()
    module = parse(memoryview(wasm_path.read_bytes()), allocator)
    function_pool_bytes = [
        len(function.interpreter_annotation_pool or b"") for function in module.functions
    ]
    block_count = len(module.blocks)
    old_block_bytes = 28
    new_block_bytes = ctypes.sizeof(BasicBlock)
    old_pool_bytes = baseline["after_bytes"]
    old_arena_bytes = baseline["allocator_used_bytes"]
    current_pool_bytes = sum(function_pool_bytes)
    current_arena_bytes = allocator.offset
    source_paths = (
        repository / "experiments/pysim/tier2_runtime/interpreter/control_flow.py",
        repository / "experiments/pysim/tier2_runtime/wasm/module.py",
        repository / "experiments/pysim/tier2_runtime/abi/interpreter_abi.hxx",
        repository / "experiments/pysim/native/tier3_plugins/jit/jit_types.hxx",
    )
    report: dict[str, JsonValue] = {
        "measured_at_local": "2026-10-13",
        "workload": "AO-Bench WASM module (Loader only)",
        "input_wasm_sha256": _sha256(wasm_path),
        "measurement_script_sha256": _sha256(Path(__file__)),
        "baseline_step0": {
            "source_result": str(baseline_path.relative_to(repository)),
            "source_result_sha256": _sha256(baseline_path),
            "annotation_pool_bytes": old_pool_bytes,
            "loader_arena_bytes": old_arena_bytes,
            "basicblock_bytes_each": old_block_bytes,
        },
        "current": {
            "function_count": len(module.functions),
            "basicblock_count": block_count,
            "function_annotation_pool_bytes": function_pool_bytes,
            "annotation_pool_bytes": current_pool_bytes,
            "basicblock_bytes_each": new_block_bytes,
            "basicblock_metadata_bytes": block_count * new_block_bytes,
            "loader_arena_bytes": current_arena_bytes,
            "loader_arena_capacity_bytes": allocator.capacity,
        },
        "increase": {
            "annotation_pool_bytes": current_pool_bytes - old_pool_bytes,
            "annotation_pool_percent": (current_pool_bytes - old_pool_bytes) * 100 / old_pool_bytes,
            "basicblock_bytes": block_count * (new_block_bytes - old_block_bytes),
            "basicblock_percent": (new_block_bytes - old_block_bytes) * 100 / old_block_bytes,
            "loader_arena_bytes": current_arena_bytes - old_arena_bytes,
            "loader_arena_percent": (current_arena_bytes - old_arena_bytes) * 100 / old_arena_bytes,
            "arena_capacity_percent": (current_arena_bytes - old_arena_bytes)
            * 100
            / allocator.capacity,
        },
        "jit_boundary_metadata": {
            "bytes_before": 24,
            "bytes_after": ctypes.sizeof(NativeTraceExit),
            "block_index_offset": NativeTraceExit.block_index.offset,
            "increase_bytes": ctypes.sizeof(NativeTraceExit) - 24,
        },
        "runtime_abi_current_bytes": {
            "execution_context": ctypes.sizeof(ExecutionContextABI),
            "call_frame": ctypes.sizeof(CallFrameNative),
            "call_stack_32_frames": ctypes.sizeof(CallStackNative),
            "function_execution_view": ctypes.sizeof(FunctionExecutionViewNative),
        },
        "source_sha256": {
            str(path.relative_to(repository)): _sha256(path) for path in source_paths
        },
    }
    output = result_dir / "memory_impact.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
