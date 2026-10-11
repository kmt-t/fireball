"""Tests for numeric WASM basic-block JIT scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

_PYSIM_DIR = Path(__file__).resolve().parents[2]

from qa.shared.helpers import parse_single_function_module
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.jit_scoring import (
    JIT_CANDIDATE_THRESHOLD,
    OPCODE_BENEFIT_TABLE,
    OPCODE_TABLE_BYTES,
    OpcodeBenefitTable,
    block_score,
    score_opcodes,
)
from tier2_runtime.wasm.module import I32
from tier2_runtime.wasm.opcodes import END, I32_ADD, I32_CONST, I32_POPCNT


def test_numeric_opcode_score_table() -> None:
    table = OpcodeBenefitTable()
    assert len(table.storage) == OPCODE_TABLE_BYTES
    assert table.score(I32_CONST) == 6
    assert table.score(I32_ADD) == 7
    assert table.score(I32_POPCNT) == 3
    assert table.score(0x7F) == 3  # i64.div_s uses a JIT Runtime API.
    assert table.score(0x91) == 3  # f32.sqrt uses a JIT Runtime API.
    assert table.score(0x23) == 4  # global.get uses a JIT Runtime API.
    assert table.score(0xFC) == -2  # Admitted 0xfc subopcodes use Runtime APIs.
    assert score_opcodes((I32_CONST, I32_CONST, I32_ADD), table) == 19


def test_plugin_scores_basic_block() -> None:
    code = bytes((I32_CONST, 1, I32_CONST, 2, I32_ADD, END))
    module = parse_single_function_module(code, results=(I32,))
    assert OPCODE_BENEFIT_TABLE.score(I32_ADD) == 7
    assert len(module.blocks) == 1
    assert block_score(module, module.blocks[0]) == 19
    assert JIT_CANDIDATE_THRESHOLD == 9  # jit_runtime.mdの規定値から独立に比較する。


@pytest.mark.parametrize(
    "code, results, score, candidate",
    (
        (bytes.fromhex("41 01 00 45 1a 0b"), (), 8, False),
        (bytes.fromhex("41 01 69 0b"), (I32,), 9, True),
        (bytes.fromhex("41 01 1a 0b"), (), 10, True),
        (bytes.fromhex("00 0b"), (), -8, False),
    ),
)
def test_plugin_candidate_gate_uses_specification_threshold(
    code: bytes, results: tuple[int, ...], score: int, candidate: bool
) -> None:
    """TEST-LOAD-49: 実登録で候補カードを生成し、非候補の履歴を記録しない。"""
    module = parse_single_function_module(code, results=results)
    executable_blocks = tuple(block for block in module.blocks if block.byte_span != 0)
    assert len(executable_blocks) == 1
    block = executable_blocks[0]
    assert block_score(module, block) == score
    manager = JITRuntimeManager(card_shift=0, min_trace_bytes=1)
    try:
        assert manager.candidate_threshold == 9
        manager.register_module(module)
        import ctypes

        from qa.private.jit_native_abi import _NATIVE_LIBRARY

        native_score = _NATIVE_LIBRARY.fb_qa_block_score
        native_score.argtypes = (ctypes.c_void_p,)
        native_score.restype = ctypes.c_int64
        block_index = next(
            index for index, item in enumerate(module.blocks) if item.head_pc == block.head_pc
        )
        assert native_score(ctypes.byref(manager._block_inputs[block_index])) == score
        assert manager.trackable.is_marked(block.head_pc) == candidate
        assert manager.exec_counter == 0
        head_pc = block.head_pc
        assert not manager.record_block_head(head_pc)
        assert manager.exec_counter == (1 if candidate else 0)
        manager.on_interpreter_exit(False)
        assert manager.card_state(head_pc) == (1 if candidate else 0)
    finally:
        manager.cache._native.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
