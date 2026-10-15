"""Estimate compact derived-value annotation sizes without changing runtime code."""

from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path

from bump_allocator import BumpAllocator
from tier2_runtime.interpreter.control_flow import (
    _LEB_UNSIGNED_OPERAND,
    _MEMARG_OPCODES,
    _MEMORY_INDEX_OPCODES,
    _count_interpreter_annotation_pool_bytes,
    iter_scan_instrs,
)
from tier2_runtime.wasm.leb128 import decode_unsigned_full
from tier2_runtime.wasm.module import BasicBlock
from tier2_runtime.wasm.opcodes import (
    BLOCK,
    BR,
    BR_IF,
    BR_TABLE,
    CALL,
    CALL_INDIRECT,
    ELSE,
    END,
    FC_PREFIX,
    I32_CONST,
    I64_CONST,
    IF,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    LOOP,
)
from tier2_runtime.wasm.reader import parse

# JSON output is limited to concrete scalar, sequence, and mapping values.
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def estimate(code: memoryview) -> tuple[int, dict[str, int]]:
    total = 0
    counts: dict[str, int] = {}
    for ins in iter_scan_instrs(code):
        op = ins.opcode
        name = f"0x{op:02x}"
        counts[name] = counts.get(name, 0) + 1
        if op in (LOCAL_GET, LOCAL_SET, LOCAL_TEE):
            size = 4  # frame offset u16, width u8, encoded instruction width u8
        elif op in (0x23, 0x24):  # global.get/set
            size = 3  # index u16, encoded instruction width u8
        elif op == I32_CONST:
            size = 5  # sign-extended value u32 plus encoded width u8
        elif op == I64_CONST:
            size = 9  # sign-extended value u64 plus encoded width u8
        elif _MEMARG_OPCODES.at(op):
            size = 5  # effective offset u32 plus encoded width u8
        elif op == BR:
            size = 3  # target lexical frame u8 plus target block u16
        elif op == BR_IF:
            size = 4  # target pair plus encoded width for untaken branch
        elif op == BR_TABLE:
            label_count, _ = decode_unsigned_full(code, ins.offset + 1)
            size = 4 + (label_count + 1) * 3  # u32 count; (frame u8, block u16) pairs
        elif op == IF:
            size = 4  # then and else block indices u16
        elif op in (BLOCK, LOOP, ELSE, END):
            size = 2  # continuation block index u16
        elif op in (CALL, CALL_INDIRECT):
            size = 5  # function/type index u16, encoded width u8, resume block u16
        elif op == FC_PREFIX:
            size = 4  # subopcode u8, encoded width u8 and successor block index u16
        elif _MEMORY_INDEX_OPCODES.at(op):
            size = 0  # fixed opcode width; the reserved memory index is validated as zero
        elif _LEB_UNSIGNED_OPERAND.at(op):
            size = 3  # compact decoded index u16 and encoded width u8
        else:
            size = 0  # fixed-width/no-operand instructions need no annotation
        total += size
    return total, counts


def module_report(path: Path) -> dict[str, JsonValue]:
    data = path.read_bytes()
    allocator = BumpAllocator(2_000_000)
    module = parse(memoryview(data), allocator)
    old_bytes = 0
    compact_bytes = 0
    instruction_count = 0
    per_function: list[dict[str, int]] = []
    for index in range(len(module.functions)):
        code = module.code_for(index + len(module.imports))
        old_function_bytes = _count_interpreter_annotation_pool_bytes(code)
        proposed, counts = estimate(code)
        old_bytes += old_function_bytes
        compact_bytes += proposed
        instruction_count += sum(counts.values())
        per_function.append(
            {
                "function_index": index + len(module.imports),
                "instruction_count": sum(counts.values()),
                "current_pool_bytes": old_function_bytes,
                "proposed_pool_bytes": proposed,
            }
        )
    radix_count = 0 if module.block_radix_table is None else len(module.block_radix_table)
    index_entry_count = (
        0 if module.block_index_entries is None else len(module.block_index_entries) // 2
    )
    current_block_bytes = len(module.blocks) * ctypes.sizeof(BasicBlock)
    proposed_block_bytes = len(module.blocks) * 32
    current_index_bytes = index_entry_count * 8
    proposed_index_bytes = index_entry_count * 8  # Natural C alignment keeps each entry 8 bytes.
    current_radix_bytes = radix_count * 4
    proposed_radix_bytes = radix_count * 2
    proposed_arena = (
        allocator.offset
        - old_bytes
        + compact_bytes
        + proposed_block_bytes
        - current_block_bytes
        + proposed_radix_bytes
        - current_radix_bytes
    )
    return {
        "path": str(path),
        "sha256": sha256(path),
        "functions": len(module.functions),
        "blocks": len(module.blocks),
        "instructions": instruction_count,
        "per_function": per_function,
        "current_pool_bytes": old_bytes,
        "proposed_pool_bytes": compact_bytes,
        "saved_bytes": old_bytes - compact_bytes,
        "saved_percent": (old_bytes - compact_bytes) * 100 / old_bytes if old_bytes else 0,
        "metadata_estimate": {
            "basic_block_bytes_each_before": ctypes.sizeof(BasicBlock),
            "basic_block_bytes_each_after": 32,
            "basic_block_bytes_before": current_block_bytes,
            "basic_block_bytes_after": proposed_block_bytes,
            "block_index_entry_count": index_entry_count,
            "block_index_bytes_before": current_index_bytes,
            "block_index_bytes_after": proposed_index_bytes,
            "radix_entry_count": radix_count,
            "radix_table_bytes_before": current_radix_bytes,
            "radix_table_bytes_after": proposed_radix_bytes,
            "loader_arena_bytes_before": allocator.offset,
            "loader_arena_bytes_after_estimate": proposed_arena,
            "loader_arena_saved_bytes_estimate": allocator.offset - proposed_arena,
        },
        "jit_boundary_metadata_bytes_before_after": [24, 24],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    paths = (
        root / "experiments/pysim/benchmarks/aobench/aobench.wasm",
        root / "experiments/pysim/benchmarks/profile/guest/suite.wasm",
    )
    report = {
        "method": "loader byte-count formula; no runtime or source changes",
        "modules": [module_report(path) for path in paths],
    }
    output = Path(__file__).resolve().parent / "pool_estimate.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
