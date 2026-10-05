"""Tests for numeric WASM basic-block JIT scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

_PYSIM_DIR = Path(__file__).resolve().parents[2]

from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.jit_scoring import (
    JIT_CANDIDATE_THRESHOLD,
    OPCODE_BENEFIT_TABLE,
    OPCODE_TABLE_BYTES,
    OpcodeBenefitTable,
    block_score,
    score_opcodes,
)
from tier2_runtime.wasm.module import Function, FuncType, Module
from tier2_runtime.wasm.opcodes import I32_ADD, I32_CONST, I32_POPCNT, RETURN


def test_numeric_opcode_score_table() -> None:
    table = OpcodeBenefitTable()
    assert len(table.storage) == OPCODE_TABLE_BYTES
    assert table.score(I32_CONST) == 6
    assert table.score(I32_ADD) == 7
    assert table.score(I32_POPCNT) == -8
    assert score_opcodes((I32_CONST, I32_CONST, I32_ADD), table) == 19


def test_plugin_scores_basic_block() -> None:
    code = bytes((I32_CONST, 1, I32_CONST, 2, I32_ADD, RETURN))
    module = Module(
        types=[FuncType(params=(), results=())],
        functions=[Function(type_index=0, locals_extra=[], code=code)],
    )
    module.build_basic_block_index()
    assert OPCODE_BENEFIT_TABLE.score(I32_ADD) == 7
    assert len(module.blocks) == 1
    assert block_score(module, module.blocks[0]) == 19
    assert JIT_CANDIDATE_THRESHOLD == 9  # jit_runtime.mdの規定値から独立に比較する。


@pytest.mark.parametrize(
    "code, score, candidate",
    (
        (bytes.fromhex("41 01 69 45 1a 0b"), 8, False),
        (bytes.fromhex("41 01 1a 10 00 0b"), 9, True),
        (bytes.fromhex("41 01 1a 0b"), 10, True),
        (bytes.fromhex("41 01 69 1a 0b"), 2, False),
    ),
)
def test_plugin_candidate_gate_uses_specification_threshold(
    code: bytes, score: int, candidate: bool
) -> None:
    """TEST-LOAD-49: 実登録で候補カードを生成し、非候補の履歴を記録しない。"""
    module = Module(
        types=(FuncType(params=(), results=()),),
        functions=(Function(type_index=0, locals_extra=(), code=code),),
    )
    module.build_basic_block_index()
    assert len(module.blocks) == 1
    assert block_score(module, module.blocks[0]) == score
    manager = JITRuntimeManager(card_shift=0, min_trace_bytes=1)
    try:
        assert manager.candidate_threshold == 9
        manager.register_module(module)
        import ctypes

        from qa.private.jit_native_abi import _NATIVE_LIBRARY

        native_score = _NATIVE_LIBRARY.fb_qa_block_score
        native_score.argtypes = (ctypes.c_void_p,)
        native_score.restype = ctypes.c_int64
        assert native_score(ctypes.byref(manager._block_inputs[0])) == score
        assert manager.trackable.is_marked(module.blocks[0].head_pc) == candidate
        assert manager.exec_counter == 0
        head_pc = module.blocks[0].head_pc
        assert not manager.record_block_head(head_pc)
        assert manager.exec_counter == (1 if candidate else 0)
        manager.on_interpreter_exit(False)
        assert manager.card_state(head_pc) == (1 if candidate else 0)
    finally:
        manager.cache._native.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
