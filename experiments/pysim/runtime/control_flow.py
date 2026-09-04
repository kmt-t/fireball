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

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from leb128 import decode_signed, decode_unsigned
from system_containers import (
    BitView,
    FlatMapView,
    MutableBitStorage,
    RadixBinaryTreeView,
    ReadOnlyBitStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    bswap32,
    build_radix_table,
)
from wasm_module import Function
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


def _opcode_bitview(*opcodes: int) -> BitView:
    """
    Builds a frozen, read-only 1-bit-per-opcode (32 bytes total) membership
    table for one of the fixed opcode-class checks below, in place of a
    Python `set` (a hash table with no real embedded-target equivalent).
    """
    storage = MutableBitStorage(count=256, bits=1)
    for _op in opcodes:
        storage.put(_op, 1)
    return ReadOnlyBitStorage(bytes(storage.buffer), bits=1, count=256).view()


_MEMARG_OPCODES = _opcode_bitview(
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
)

_MEMORY_INDEX_OPCODES = _opcode_bitview(
    MEMORY_SIZE,
    MEMORY_GROW,
)  # followed by a single reserved 0x00 byte

_NO_OPERAND = _opcode_bitview(
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
)

_LEB_UNSIGNED_OPERAND = _opcode_bitview(
    BR,
    BR_IF,
    CALL,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    GLOBAL_GET,
    GLOBAL_SET,
)

_BLOCK_OPENERS = _opcode_bitview(BLOCK, LOOP, IF)

# {Policy_Memory}: no dynamically-growing container (a Python list grown via
# .append()/.pop() models a heap-backed std::vector, which is banned by
# .agents/rules/coding-standards-cpp.md's dynamic-memory prohibition) --
# every open BLOCK/LOOP/IF nesting stack in this module is instead a single
# fixed-size buffer (`[None] * FB_CONF_MAX_NESTING_DEPTH`, allocated once,
# indexed by an explicit depth counter, exactly mirroring a real
# `std::array<Opener, FB_CONF_MAX_NESTING_DEPTH>` + `size_t depth`) that a
# pathologically over-nested function overflows into an explicit rejection
# rather than growing without bound. Not yet a documented requirement
# (docs/requires/requirement_list.md has no existing nesting-depth bound);
# chosen to match this codebase's other FB_CONF_* capacities (e.g.
# core/scheduler.py's FB_CONF_MAX_TASKS=16) until a real spec value exists.
FB_CONF_MAX_NESTING_DEPTH = 32


@dataclass
class Instr:
    """
    Minimal per-instruction descriptor for basic-block/control-flow
    scanning (`iter_scan_instrs`) -- exactly the fields that resolving
    block boundaries and branch targets needs, and no others. Immediate
    values a block-scan never inspects (`i32.const`'s decoded value,
    memarg align/offset, `br_table`'s full label vector, `call_indirect`'s
    tableidx) are walked past to find the next instruction's offset, but
    never decoded into a stored field -- see `iter_scan_instrs`.
    """

    offset: int  # offset of the opcode byte itself
    opcode: int
    end_offset: int  # offset immediately after this instruction
    operand: int | None = (
        None  # depth / local index / func index / BR_TABLE's default label / CALL_INDIRECT's typeidx
    )


@dataclass
class ControlMap:
    """Pre-indexed block delimiters and br_table labels for direct bytecode interpretation.
    A sorted (key, value) array with O(log N) binary search lookup (`FlatMapView`, matching
    `{Type_Vocabulary}`/`{META_BinarySearch}`), never a hash map -- a real embedded target has
    no dynamically-resized hash table to reach for."""

    blocks: FlatMapView[int, tuple[int, int | None]]  # opener_ip -> (match_end_ip, else_offset)
    br_tables: FlatMapView[int, tuple[list[int], int]]  # br_table_ip -> (labels, default_label)


def build_control_map(code: bytes) -> ControlMap:
    """Single linear scan over WASM bytecode to resolve block structure and br_tables once per function."""
    block_entries: list[tuple[int, tuple[int, int | None]]] = []
    br_table_entries: list[tuple[int, tuple[list[int], int]]] = []
    # [opcode, start_offset, else_offset] per still-open BLOCK/LOOP/IF, in a
    # fixed-size buffer indexed by `depth` (see FB_CONF_MAX_NESTING_DEPTH) --
    # else_offset is filled in place when this entry's own ELSE is reached,
    # read back when its own END pops it.
    open_stack: list[list[int | None] | None] = [None] * FB_CONF_MAX_NESTING_DEPTH
    depth = 0

    off = 0
    n = len(code)
    while off < n:
        start = off
        opcode = code[off]
        off += 1
        if _BLOCK_OPENERS.at(opcode):
            blocktype = code[off]
            off += 1
            assert blocktype == 0x40, "only the empty blocktype is supported in this experiment"
            if depth >= FB_CONF_MAX_NESTING_DEPTH:
                raise WasmUnsupportedFeatureError(
                    "ERR_WASM_UNSUPPORTED_FEATURE: block/loop/if nesting exceeds "
                    f"FB_CONF_MAX_NESTING_DEPTH={FB_CONF_MAX_NESTING_DEPTH} at offset {start}"
                )
            open_stack[depth] = [opcode, start, None]
            depth += 1
        elif _LEB_UNSIGNED_OPERAND.at(opcode):
            _, off = decode_unsigned(code, off)
        elif opcode in (I32_CONST, I64_CONST):
            _, off = decode_signed(code, off)
        elif opcode == F32_CONST:
            off += 4
        elif opcode == F64_CONST:
            off += 8
        elif _MEMARG_OPCODES.at(opcode):
            _, off = decode_unsigned(code, off)
            _, off = decode_unsigned(code, off)
        elif _MEMORY_INDEX_OPCODES.at(opcode):
            off += 1  # reserved
        elif opcode == BR_TABLE:
            n_labels, off = decode_unsigned(code, off)
            labels = []
            for _ in range(n_labels):
                lbl, off = decode_unsigned(code, off)
                labels.append(lbl)
            default_lbl, off = decode_unsigned(code, off)
            br_table_entries.append((start, (labels, default_lbl)))
        elif opcode == CALL_INDIRECT:
            _, off = decode_unsigned(code, off)
            _, off = decode_unsigned(code, off)
        elif opcode == ELSE:
            opener = open_stack[depth - 1]
            assert opener is not None and opener[0] == IF, "ELSE without matching IF"
            opener[2] = start
        elif opcode == END:
            if depth > 0:
                depth -= 1
                opener = open_stack[depth]
                assert opener is not None
                _opener_op, opener_start, else_offset = opener
                open_stack[depth] = None
                block_entries.append((opener_start, (start, else_offset)))
        elif _NO_OPERAND.at(opcode):
            pass
        else:
            raise WasmUnsupportedFeatureError(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} is not supported"
            )

    assert depth == 0, "unterminated block/loop/if (missing END)"
    return ControlMap(
        blocks=ReadOnlyFlatMapStorage.create(block_entries).view(),
        br_tables=ReadOnlyFlatMapStorage.create(br_table_entries).view(),
    )


def iter_scan_instrs(code: bytes, start: int = 0) -> Iterator[Instr]:
    """
    Streams every instruction in `code[start:]` as a freshly-decoded, minimal
    `Instr` (offset/opcode/end_offset/operand only), one at a time in
    strictly increasing offset order -- never materializes more than the
    single instruction currently being yielded, and never decodes an
    immediate this scan itself has no use for (see `Instr`): a const's
    value, a memarg's align/offset, a `br_table`'s full label vector, and
    `call_indirect`'s tableidx are all walked past byte-for-byte to find
    the next instruction's offset, never unpacked or stored. A caller that
    needs to look back at an enclosing BLOCK/LOOP/IF (e.g.
    `extract_basic_blocks`'s `active_openers`) keeps its own O(nesting
    depth) stack of exactly the instructions it still needs, rather than
    this function holding every instruction of the whole function for it.
    A caller that only wants the single instruction sitting at a known
    offset (e.g. `build_control_skip_storage`) gets it via
    `next(iter_scan_instrs(code, offset))` -- being a generator, this
    decodes exactly that one instruction and no more, not the whole
    function up to it.
    """
    off = start
    n = len(code)
    while off < n:
        start = off
        opcode = code[off]
        off += 1
        operand = None
        if _BLOCK_OPENERS.at(opcode):
            blocktype = code[off]
            off += 1
            assert blocktype == 0x40, "only the empty blocktype is supported in this experiment"
        elif _LEB_UNSIGNED_OPERAND.at(opcode):
            operand, off = decode_unsigned(code, off)
        elif opcode in (I32_CONST, I64_CONST):
            _, off = decode_signed(code, off)  # value unused by block-boundary scanning
        elif opcode == F32_CONST:
            off += 4
        elif opcode == F64_CONST:
            off += 8
        elif _MEMARG_OPCODES.at(opcode):
            _, off = decode_unsigned(code, off)  # align, unused
            _, off = decode_unsigned(code, off)  # mem_offset, unused
        elif _MEMORY_INDEX_OPCODES.at(opcode):
            reserved = code[off]
            off += 1
            assert reserved == 0, "only memory index 0 is supported"
        elif opcode == BR_TABLE:
            n_labels, off = decode_unsigned(code, off)
            for _ in range(n_labels):
                _, off = decode_unsigned(
                    code, off
                )  # label, unused (see wasm_opcodes.BR_TABLE handling)
            operand, off = decode_unsigned(code, off)  # default label
        elif opcode == CALL_INDIRECT:
            operand, off = decode_unsigned(code, off)  # typeidx
            _, off = decode_unsigned(code, off)  # tableidx (0x00 in the MVP encoding), unused
        elif _NO_OPERAND.at(opcode):
            pass
        else:
            raise WasmUnsupportedFeatureError(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} is not supported"
            )

        yield Instr(offset=start, opcode=opcode, end_offset=off, operand=operand)


_IS_BB_OPCODE_BUILD = MutableBitStorage(count=256, bits=1)
for _op in (
    I32_CONST,
    I32_ADD,
    I32_SUB,
    I32_MUL,
    I32_DIV_S,
    I32_DIV_U,
    I32_AND,
    I32_OR,
    I32_XOR,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    GLOBAL_GET,
    GLOBAL_SET,
    I32_EQZ,
    I32_EQ,
    I32_NE,
    I32_LT_S,
    I32_LT_U,
    I32_GT_S,
    I32_GT_U,
    I32_LE_S,
    I32_LE_U,
    I32_GE_S,
    I32_GE_U,
    DROP,
    SELECT,
    CALL,
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
):
    _IS_BB_OPCODE_BUILD.put(_op, 1)
# Read-only 1-bit-per-opcode membership table (32 bytes total, not a
# 256-slot Python list of bool object pointers): frozen once at import
# time, never mutated again.
_IS_BB_OPCODE: BitView = ReadOnlyBitStorage(
    bytes(_IS_BB_OPCODE_BUILD.buffer), bits=1, count=256
).view()


def iter_block_ops(code: bytes, head_offset: int, byte_span: int) -> Iterator[tuple[int, object]]:
    """
    Streams ONE BasicBlock's compilable `(opcode, arg)` op stream directly
    from raw bytecode, scoped to exactly `[head_offset, head_offset+byte_span)`,
    one instruction at a time -- never materializes the whole block's op
    list. Called on demand, at the moment a block is actually compiled or
    interpreted (see `wasm_module.BasicBlock` / `TraceBlock`). A block's own
    byte_span, by construction (see `extract_basic_blocks`), spans only
    BB-opcode instructions, so every instruction decoded in range belongs in
    the result -- no filtering needed here.
    """
    off = head_offset
    end = head_offset + byte_span
    while off < end:
        start = off
        opcode = code[off]
        off += 1
        if _LEB_UNSIGNED_OPERAND.at(opcode):
            operand, off = decode_unsigned(code, off)
            arg: object = operand
        elif opcode == I32_CONST:
            arg, off = decode_signed(code, off)
        elif _MEMARG_OPCODES.at(opcode):
            _align, off = decode_unsigned(code, off)
            mem_offset, off = decode_unsigned(code, off)
            arg = mem_offset
        elif _NO_OPERAND.at(opcode):
            arg = None
        else:
            raise WasmUnsupportedFeatureError(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} "
                "is not a supported basic-block opcode"
            )
        yield (opcode, arg)


def extract_basic_blocks(
    code: bytes, func_index: int = 0
) -> list[tuple[int, int | None, int | None, int, int]]:
    """Extracts straight-line BasicBlock PC ranges from WASM bytecode as a flat list.
    Each entry is: (head_pc, next_pc, loops_to, frame_depth, byte_span).
    head_pc = (func_index << 16) | start_offset. frame_depth is the count of
    enclosing BLOCK/LOOP/IF frames the interpreter's frame.frames stack must
    hold once execution resumes at head_pc. byte_span is this block's own
    instruction-stream length -- deliberately NOT `next_pc - head_pc`, which
    is negative for a block whose terminator branches backward (a loop
    body's own `br` to the loop's head), and would wrongly read as "shorter
    than min_trace_bytes" and permanently disqualify that block -- usually
    the hottest one in the function -- from JIT. Does not return each
    block's decoded op stream: callers that need it (JIT compilation, block
    interpretation) derive it on demand via `iter_block_ops`, scoped to
    just the one block being compiled/interpreted right now -- see
    `wasm_module.BasicBlock` for why this is never precomputed and stored
    here for every block up front.
    """
    from wasm_opcodes import BLOCK, BR, BR_IF, ELSE, END, IF, LOOP, RETURN

    control_map = build_control_map(code)
    instr_stream = iter_scan_instrs(code)

    def _skip_trailing_ends(offset: int) -> int:
        # A branch/if-skip target computed as "one past a matching END" can
        # itself land exactly on the NEXT enclosing block/loop/if's own
        # closing END (nested constructs sharing one exit point). None of
        # those bare structural opcodes were ever entered via _h_block /
        # _h_loop / _h_if for a JIT-bypassed trace, so walk past every
        # consecutive END to the first real instruction -- landing on one
        # would otherwise pop the interpreter's frame stack for a frame
        # that was never pushed. END is a single, no-operand byte (0x0B),
        # so this is a direct raw-byte scan -- no per-instruction Instr
        # lookup table needed for it.
        while offset < len(code) and code[offset] == END:
            offset += 1
        return offset

    base_pc = func_index << 16
    blocks: list[tuple[int, int | None, int | None, int, int]] = []
    cur_op_count = 0  # count only -- the ops themselves are never materialized here
    cur_head: int | None = None
    cur_frame_depth = 0
    cur_span_end = 0  # local offset just past the last BB-opcode instruction seen
    # Fixed-size buffer of still-open BLOCK/LOOP/IF instructions, indexed by
    # active_openers_depth (see FB_CONF_MAX_NESTING_DEPTH) -- never a
    # dynamically-growing list ({Policy_Memory}).
    active_openers: list[Instr | None] = [None] * FB_CONF_MAX_NESTING_DEPTH
    active_openers_depth = 0

    for ins in instr_stream:
        pc = base_pc | ins.offset
        if cur_head is None:
            cur_head = pc
            # The nesting depth (count of enclosing BLOCK/LOOP/IF frames)
            # that must be active in the interpreter's frame.frames stack
            # once execution resumes here -- recorded from this single
            # linear scan, so it stays correct regardless of whether a
            # given visit at runtime arrives via interp.step() or a JIT
            # jump that skipped the frame push/pop entirely.
            cur_frame_depth = active_openers_depth
            cur_span_end = ins.offset

        if _BLOCK_OPENERS.at(ins.opcode):
            if active_openers_depth >= FB_CONF_MAX_NESTING_DEPTH:
                raise WasmUnsupportedFeatureError(
                    "ERR_WASM_UNSUPPORTED_FEATURE: block/loop/if nesting exceeds "
                    f"FB_CONF_MAX_NESTING_DEPTH={FB_CONF_MAX_NESTING_DEPTH} at offset {ins.offset}"
                )
            active_openers[active_openers_depth] = ins
            active_openers_depth += 1

        if ins.opcode < 256 and _IS_BB_OPCODE.at(ins.opcode):
            cur_op_count += 1
            cur_span_end = ins.end_offset

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
            if cur_op_count:
                branch_target = None
                if (
                    ins.opcode in (BR, BR_IF)
                    and ins.operand is not None
                    and ins.operand < active_openers_depth
                ):
                    target = active_openers[active_openers_depth - 1 - ins.operand]
                    assert target is not None
                    if target.opcode == LOOP:
                        # Backward continuation: br/br_if taken jumps to the
                        # loop's own start (re-enter the loop body).
                        branch_target = base_pc | target.end_offset
                    else:
                        # Forward exit: br/br_if taken jumps past the block/if's
                        # matching END (block/if labels resume after, unlike
                        # loop labels which resume at the top).
                        match = control_map.blocks.find(target.offset)
                        if match is not None:
                            match_end_ip, _else_offset = match
                            branch_target = base_pc | _skip_trailing_ends(match_end_ip + 1)

                if ins.opcode == BR:
                    # Unconditional: the branch target is the block's only
                    # successor, never a "fallthrough" past this instruction.
                    next_pc = branch_target
                    loops_to = None
                elif ins.opcode == RETURN:
                    next_pc = None
                    loops_to = None
                elif ins.opcode == IF:
                    # Conditional entry: the just-computed condition decides
                    # between the then-body (right after this IF) and the
                    # else-body / past-END (condition false, then-body
                    # skipped entirely) -- reuses the same cond!=0 -> loops_to
                    # / cond==0 -> next_pc contract as BR_IF.
                    then_target = base_pc | ins.end_offset
                    skip_target = then_target
                    match = control_map.blocks.find(ins.offset)
                    if match is not None:
                        match_end_ip, else_offset = match
                        skip_target = base_pc | (
                            (else_offset + 1)
                            if else_offset is not None
                            else _skip_trailing_ends(match_end_ip + 1)
                        )
                    next_pc = skip_target
                    loops_to = then_target
                elif ins.opcode == ELSE and active_openers_depth > 0:
                    # Finished running the then-body: never fall into the
                    # else-body behind it -- skip straight past the matching
                    # END.
                    if_opener = active_openers[active_openers_depth - 1]
                    assert if_opener is not None
                    match = control_map.blocks.find(if_opener.offset)
                    next_pc = (
                        base_pc | _skip_trailing_ends(match[0] + 1)
                        if match is not None
                        else base_pc | ins.end_offset
                    )
                    loops_to = None
                else:
                    next_pc = base_pc | _skip_trailing_ends(ins.end_offset)
                    loops_to = branch_target if ins.opcode == BR_IF else None

                byte_span = cur_span_end - (cur_head & 0xFFFF)
                blocks.append((cur_head, next_pc, loops_to, cur_frame_depth, byte_span))
                cur_op_count = 0

            cur_head = None

        if ins.opcode == END and active_openers_depth > 0:
            active_openers_depth -= 1
            active_openers[active_openers_depth] = None

    if cur_head is not None and cur_op_count:
        byte_span = cur_span_end - (cur_head & 0xFFFF)
        blocks.append((cur_head, None, None, cur_frame_depth, byte_span))
    return blocks


def build_control_skip_storage(
    functions: Sequence[Function], n_imports: int = 0
) -> ReadOnlyRadixBinaryTreeStorage[int] | None:
    """Constructs a ReadOnlyRadixBinaryTreeStorage owning delimiter PCs -> fallthrough basic block head PCs.

    Key byte order is inverted using bswap32 to maximize entropy in the upper bits
    for uniform Radix Table prefix distribution. Backing buffers are owned by this storage.
    """
    pairs: list[tuple[int, int]] = []
    for idx, fn in enumerate(functions):
        func_idx = n_imports + idx
        base_pc = func_idx << 16
        code = fn.code
        if not code:
            continue
        blocks = extract_basic_blocks(code, func_index=func_idx)
        heads = {b[0] for b in blocks}
        for _head_pc, delim_pc, _loops_to, _frame_depth, _byte_span in blocks:
            if delim_pc is not None:
                offset = delim_pc & 0xFFFF
                if offset < len(code):
                    # Decodes just this one instruction (see iter_scan_instrs),
                    # never the whole function up to it.
                    ins = next(iter_scan_instrs(code, offset))
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
