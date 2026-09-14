"""Tests for numeric WASM basic-block JIT scoring."""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[2]
for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
):
    _path_text = str(_path)
    if _path_text not in sys.path:
        sys.path.insert(0, _path_text)

from jit_scoring import (
    JIT_CANDIDATE_THRESHOLD,
    OPCODE_TABLE_BYTES,
    JITCandidateBitmap,
    OpcodeBenefitTable,
    score_opcodes,
)
from wasm_module import Function, FuncType, Module
from wasm_opcodes import I32_ADD, I32_CONST, I32_POPCNT, RETURN


def test_numeric_opcode_score_table() -> None:
    table = OpcodeBenefitTable()
    assert len(table.storage) == OPCODE_TABLE_BYTES
    assert table.score(I32_CONST) == 6
    assert table.score(I32_ADD) == 7
    assert table.score(I32_POPCNT) == -8
    assert score_opcodes((I32_CONST, I32_CONST, I32_ADD), table) == 19


def test_loader_scores_basic_block_once() -> None:
    code = bytes((I32_CONST, 1, I32_CONST, 2, I32_ADD, RETURN))
    module = Module(
        types=[FuncType(params=(), results=())],
        functions=[Function(type_index=0, locals_extra=[], code=code)],
    )
    module.build_basic_block_index()
    assert module.opcode_benefit_table is not None
    assert len(module.blocks) == 1
    assert module.blocks[0].jit_score == 19
    assert module.blocks[0].jit_score >= JIT_CANDIDATE_THRESHOLD


def test_jit_candidate_bitmap_allocates_and_marks_function_cards() -> None:
    bitmap = JITCandidateBitmap(card_shift=2)
    bitmap.allocate_functions(2)
    assert bitmap.is_candidate(0) is False
    bitmap.mark((1 << 16) | 4, code_len=8)
    assert bitmap.is_candidate((1 << 16) | 4) is True
    assert bitmap.is_candidate((1 << 16) | 0) is False
    assert bitmap.is_candidate((2 << 16) | 0) is False


if __name__ == "__main__":
    test_numeric_opcode_score_table()
    test_loader_scores_basic_block_once()
    test_jit_candidate_bitmap_allocates_and_marks_function_cards()
    print("[PASS] All 3 JIT scoring tests passed.")
