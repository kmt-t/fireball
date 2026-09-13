"""Static JIT-candidate scoring for decoded WASM basic blocks."""

from __future__ import annotations

from collections.abc import Iterable

from config import JIT_CARD_SHIFT
from system_containers import BitView, MutableBitStorage, StaticVector
from wasm_opcodes import (
    BLOCK,
    BR,
    BR_IF,
    BR_TABLE,
    CALL,
    CALL_INDIRECT,
    DROP,
    ELSE,
    END,
    F32_ADD,
    F32_CONST,
    F32_DIV,
    F32_MUL,
    F32_SUB,
    F64_ADD,
    F64_CONST,
    F64_DIV,
    F64_MUL,
    F64_SUB,
    I32_ADD,
    I32_AND,
    I32_CLZ,
    I32_CONST,
    I32_CTZ,
    I32_DIV_S,
    I32_DIV_U,
    I32_EQ,
    I32_EQZ,
    I32_GE_S,
    I32_GE_U,
    I32_GT_S,
    I32_GT_U,
    I32_LE_S,
    I32_LE_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_REM_S,
    I32_REM_U,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_SUB,
    I32_XOR,
    I64_ADD,
    I64_CONST,
    I64_MUL,
    I64_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    LOOP,
    MEMORY_GROW,
    NOP,
    RETURN,
    SELECT,
    UNREACHABLE,
)

SCORE_MIN = -8
SCORE_MAX = 7
JIT_CANDIDATE_THRESHOLD = 9
OPCODE_TABLE_COUNT = 256
OPCODE_TABLE_BYTES = OPCODE_TABLE_COUNT // 2


def _encode_int4(value: int) -> int:
    assert SCORE_MIN <= value <= SCORE_MAX
    return value & 0x0F


def _decode_int4(value: int) -> int:
    value &= 0x0F
    return value - 16 if value >= 8 else value


# Numeric entries keep the loader independent of opcode-name strings and
# make the table directly translatable to a ROM-resident C++ constexpr array.
_SCORE_ENTRIES: tuple[tuple[int, int], ...] = (
    (I32_ADD, 7),
    (I32_SUB, 7),
    (I32_AND, 7),
    (I32_OR, 7),
    (I32_XOR, 7),
    (I32_SHL, 7),
    (I32_SHR_S, 7),
    (I32_SHR_U, 7),
    (I32_MUL, 6),
    (I32_EQZ, 6),
    (I32_EQ, 6),
    (I32_NE, 6),
    (I32_LT_S, 6),
    (I32_LT_U, 6),
    (I32_GT_S, 6),
    (I32_GT_U, 6),
    (I32_LE_S, 6),
    (I32_LE_U, 6),
    (I32_GE_S, 6),
    (I32_GE_U, 6),
    (I32_CLZ, 6),
    (I32_CTZ, 6),
    (I32_CONST, 6),
    (I64_CONST, 6),
    (F32_CONST, 6),
    (F64_CONST, 6),
    (LOCAL_GET, 6),
    (LOCAL_SET, 6),
    (LOCAL_TEE, 6),
    (I32_DIV_S, 4),
    (I32_DIV_U, 4),
    (DROP, 4),
    (SELECT, 4),
    (RETURN, 4),
    (NOP, 4),
    (I32_REM_S, 4),
    (I32_REM_U, 4),
    (BR, 5),
    (BR_IF, 5),
    (I64_ADD, 3),
    (I64_SUB, 3),
    (I64_MUL, 3),
    (F32_ADD, 3),
    (F32_SUB, 3),
    (F32_MUL, 3),
    (F32_DIV, 3),
    (F64_ADD, 3),
    (F64_SUB, 3),
    (F64_MUL, 3),
    (F64_DIV, 3),
    (BLOCK, 0),
    (LOOP, 0),
    (ELSE, 0),
    (END, 0),
    (CALL, -1),
    (CALL_INDIRECT, -1),
    (BR_TABLE, -1),
    (MEMORY_GROW, -2),
    (UNREACHABLE, -8),
)


class OpcodeBenefitTable:
    """ROM-shaped 4-bit signed score table indexed by numeric WASM opcode."""

    __slots__ = ("storage", "view")

    def __init__(self) -> None:
        storage = bytearray((0x88,) * OPCODE_TABLE_BYTES)
        view = BitView(storage, bits=4, count=OPCODE_TABLE_COUNT)
        for opcode, score in _SCORE_ENTRIES:
            view.put(opcode, _encode_int4(score))
        self.storage: bytes = bytes(storage)
        self.view: BitView = BitView(self.storage, bits=4, count=OPCODE_TABLE_COUNT)

    def score(self, opcode: int) -> int:
        assert 0 <= opcode < OPCODE_TABLE_COUNT
        return _decode_int4(self.view.at(opcode))


class JITCandidateBitmap:
    """Fixed per-function one-bit card bitmap populated at module load."""

    __slots__ = ("card_shift", "func_storages", "func_tables")

    def __init__(self, card_shift: int = JIT_CARD_SHIFT) -> None:
        assert card_shift >= 0
        self.card_shift = card_shift
        self.func_storages: StaticVector[MutableBitStorage | None] = StaticVector(capacity=0)
        self.func_tables: StaticVector[BitView | None] = StaticVector(capacity=0)

    def allocate_functions(self, function_count: int) -> None:
        assert function_count >= 0
        self.func_storages = StaticVector.of((None,) * function_count, capacity=function_count)
        self.func_tables = StaticVector.of((None,) * function_count, capacity=function_count)

    @staticmethod
    def _split_pc(pc: int) -> tuple[int, int]:
        assert 0 <= pc <= 0xFFFF_FFFF
        return (pc >> 16, pc & 0xFFFF)

    def mark(self, pc: int, code_len: int) -> None:
        func_index, offset = self._split_pc(pc)
        assert func_index < len(self.func_tables)
        card = offset >> self.card_shift
        card_count = max(1, (code_len + (1 << self.card_shift) - 1) >> self.card_shift)
        assert card < card_count
        storage = self.func_storages[func_index]
        view = self.func_tables[func_index]
        if storage is None or view is None:
            storage = MutableBitStorage(count=card_count, bits=1)
            view = storage.view()
            self.func_storages[func_index] = storage
            self.func_tables[func_index] = view
        assert card < view.size()
        view.put(card, 1)

    def is_candidate(self, pc: int) -> bool:
        func_index, offset = self._split_pc(pc)
        if func_index >= len(self.func_tables):
            return False
        view = self.func_tables[func_index]
        if view is None:
            return False
        card = offset >> self.card_shift
        return card < view.size() and view.at(card) != 0


def score_opcodes(opcodes: Iterable[int], table: OpcodeBenefitTable) -> int:
    total = 0
    for opcode in opcodes:
        total += table.score(opcode)
    return total
