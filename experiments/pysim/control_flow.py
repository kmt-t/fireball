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

from dataclasses import dataclass

from leb128 import decode_signed, decode_unsigned
from wasm_opcodes import (
    BLOCK, BR, BR_IF, BR_TABLE, CALL, CALL_INDIRECT, DROP, ELSE, END, GLOBAL_GET,
    GLOBAL_SET, I32_CONST, I32_LOAD, I32_LOAD8_S, I32_LOAD8_U, I32_LOAD16_S,
    I32_LOAD16_U, I32_STORE, I32_STORE8, I32_STORE16, IF, LOCAL_GET, LOCAL_SET,
    LOCAL_TEE, LOOP, MEMORY_GROW, MEMORY_SIZE, NOP, RETURN, SELECT, UNREACHABLE,
)

_MEMARG_OPCODES = {I32_LOAD, I32_LOAD8_S, I32_LOAD8_U, I32_LOAD16_S, I32_LOAD16_U,
                    I32_STORE, I32_STORE8, I32_STORE16}
_MEMORY_INDEX_OPCODES = {MEMORY_SIZE, MEMORY_GROW}   # followed by a single reserved 0x00 byte

_NO_OPERAND = {
    UNREACHABLE, NOP, ELSE, END, RETURN, DROP, SELECT,
    0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,   # i32 compares
    0x67, 0x68, 0x69,                                                    # i32 clz/ctz/popcnt
    0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76,  # i32 arith
    0x77, 0x78,                                                          # i32 rotl/rotr
}
_LEB_UNSIGNED_OPERAND = {BR, BR_IF, CALL, LOCAL_GET, LOCAL_SET, LOCAL_TEE, GLOBAL_GET, GLOBAL_SET}
_BLOCK_OPENERS = {BLOCK, LOOP, IF}


@dataclass
class Instr:
    offset: int                     # offset of the opcode byte itself
    opcode: int
    end_offset: int                 # offset immediately after this instruction
    operand: int | None = None      # depth / local index / func index / BR_TABLE's default label / CALL_INDIRECT's typeidx
    const_value: int | None = None  # i32.const's decoded sleb128 value
    memarg: tuple[int, int] | None = None   # (align, offset) for load/store
    match_offset: int | None = None  # BLOCK/LOOP/IF -> its END's offset; ELSE -> its END's offset
    else_offset: int | None = None   # IF only: matching ELSE's offset, if present
    br_table_labels: list[int] | None = None   # BR_TABLE's vec(labelidx), default is in `operand`
    table_index: int | None = None   # CALL_INDIRECT's tableidx


def decode_all(code: bytes) -> dict[int, Instr]:
    """Decodes every instruction in `code` and resolves block nesting.
    Returns {offset: Instr}, so callers can do random-access lookups (the
    JIT needs this for branch targets; the interpreter walks it in order).
    """
    instrs: dict[int, Instr] = {}
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
            operand, off = decode_unsigned(code, off)   # default label
        elif opcode == CALL_INDIRECT:
            operand, off = decode_unsigned(code, off)       # typeidx
            table_index, off = decode_unsigned(code, off)   # tableidx (0x00 in the MVP encoding)
        elif opcode in _NO_OPERAND:
            pass
        else:
            raise NotImplementedError(f"opcode 0x{opcode:02X} at offset {start} is not supported")

        instr = Instr(offset=start, opcode=opcode, end_offset=off, operand=operand,
                       const_value=const_value, memarg=memarg,
                       br_table_labels=br_table_labels, table_index=table_index)
        instrs[start] = instr

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
    return instrs


def ordered(instrs: dict[int, Instr]) -> list[Instr]:
    return [instrs[k] for k in sorted(instrs.keys())]
