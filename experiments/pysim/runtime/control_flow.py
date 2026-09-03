"""
experiments/pysim/control_flow.py
Shared instruction decoding and block-nesting analysis, used by BOTH
interpreter.py and x64_jit.py so the two engines can never disagree about
where a block/loop/if actually ends -- exactly the kind of drift a real
dual-engine (interpreter + JIT) design has to rule out structurally, not by
convention.
For each BLOCK/LOOP/IF this resolves the byte-offset of its matching END
(and, for IF, its matching ELSE if present) in one linear forward pass with
an explicit stack -- no recursion, so it scales to real function bodies
without hitting Python's recursion limit.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from leb128 import decode_signed, decode_unsigned
from system_containers import (
    FlatMapView,
    RadixBinaryTreeView,
    ReadOnlyRadixBinaryTreeStorage,
    bswap32,
    build_radix_table,
)
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
    F32_ABS,
    F32_ADD,
    F32_CEIL,
    F32_CONST,
    F32_CONVERT_I32_S,
    F32_CONVERT_I32_U,
    F32_CONVERT_I64_S,
    F32_CONVERT_I64_U,
    F32_COPYSIGN,
    F32_DEMOTE_F64,
    F32_DIV,
    F32_EQ,
    F32_FLOOR,
    F32_GE,
    F32_GT,
    F32_LE,
    F32_LOAD,
    F32_LT,
    F32_MAX,
    F32_MIN,
    F32_MUL,
    F32_NE,
    F32_NEAREST,
    F32_NEG,
    F32_REINTERPRET_I32,
    F32_SQRT,
    F32_STORE,
    F32_SUB,
    F32_TRUNC,
    F64_ABS,
    F64_ADD,
    F64_CEIL,
    F64_CONST,
    F64_CONVERT_I32_S,
    F64_CONVERT_I32_U,
    F64_CONVERT_I64_S,
    F64_CONVERT_I64_U,
    F64_COPYSIGN,
    F64_DIV,
    F64_EQ,
    F64_FLOOR,
    F64_GE,
    F64_GT,
    F64_LE,
    F64_LOAD,
    F64_LT,
    F64_MAX,
    F64_MIN,
    F64_MUL,
    F64_NE,
    F64_NEAREST,
    F64_NEG,
    F64_PROMOTE_F32,
    F64_REINTERPRET_I64,
    F64_SQRT,
    F64_STORE,
    F64_SUB,
    F64_TRUNC,
    GLOBAL_GET,
    GLOBAL_SET,
    I32_ADD,
    I32_AND,
    I32_CONST,
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
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_REINTERPRET_F32,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
    I32_SUB,
    I32_TRUNC_F32_S,
    I32_TRUNC_F32_U,
    I32_TRUNC_F64_S,
    I32_TRUNC_F64_U,
    I32_WRAP_I64,
    I32_XOR,
    I64_ADD,
    I64_AND,
    I64_CLZ,
    I64_CONST,
    I64_CTZ,
    I64_DIV_S,
    I64_DIV_U,
    I64_EQ,
    I64_EQZ,
    I64_EXTEND_I32_S,
    I64_EXTEND_I32_U,
    I64_GE_S,
    I64_GE_U,
    I64_GT_S,
    I64_GT_U,
    I64_LE_S,
    I64_LE_U,
    I64_LOAD,
    I64_LT_S,
    I64_LT_U,
    I64_MUL,
    I64_NE,
    I64_OR,
    I64_POPCNT,
    I64_REINTERPRET_F64,
    I64_REM_S,
    I64_REM_U,
    I64_ROTL,
    I64_ROTR,
    I64_SHL,
    I64_SHR_S,
    I64_SHR_U,
    I64_STORE,
    I64_SUB,
    I64_TRUNC_F32_S,
    I64_TRUNC_F32_U,
    I64_TRUNC_F64_S,
    I64_TRUNC_F64_U,
    I64_XOR,
    IF,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    LOOP,
    MEMORY_GROW,
    MEMORY_SIZE,
    NOP,
    RETURN,
    SELECT,
    UNREACHABLE,
)
from wasm_reader import WasmUnsupportedFeatureError

_MEMARG_OPCODES = {
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
    I64_LOAD,
    F32_LOAD,
    F64_LOAD,
    I64_STORE,
    F32_STORE,
    F64_STORE,
}

_MEMORY_INDEX_OPCODES = {
    MEMORY_SIZE,
    MEMORY_GROW,
}  # followed by a single reserved 0x00 byte

_NO_OPERAND = {
    UNREACHABLE,
    NOP,
    ELSE,
    END,
    RETURN,
    DROP,
    SELECT,
    0x45,
    0x46,
    0x47,
    0x48,
    0x49,
    0x4A,
    0x4B,
    0x4C,
    0x4D,
    0x4E,
    0x4F,  # i32 compares
    0x67,
    0x68,
    0x69,  # i32 clz/ctz/popcnt
    0x6A,
    0x6B,
    0x6C,
    0x6D,
    0x6E,
    0x6F,
    0x70,
    0x71,
    0x72,
    0x73,
    0x74,
    0x75,
    0x76,  # i32 arith
    0x77,
    0x78,  # i32 rotl/rotr
    # i64 / f32 / f64 arithmetic & comparison ops (0 operands in instruction stream)
    I64_EQZ,
    I64_EQ,
    I64_NE,
    I64_LT_S,
    I64_LT_U,
    I64_GT_S,
    I64_GT_U,
    I64_LE_S,
    I64_LE_U,
    I64_GE_S,
    I64_GE_U,
    F32_EQ,
    F32_NE,
    F32_LT,
    F32_GT,
    F32_LE,
    F32_GE,
    F64_EQ,
    F64_NE,
    F64_LT,
    F64_GT,
    F64_LE,
    F64_GE,
    I64_CLZ,
    I64_CTZ,
    I64_POPCNT,
    I64_ADD,
    I64_SUB,
    I64_MUL,
    I64_DIV_S,
    I64_DIV_U,
    I64_REM_S,
    I64_REM_U,
    I64_AND,
    I64_OR,
    I64_XOR,
    I64_SHL,
    I64_SHR_S,
    I64_SHR_U,
    I64_ROTL,
    I64_ROTR,
    F32_ABS,
    F32_NEG,
    F32_CEIL,
    F32_FLOOR,
    F32_TRUNC,
    F32_NEAREST,
    F32_SQRT,
    F32_ADD,
    F32_SUB,
    F32_MUL,
    F32_DIV,
    F32_MIN,
    F32_MAX,
    F32_COPYSIGN,
    F64_ABS,
    F64_NEG,
    F64_CEIL,
    F64_FLOOR,
    F64_TRUNC,
    F64_NEAREST,
    F64_SQRT,
    F64_ADD,
    F64_SUB,
    F64_MUL,
    F64_DIV,
    F64_MIN,
    F64_MAX,
    F64_COPYSIGN,
    I32_WRAP_I64,
    I32_TRUNC_F32_S,
    I32_TRUNC_F32_U,
    I32_TRUNC_F64_S,
    I32_TRUNC_F64_U,
    I64_EXTEND_I32_S,
    I64_EXTEND_I32_U,
    I64_TRUNC_F32_S,
    I64_TRUNC_F32_U,
    I64_TRUNC_F64_S,
    I64_TRUNC_F64_U,
    F32_CONVERT_I32_S,
    F32_CONVERT_I32_U,
    F32_CONVERT_I64_S,
    F32_CONVERT_I64_U,
    F32_DEMOTE_F64,
    F64_CONVERT_I32_S,
    F64_CONVERT_I32_U,
    F64_CONVERT_I64_S,
    F64_CONVERT_I64_U,
    F64_PROMOTE_F32,
    I32_REINTERPRET_F32,
    I64_REINTERPRET_F64,
    F32_REINTERPRET_I32,
    F64_REINTERPRET_I64,
}

_LEB_UNSIGNED_OPERAND = {
    BR,
    BR_IF,
    CALL,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    GLOBAL_GET,
    GLOBAL_SET,
}

_BLOCK_OPENERS = {BLOCK, LOOP, IF}


@dataclass
class Instr:
    offset: int  # offset of the opcode byte itself
    opcode: int
    end_offset: int  # offset immediately after this instruction
    operand: int | None = (
        None  # depth / local index / func index / BR_TABLE's default label / CALL_INDIRECT's typeidx
    )
    const_value: int | None = None  # i32.const's decoded sleb128 value
    memarg: tuple[int, int] | None = None  # (align, offset) for load/store
    match_offset: int | None = None  # BLOCK/LOOP/IF -> its END's offset; ELSE -> its END's offset
    else_offset: int | None = None  # IF only: matching ELSE's offset, if present
    br_table_labels: list[int] | None = None  # BR_TABLE's vec(labelidx), default is in `operand`
    table_index: int | None = None  # CALL_INDIRECT's tableidx


def decode_all(code: bytes) -> FlatMapView[int, Instr]:
    """
    Decodes every instruction in `code` and resolves block nesting.
        Returns a flat_map_view<offset, Instr> (offsets are visited strictly
        increasing in this single forward pass, so the two arrays it wraps
        come out pre-sorted), so callers can do random-access lookups (the
        JIT needs this for branch targets; the interpreter walks it in order).
    """

    keys: list[int] = []
    values: list[Instr] = []
    open_stack: list[Instr] = []  # BLOCK/LOOP/IF instrs still awaiting their END
    off = 0
    n = len(code)
    while off < n:
        start = off
        opcode = code[off]
        off += 1
        operand = None
        const_value = None
        memarg = None
        br_table_labels = None
        table_index = None
        if opcode in _BLOCK_OPENERS:
            blocktype = code[off]
            off += 1
            assert blocktype == 0x40, "only the empty blocktype is supported in this experiment"
        elif opcode in _LEB_UNSIGNED_OPERAND:
            operand, off = decode_unsigned(code, off)
        elif opcode == I32_CONST:
            const_value, off = decode_signed(code, off)
        elif opcode == I64_CONST:
            const_value, off = decode_signed(code, off)
        elif opcode == F32_CONST:
            const_value = struct.unpack("<f", code[off : off + 4])[0]
            off += 4
        elif opcode == F64_CONST:
            const_value = struct.unpack("<d", code[off : off + 8])[0]
            off += 8
        elif opcode in _MEMARG_OPCODES:
            align, off = decode_unsigned(code, off)
            mem_offset, off = decode_unsigned(code, off)
            memarg = (align, mem_offset)
        elif opcode in _MEMORY_INDEX_OPCODES:
            reserved = code[off]
            off += 1
            assert reserved == 0, "only memory index 0 is supported"
            operand = reserved
        elif opcode == BR_TABLE:
            n_labels, off = decode_unsigned(code, off)
            br_table_labels = []
            for _ in range(n_labels):
                label, off = decode_unsigned(code, off)
                br_table_labels.append(label)

            operand, off = decode_unsigned(code, off)  # default label
        elif opcode == CALL_INDIRECT:
            operand, off = decode_unsigned(code, off)  # typeidx
            table_index, off = decode_unsigned(code, off)  # tableidx (0x00 in the MVP encoding)
        elif opcode in _NO_OPERAND:
            pass
        else:
            raise WasmUnsupportedFeatureError(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} is not supported"
            )

        instr = Instr(
            offset=start,
            opcode=opcode,
            end_offset=off,
            operand=operand,
            const_value=const_value,
            memarg=memarg,
            br_table_labels=br_table_labels,
            table_index=table_index,
        )
        keys.append(start)
        values.append(instr)
        if opcode in _BLOCK_OPENERS:
            open_stack.append(instr)
        elif opcode == ELSE:
            opener = open_stack[-1]
            assert opener.opcode == IF, "ELSE without a matching IF"
            opener.else_offset = start
        elif opcode == END:
            if open_stack:
                opener = open_stack.pop()
                opener.match_offset = start
                instr.match_offset = opener.offset  # END also points back to its opener

    assert not open_stack, "unterminated block/loop/if (missing END)"
    return InstructionTable(keys, values)


class InstructionTable:
    """
    Owning storage container for decoded WASM instructions.
    Explicitly owns the entries array, and provides non-owning FlatMapView via .view().
    `{Type_Vocabulary}` `{META_BinarySearch}`
    """

    __slots__ = ("_view", "entries")

    def __init__(self, keys: Sequence[int], values: Sequence[Instr]):
        self.entries = list(zip(keys, values, strict=False))
        self._view = FlatMapView(self.entries)

    @property
    def keys(self) -> list[int]:
        return [k for k, _ in self.entries]

    @property
    def values(self) -> list[Instr]:
        return [v for _, v in self.entries]

    def view(self) -> FlatMapView[int, Instr]:
        """Returns a non-owning FlatMapView borrowing the entries storage."""
        return self._view

    def find(self, offset: int) -> Instr | None:
        return self._view.find(offset)

    def slice(self, first: int, last: int) -> FlatMapView[int, Instr]:
        return self._view.slice(first, last)

    def narrow(self, lo: int, hi: int) -> FlatMapView[int, Instr]:
        return self._view.narrow(lo, hi)

    def __getitem__(self, offset: int) -> Instr:
        return self._view[offset]

    def __contains__(self, offset: int) -> bool:
        return offset in self._view

    def __len__(self) -> int:
        return len(self.keys)


def ordered(instrs: InstructionTable | FlatMapView[int, Instr]) -> list[Instr]:
    return list(instrs.values)


_OPCODE_TABLE: list[str | None] = [None] * 256

_OPCODE_TABLE[I32_CONST] = "i32.const"

_OPCODE_TABLE[I32_ADD] = "i32.add"

_OPCODE_TABLE[I32_SUB] = "i32.sub"

_OPCODE_TABLE[I32_MUL] = "i32.mul"

_OPCODE_TABLE[I32_DIV_S] = "i32.div_s"

_OPCODE_TABLE[I32_DIV_U] = "i32.div_u"

_OPCODE_TABLE[I32_AND] = "i32.and"

_OPCODE_TABLE[I32_OR] = "i32.or"

_OPCODE_TABLE[I32_XOR] = "i32.xor"

_OPCODE_TABLE[I32_SHL] = "i32.shl"

_OPCODE_TABLE[I32_SHR_S] = "i32.shr_s"

_OPCODE_TABLE[I32_SHR_U] = "i32.shr_u"

_OPCODE_TABLE[LOCAL_GET] = "local.get"

_OPCODE_TABLE[LOCAL_SET] = "local.set"

_OPCODE_TABLE[LOCAL_TEE] = "local.tee"

_OPCODE_TABLE[GLOBAL_GET] = "global.get"

_OPCODE_TABLE[GLOBAL_SET] = "global.set"

_OPCODE_TABLE[I32_EQZ] = "i32.eqz"

_OPCODE_TABLE[I32_EQ] = "i32.eq"

_OPCODE_TABLE[I32_NE] = "i32.ne"

_OPCODE_TABLE[I32_LT_S] = "i32.lt_s"

_OPCODE_TABLE[I32_LT_U] = "i32.lt_u"

_OPCODE_TABLE[I32_GT_S] = "i32.gt_s"

_OPCODE_TABLE[I32_GT_U] = "i32.gt_u"

_OPCODE_TABLE[I32_LE_S] = "i32.le_s"

_OPCODE_TABLE[I32_LE_U] = "i32.le_u"

_OPCODE_TABLE[I32_GE_S] = "i32.ge_s"

_OPCODE_TABLE[I32_GE_U] = "i32.ge_u"

_OPCODE_TABLE[DROP] = "drop"
_OPCODE_TABLE[SELECT] = "select"
_OPCODE_TABLE[RETURN] = "return"
_OPCODE_TABLE[CALL] = "call"
_OPCODE_TABLE[I32_LOAD] = "i32.load"
_OPCODE_TABLE[I32_LOAD8_S] = "i32.load8_s"
_OPCODE_TABLE[I32_LOAD8_U] = "i32.load8_u"
_OPCODE_TABLE[I32_LOAD16_S] = "i32.load16_s"
_OPCODE_TABLE[I32_LOAD16_U] = "i32.load16_u"
_OPCODE_TABLE[I32_STORE] = "i32.store"
_OPCODE_TABLE[I32_STORE8] = "i32.store8"
_OPCODE_TABLE[I32_STORE16] = "i32.store16"


def extract_basic_blocks(
    code: bytes, func_index: int = 0
) -> list[tuple[int, list[tuple[str, object]], int | None]]:
    """Extracts straight-line BasicBlocks from WASM bytecode as a flat list.
    Returns [(head_pc, [(op_name, arg), ...], next_pc), ...] where head_pc = (func_index << 16) | offset."""
    from wasm_opcodes import BLOCK, BR, BR_IF, ELSE, END, IF, LOOP, RETURN

    instrs = decode_all(code)
    sorted_instrs = list(instrs.values)
    base_pc = func_index << 16
    blocks: list[tuple[int, list[tuple[str, object]], int | None]] = []
    cur_ops: list[tuple[str, object]] = []
    cur_head: int | None = None
    for ins in sorted_instrs:
        pc = base_pc | ins.offset
        if cur_head is None:
            cur_head = pc

        op_name = _OPCODE_TABLE[ins.opcode] if ins.opcode < 256 else None
        if op_name is not None:
            if ins.const_value is not None:
                arg = ins.const_value
            elif ins.memarg is not None:
                arg = ins.memarg[1]  # static offset
            else:
                arg = ins.operand
            cur_ops.append((op_name, arg))

        # Check if this instruction ends the basic block
        if ins.opcode in (
            BR,
            BR_IF,
            RETURN,
            END,
            ELSE,
            LOOP,
            BLOCK,
            IF,
            CALL,
            CALL_INDIRECT,
        ):
            if cur_ops:
                blocks.append((cur_head, list(cur_ops), base_pc | ins.offset))
                cur_ops.clear()

            cur_head = None

    if cur_head is not None and cur_ops:
        blocks.append((cur_head, list(cur_ops), None))
    return blocks


def build_control_skip_storage(
    functions: Sequence[object], n_imports: int = 0
) -> ReadOnlyRadixBinaryTreeStorage[int] | None:
    """Constructs a ReadOnlyRadixBinaryTreeStorage owning delimiter PCs -> fallthrough basic block head PCs.

    Key byte order is inverted using bswap32 to maximize entropy in the upper bits
    for uniform Radix Table prefix distribution. Backing buffers are owned by this storage.
    """
    pairs: list[tuple[int, int]] = []
    for idx, fn in enumerate(functions):
        func_idx = n_imports + idx
        base_pc = func_idx << 16
        code = getattr(fn, "code", None)
        if not code:
            continue
        instrs = decode_all(code)
        blocks = extract_basic_blocks(code, func_index=func_idx)
        heads = {b[0] for b in blocks}
        for _head_pc, _ops, delim_pc in blocks:
            if delim_pc is not None:
                offset = delim_pc & 0xFFFF
                if offset in instrs:
                    ins = instrs[offset]
                    fallthrough_pc = base_pc | ins.end_offset
                    if fallthrough_pc in heads:
                        pairs.append((delim_pc, fallthrough_pc))

    if not pairs:
        return None

    # Key byte order is inverted via bswap32
    sorted_pairs = sorted(pairs, key=lambda p: bswap32(p[0]))
    inv_keys = [bswap32(p[0]) for p in sorted_pairs]
    fallthrough_heads = [p[1] for p in sorted_pairs]

    # Compact 4-bit prefix Radix Table (<= 16 buckets / 17 entries)
    radix_shift = 28
    radix_table = build_radix_table(inv_keys, radix_shift=radix_shift)
    return ReadOnlyRadixBinaryTreeStorage(
        keys=inv_keys,
        values=fallthrough_heads,
        radix_table=radix_table,
        radix_shift=radix_shift,
        entries=list(zip(inv_keys, fallthrough_heads, strict=False)),
    )


def build_control_skip_tree(
    functions: Sequence[object], n_imports: int = 0
) -> RadixBinaryTreeView[int] | None:
    """Borrows a non-owning RadixBinaryTreeView over the constructed storage."""
    storage = build_control_skip_storage(functions, n_imports=n_imports)
    return storage.view() if storage is not None else None
