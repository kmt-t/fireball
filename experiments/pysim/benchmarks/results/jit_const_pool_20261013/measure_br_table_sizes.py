"""Measure current br_table annotation bytes and compare both prior layouts."""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent
ROOT = RESULTS.parents[4]
PYSIM = ROOT / "experiments" / "pysim"
BENCH = PYSIM / "benchmarks"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(PYSIM))

from _bootstrap import configure_import_paths

configure_import_paths(PYSIM, BENCH)

from bump_allocator import BumpAllocator
from tier2_runtime.interpreter.control_flow import iter_scan_instrs
from tier2_runtime.wasm.leb128 import decode_unsigned_full
from tier2_runtime.wasm.module import BasicBlock
from tier2_runtime.wasm.opcodes import BR_TABLE
from tier2_runtime.wasm.reader import parse


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure_module(
    path: Path, arena_capacity: int
) -> dict[str, int | float | str | list[int] | dict[str, int]]:
    wasm = path.read_bytes()
    allocator = BumpAllocator(arena_capacity)
    module = parse(memoryview(wasm), allocator)
    label_counts: list[int] = []
    annotation_pool_bytes = 0
    for function_index in range(len(module.functions)):
        function = module.functions[function_index]
        if function.interpreter_annotation_pool is not None:
            annotation_pool_bytes += len(function.interpreter_annotation_pool)
        code = module.code_for(function_index + len(module.imports))
        for instruction in iter_scan_instrs(code):
            if instruction.opcode == BR_TABLE:
                label_count, _ = decode_unsigned_full(code, instruction.offset + 1)
                label_counts.append(label_count)

    target_count = sum(count + 1 for count in label_counts)
    old_duplicate_bytes = sum(4 * (count + 2) + 3 * (count + 1) for count in label_counts)
    current_bytes = len(label_counts) * 2 + target_count * 3
    return {
        "path": str(path.relative_to(ROOT)),
        "wasm_sha256": sha256(path),
        "br_table_count": len(label_counts),
        "label_counts_excluding_default": label_counts,
        "target_record_count_including_default": target_count,
        "current_annotation_pool_bytes": annotation_pool_bytes,
        "loader_sizes": {
            "functions_defined": len(module.functions),
            "functions_imported": len(module.imports),
            "code_bytes": sum(
                len(module.code_for(index + len(module.imports)))
                for index in range(len(module.functions))
            ),
            "basic_block_count": len(module.blocks),
            "basic_block_record_bytes_each": ctypes.sizeof(BasicBlock),
            "basic_block_records_bytes": len(module.blocks) * ctypes.sizeof(BasicBlock),
            "block_index_entries_bytes": 0
            if module.block_index_entries is None
            else len(module.block_index_entries),
            "radix_table_bytes": 0
            if module.block_radix_table is None
            else len(module.block_radix_table) * 2,
            "loader_arena_bytes": allocator.offset,
            "loader_arena_capacity_bytes": arena_capacity,
        },
        "br_table_annotation_bytes_old_duplicate_formula": old_duplicate_bytes,
        "br_table_annotation_bytes_current": current_bytes,
        "br_table_annotation_bytes_saved_by_old_duplicate_formula": old_duplicate_bytes
        - current_bytes,
        "br_table_annotation_bytes_saved_percent": (
            (old_duplicate_bytes - current_bytes) * 100 / old_duplicate_bytes
            if old_duplicate_bytes
            else 0.0
        ),
    }


def main() -> None:
    modules = [
        measure_module(BENCH / "aobench/aobench.wasm", 131_072),
        measure_module(BENCH / "profile/guest/suite.wasm", 2_000_000),
    ]
    modules[1]["historical_interpreter_ij_snapshot_annotation_pool_bytes"] = 20_121
    modules[1]["historical_snapshot_source_sha256"] = (
        "69b2e5aae653dbcf6f0d6b24c30671ffd671b74db60755abcd4b6747b48c5de0"
    )
    modules[1]["historical_snapshot_difference_current_minus_historical_bytes"] = (
        modules[1]["current_annotation_pool_bytes"] - 20_121
    )
    loader_report = {
        "method": "Loader parse with arena allocation; no timing instrumentation",
        "modules": [
            {
                "path": item["path"],
                "wasm_sha256": item["wasm_sha256"],
                **item["loader_sizes"],
            }
            for item in modules
        ],
    }
    loader_output = RESULTS / "loader_sizes_after.json"
    loader_output.write_text(json.dumps(loader_report, indent=2) + "\n")
    for item in modules:
        del item["loader_sizes"]
    report = {
        "source_sha256": {
            "experiments/pysim/tier2_runtime/interpreter/control_flow.py": sha256(
                PYSIM / "tier2_runtime/interpreter/control_flow.py"
            )
        },
        "modules": modules,
        "current_loader_sizes_json_sha256": sha256(loader_output),
        "capacity_formula": "2 + 3 * (label_count + 1)",
        "write_capacity_assertion": (
            "len(annotation_pool) == sum(_count_interpreter_annotation_pool_bytes(code_span))"
        ),
    }
    output = RESULTS / "br_table_measurement.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
