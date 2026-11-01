"""
experiments/pysim/tier2_runtime/interpreter/control_flow.py
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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import IntFlag

from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_NESTING_DEPTH
from system_containers import (
    MutableBitStorage,
    ReadOnlyBitStorage,
    ReadOnlyFlatMapStorage,
    SequenceView,
    StaticVector,
    freeze_sequence,
)
from tier2_runtime.abi.integer_types import (
    annotation_offset_t,
    block_index_t,
    frame_word_offset_t,
    function_index_t,
    global_index_t,
    instruction_width_t,
    type_index_t,
)
from tier2_runtime.wasm.leb128 import (
    decode_signed_full,
    decode_unsigned_full,
)
from tier2_runtime.wasm.module import (
    ControlBlockMetadata,
    InterpreterAnnotationPool,
    LocalLayout,
    WasmOperand,
)
from tier2_runtime.wasm.opcodes import (
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
    FC_I32_TRUNC_SAT_F32_S,
    FC_I64_TRUNC_SAT_F64_U,
    FC_MEMORY_COPY,
    FC_MEMORY_FILL,
    FC_PREFIX,
    GLOBAL_GET,
    GLOBAL_SET,
    I32_CONST,
    I32_EXTEND8_S,
    I32_EXTEND16_S,
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_REINTERPRET_F32,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
    I32_TRUNC_F32_S,
    I32_TRUNC_F32_U,
    I32_TRUNC_F64_S,
    I32_TRUNC_F64_U,
    I32_WRAP_I64,
    I64_ADD,
    I64_AND,
    I64_CLZ,
    I64_CONST,
    I64_CTZ,
    I64_DIV_S,
    I64_DIV_U,
    I64_EQ,
    I64_EQZ,
    I64_EXTEND8_S,
    I64_EXTEND16_S,
    I64_EXTEND32_S,
    I64_EXTEND_I32_S,
    I64_EXTEND_I32_U,
    I64_GE_S,
    I64_GE_U,
    I64_GT_S,
    I64_GT_U,
    I64_LE_S,
    I64_LE_U,
    I64_LOAD,
    I64_LOAD8_S,
    I64_LOAD8_U,
    I64_LOAD16_S,
    I64_LOAD16_U,
    I64_LOAD32_S,
    I64_LOAD32_U,
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
    I64_STORE8,
    I64_STORE16,
    I64_STORE32,
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
    NATIVE_LOCAL_GET_W1,
    NATIVE_LOCAL_SET_W1,
    NOP,
    RETURN,
    RPN_ELEM_BINOP_BASE,
    RPN_ELEM_END,
    RPN_ELEM_END_STACK,
    RPN_ELEM_PUSH_IMM,
    RPN_ELEM_PUSH_LOCAL,
    RPN_ELEM_SPEC_BASE,
    RPN_ELEM_UNARY_BASE,
    RPN_EXPR_HEAD,
    RPN_MAX_DEPTH,
    RPN_SPECIALISED_FORMS,
    SELECT,
    UNREACHABLE,
)
from tier2_runtime.wasm.validation import reject_wasm, require_wasm

ControlBlock = tuple[int, int | None, int]
BR_TABLE_COUNT_BYTES = 2
BR_TABLE_TARGET_DEPTH_BYTES = 1
BR_TABLE_TARGET_INDEX_BYTES = 2
BR_TABLE_TARGET_STACK_BASE_BYTES = 1
BR_TABLE_TARGET_RESULT_WORDS_BYTES = 1
BR_TABLE_TARGET_BYTES = (
    BR_TABLE_TARGET_INDEX_BYTES
    + BR_TABLE_TARGET_STACK_BASE_BYTES
    + BR_TABLE_TARGET_RESULT_WORDS_BYTES
)


def _decode_blocktype(code: memoryview, offset: int) -> tuple[int, int]:
    """Decode a supported blocktype and return (native-slot-count, next-offset)."""

    require_wasm(0 <= offset < len(code), "truncated blocktype")
    blocktype = code[offset]
    if blocktype == 0x40:
        return 0, offset + 1
    if blocktype == 0x7F or blocktype == 0x7D:  # i32/f32 occupy one raw stack slot
        return 1, offset + 1
    if blocktype == 0x7E or blocktype == 0x7C:  # i64/f64 occupy two raw stack slots
        return 2, offset + 1
    reject_wasm(
        "ERR_WASM_UNSUPPORTED_FEATURE: blocktype must be empty or a single "
        f"value type, got 0x{blocktype:02X}"
    )


class OpcodeAttribute(IntFlag):
    """Static attributes attached to each byte-sized WASM opcode."""

    CONTROL_LOOP = 1
    BASIC_BLOCK_BOUNDARY = 2
    CALL = 4
    BRANCH = 8


def _opcode_table(*opcodes: int) -> ReadOnlyBitStorage:
    """
    Builds a frozen, read-only 1-bit-per-opcode (32 bytes total) membership
    table for one of the fixed opcode-class checks below, in place of a
    Python `set` (a hash table with no real embedded-target equivalent).
    """
    storage = MutableBitStorage(count=256, bits=1)
    for _op in opcodes:
        storage.put(_op, 1)
    return ReadOnlyBitStorage(bytes(storage.buffer), bits=1, count=256)


def _build_opcode_attributes() -> ReadOnlyBitStorage:
    """Freeze 256 four-bit attributes without retaining the setup buffer."""
    storage = MutableBitStorage(count=256, bits=4)
    for _opcode, _attributes in (
        (LOOP, OpcodeAttribute.CONTROL_LOOP | OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (IF, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (ELSE, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (END, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (BR, OpcodeAttribute.BRANCH | OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (BR_IF, OpcodeAttribute.BRANCH | OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (BR_TABLE, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (RETURN, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (CALL, OpcodeAttribute.CALL | OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
        (CALL_INDIRECT, OpcodeAttribute.CALL | OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
    ):
        storage.put(_opcode, int(_attributes))

    return ReadOnlyBitStorage(bytes(storage.buffer), bits=4, count=256)


OPCODE_ATTRIBUTES = _build_opcode_attributes()


def opcode_has_attribute(opcode: int, attribute: OpcodeAttribute) -> bool:
    """Returns whether the loader's immutable opcode metadata has ``attribute``."""

    # Keep the immutable table as the single metadata source.  This is a
    # fixed four-bit packed table, so decode it in place instead of paying for
    # BitView.at() and an IntFlag construction on every dispatch.
    assert 0 <= opcode < 256
    packed = OPCODE_ATTRIBUTES.at(opcode)
    return (packed & int(attribute)) != 0


def _decode_fc_instruction(code: memoryview, offset: int) -> tuple[int, int]:
    """Decode and validate one supported 0xFC subopcode and its immediates."""

    subopcode, next_offset = decode_unsigned_full(code, offset)
    if FC_I32_TRUNC_SAT_F32_S <= subopcode <= FC_I64_TRUNC_SAT_F64_U:
        return subopcode, next_offset
    if subopcode == FC_MEMORY_COPY:
        destination_memory, next_offset = decode_unsigned_full(code, next_offset)
        source_memory, next_offset = decode_unsigned_full(code, next_offset)
        require_wasm(
            destination_memory == 0 and source_memory == 0, "only memory index zero is supported"
        )
        return subopcode, next_offset
    if subopcode == FC_MEMORY_FILL:
        memory_index, next_offset = decode_unsigned_full(code, next_offset)
        require_wasm(memory_index == 0, "only memory index zero is supported")
        return subopcode, next_offset
    reject_wasm(f"ERR_WASM_UNSUPPORTED_FEATURE: 0xFC subopcode {subopcode} is not supported")


def is_control_terminator(opcode: int) -> bool:
    """Return whether an opcode is a static control-boundary candidate."""

    assert 0 <= opcode < 256
    attributes = OPCODE_ATTRIBUTES.at(opcode)
    return (attributes & int(OpcodeAttribute.BASIC_BLOCK_BOUNDARY)) != 0 and (
        attributes & int(OpcodeAttribute.CALL)
    ) == 0


_MEMARG_OPCODES = _opcode_table(
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
    I64_LOAD,
    I64_LOAD8_S,
    I64_LOAD8_U,
    I64_LOAD16_S,
    I64_LOAD16_U,
    I64_LOAD32_S,
    I64_LOAD32_U,
    F32_LOAD,
    F64_LOAD,
    I64_STORE,
    I64_STORE8,
    I64_STORE16,
    I64_STORE32,
    F32_STORE,
    F64_STORE,
)

_MEMORY_INDEX_OPCODES = _opcode_table(
    MEMORY_SIZE,
    MEMORY_GROW,
)  # followed by a single reserved 0x00 byte

_NO_OPERAND = _opcode_table(
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
    I32_EXTEND8_S,
    I32_EXTEND16_S,
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
    I64_EXTEND8_S,
    I64_EXTEND16_S,
    I64_EXTEND32_S,
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

_LEB_UNSIGNED_OPERAND = _opcode_table(
    BR,
    BR_IF,
    CALL,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    GLOBAL_GET,
    GLOBAL_SET,
)

_BLOCK_OPENERS = _opcode_table(BLOCK, LOOP, IF)

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


@dataclass(slots=True)
class Instr:
    """Minimal one-instruction descriptor yielded by the streaming scanner."""

    offset: int
    opcode: int
    end_offset: int
    operand: int | None = None


@dataclass(slots=True)
class ControlMap:
    """Sparse control delimiters and statically resolved control edges."""

    blocks: ReadOnlyFlatMapStorage[int, ControlBlockMetadata]
    branch_targets: ReadOnlyFlatMapStorage[int, tuple[tuple[int, int, int], ...]]
    else_targets: ReadOnlyFlatMapStorage[int, int]
    block_cache: StaticVector[tuple[int, ControlBlock] | None]
    branch_cache: StaticVector[tuple[int, tuple[tuple[int, int, int], ...]] | None]

    @staticmethod
    def _cache_slot(ip: int) -> int:
        """Fold a 32-bit bytecode offset to the fixed 2-bit cache index."""

        assert 0 <= ip <= 0xFFFF_FFFF
        temp = ip ^ (ip >> 16)
        temp = temp ^ (temp >> 8)
        temp = temp ^ (temp >> 4)
        temp = temp ^ (temp >> 2)
        return temp & 0x03

    def block(self, ip: int) -> ControlBlock:
        """Return a block delimiter, using the fixed O(1) locality cache."""

        slot = self._cache_slot(ip)
        cached = self.block_cache[slot]
        if cached is not None and cached[0] == ip:
            return cached[1]
        value = self.blocks.view().find(ip)
        assert value is not None, f"unknown control block at {ip}"
        control = (
            value.match_end,
            None if value.else_offset == 0xFFFF_FFFF else value.else_offset,
            value.result_arity,
        )
        self.block_cache[slot] = (ip, control)
        return control

    def branch_edges(self, ip: int) -> tuple[tuple[int, int, int], ...]:
        """Return precomputed ``(target_ip, stack_base, result_words)`` edges."""

        slot = self._cache_slot(ip)
        cached = self.branch_cache[slot]
        if cached is not None and cached[0] == ip:
            return cached[1]
        value = self.branch_targets.view().find(ip)
        assert value is not None, f"unknown branch at {ip}"
        self.branch_cache[slot] = (ip, value)
        return value

    def else_target(self, ip: int) -> int:
        """Return the instruction after an ELSE's matching END."""

        target = self.else_targets.view().find(ip)
        assert target is not None, f"ELSE at {ip} has no matching block"
        return target


@dataclass(slots=True)
class _OpenBlock:
    opcode: int
    start: int
    body_start: int
    else_offset: int | None
    result_arity: int
    native_entry: ControlBlockMetadata


def build_control_map(
    code: memoryview,
    drop_widths: ReadOnlyFlatMapStorage[int, int] | None = None,
    select_widths: ReadOnlyFlatMapStorage[int, int] | None = None,
    allocator: BumpAllocator | None = None,
    branch_layouts: ReadOnlyFlatMapStorage[int, tuple[tuple[int, int], ...]] | None = None,
) -> ControlMap:
    """Build sparse control metadata directly from a loader-owned ROM view."""
    block_count = 0
    branch_count = 0
    for instruction in iter_scan_instrs(code):
        if _BLOCK_OPENERS.at(instruction.opcode):
            block_count += 1
        elif (
            instruction.opcode == BR
            or instruction.opcode == BR_IF
            or instruction.opcode == BR_TABLE
        ):
            branch_count += 1

    block_entries: StaticVector[tuple[int, ControlBlockMetadata]] = StaticVector(
        capacity=block_count
    )
    branch_opener_entries: StaticVector[tuple[int, tuple[int, ...]]] = StaticVector(
        capacity=branch_count
    )
    else_opener_entries: StaticVector[tuple[int, int]] = StaticVector(capacity=block_count)
    # [opcode, start_offset, else_offset] per still-open BLOCK/LOOP/IF, in a
    # fixed-size buffer indexed by `depth` (see FB_CONF_MAX_NESTING_DEPTH) --
    # else_offset is filled in place when this entry's own ELSE is reached,
    # read back when its own END pops it.
    open_stack: StaticVector[_OpenBlock | None] = StaticVector(capacity=FB_CONF_MAX_NESTING_DEPTH)
    for _ in range(FB_CONF_MAX_NESTING_DEPTH):
        open_stack.append(None)
    depth = 0

    off = 0
    n = len(code)
    while off < n:
        start = off
        opcode = code[off]
        off += 1
        if opcode == FC_PREFIX:
            _, off = _decode_fc_instruction(code, off)
        elif _BLOCK_OPENERS.at(opcode):
            result_arity, off = _decode_blocktype(code, off)
            assert depth < FB_CONF_MAX_NESTING_DEPTH, (
                "section-count prevalidation must cap control nesting"
            )
            entry = ControlBlockMetadata()
            entry.match_end = 0xFFFF_FFFF
            entry.else_offset = 0xFFFF_FFFF
            entry.result_arity = result_arity
            open_stack[depth] = _OpenBlock(opcode, start, off, None, result_arity, entry)
            depth += 1
        elif opcode == BR or opcode == BR_IF:
            branch_depth, off = decode_unsigned_full(code, off)
            if branch_depth < depth:
                opener = open_stack[depth - branch_depth - 1]
                assert opener is not None
                target_start = opener.start
            else:
                target_start = 0xFFFF_FFFF
            if not branch_opener_entries.push_back((start, (target_start,))):
                assert False, "branch count exceeds code capacity"
        elif _LEB_UNSIGNED_OPERAND.at(opcode):
            _, off = decode_unsigned_full(code, off)
        elif opcode == I32_CONST or opcode == I64_CONST:
            _, off = decode_signed_full(code, off, bits=64 if opcode == I64_CONST else 32)
        elif opcode == F32_CONST:
            require_wasm(off + 4 <= n, "truncated f32.const immediate")
            off += 4
        elif opcode == F64_CONST:
            require_wasm(off + 8 <= n, "truncated f64.const immediate")
            off += 8
        elif _MEMARG_OPCODES.at(opcode):
            _, off = decode_unsigned_full(code, off)
            _, off = decode_unsigned_full(code, off)
        elif _MEMORY_INDEX_OPCODES.at(opcode):
            off += 1  # reserved
        elif opcode == BR_TABLE:
            n_labels, off = decode_unsigned_full(code, off)
            require_wasm(
                n_labels <= 0xFFFF,
                "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
            )
            targets: StaticVector[int] = StaticVector(capacity=n_labels + 1)
            for _ in range(n_labels + 1):
                branch_depth, off = decode_unsigned_full(code, off)
                if branch_depth < depth:
                    opener = open_stack[depth - branch_depth - 1]
                    assert opener is not None
                    target_start = opener.start
                else:
                    target_start = 0xFFFF_FFFF
                pushed = targets.push_back(target_start)
                assert pushed
            if not branch_opener_entries.push_back((start, freeze_sequence(targets))):
                assert False, "branch count exceeds code capacity"
        elif opcode == CALL_INDIRECT:
            _, off = decode_unsigned_full(code, off)
            _, off = decode_unsigned_full(code, off)
        elif opcode == ELSE:
            opener = open_stack[depth - 1]
            assert opener is not None and opener.opcode == IF, "ELSE without matching IF"
            opener.else_offset = start
            opener.native_entry.else_offset = start
            pushed = else_opener_entries.push_back((start, opener.start))
            assert pushed
        elif opcode == END:
            if depth > 0:
                depth -= 1
                opener = open_stack[depth]
                assert opener is not None
                open_stack[depth] = None
                entry = opener.native_entry
                entry.match_end = start
                if not block_entries.push_back((opener.start, entry)):
                    assert False, "ERR_WASM_UNSUPPORTED_FEATURE: block count exceeds code capacity"
        elif _NO_OPERAND.at(opcode):
            pass
        else:
            reject_wasm(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} is not supported"
            )

    assert depth == 0, "unterminated block/loop/if (missing END)"
    block_storage = ReadOnlyFlatMapStorage.create(block_entries)
    branch_entries: StaticVector[tuple[int, tuple[tuple[int, int, int], ...]]] = StaticVector(
        capacity=branch_count
    )
    for source_ip, opener_starts in branch_opener_entries:
        layouts = None if branch_layouts is None else branch_layouts.view().find(source_ip)
        if layouts is None:
            assert all(target == 0xFFFF_FFFF for target in opener_starts), (
                "validated branches require loader stack analysis"
            )
        else:
            assert len(layouts) == len(opener_starts)
        edges: StaticVector[tuple[int, int, int]] = StaticVector(capacity=len(opener_starts))
        for target_number, opener_start in enumerate(opener_starts):
            stack_base, result_words = (0, 0) if layouts is None else layouts[target_number]
            if opener_start == 0xFFFF_FFFF:
                target_ip = 0xFFFF_FFFF
            else:
                opener = block_storage.view().find(opener_start)
                assert opener is not None
                target_ip = opener_start + 2 if code[opener_start] == LOOP else opener.match_end + 1
            edges.append((target_ip, stack_base, result_words))
        pushed = branch_entries.push_back((source_ip, freeze_sequence(edges)))
        assert pushed
    else_targets: StaticVector[tuple[int, int]] = StaticVector(capacity=len(else_opener_entries))
    for else_ip, opener_start in else_opener_entries:
        opener = block_storage.view().find(opener_start)
        assert opener is not None
        pushed = else_targets.push_back((else_ip, opener.match_end + 1))
        assert pushed
    return ControlMap(
        blocks=block_storage,
        branch_targets=ReadOnlyFlatMapStorage.create(branch_entries),
        else_targets=ReadOnlyFlatMapStorage.create(else_targets),
        block_cache=StaticVector.of((None, None, None, None), capacity=4),
        branch_cache=StaticVector.of((None, None, None, None), capacity=4),
    )


def iter_scan_instrs(code: memoryview, start: int = 0) -> Iterator[Instr]:
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
    offset gets it via
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
        if opcode == FC_PREFIX:
            operand, off = _decode_fc_instruction(code, off)
        elif _BLOCK_OPENERS.at(opcode):
            _, off = _decode_blocktype(code, off)
        elif _LEB_UNSIGNED_OPERAND.at(opcode):
            operand, off = decode_unsigned_full(code, off)
        elif opcode == I32_CONST or opcode == I64_CONST:
            _, off = decode_signed_full(
                code, off, bits=64 if opcode == I64_CONST else 32
            )  # value unused by block-boundary scanning
        elif opcode == F32_CONST:
            off += 4
        elif opcode == F64_CONST:
            off += 8
        elif _MEMARG_OPCODES.at(opcode):
            _, off = decode_unsigned_full(code, off)  # align, unused
            _, off = decode_unsigned_full(code, off)  # mem_offset, unused
        elif _MEMORY_INDEX_OPCODES.at(opcode):
            require_wasm(off < n, "truncated memory index")
            reserved = code[off]
            off += 1
            require_wasm(reserved == 0, "only memory index 0 is supported")
        elif opcode == BR_TABLE:
            n_labels, off = decode_unsigned_full(code, off)
            require_wasm(
                n_labels <= 0xFFFF,
                "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
            )
            for _ in range(n_labels):
                _, off = decode_unsigned_full(
                    code, off
                )  # label, unused (see wasm_opcodes.BR_TABLE handling)
            operand, off = decode_unsigned_full(code, off)  # default label
        elif opcode == CALL_INDIRECT:
            operand, off = decode_unsigned_full(code, off)  # typeidx
            _, off = decode_unsigned_full(code, off)  # tableidx (0x00 in the MVP encoding), unused
        elif _NO_OPERAND.at(opcode):
            pass
        else:
            reject_wasm(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} is not supported"
            )

        if _has_instruction_width_record(opcode):
            _instruction_width_record_size(off - start)
        yield Instr(offset=start, opcode=opcode, end_offset=off, operand=operand)


def _has_instruction_width_record(opcode: int) -> bool:
    """Return whether a variable-length immediate needs its encoded width."""

    return (
        (bool(_LEB_UNSIGNED_OPERAND.at(opcode)) and opcode != BR and opcode != BR_TABLE)
        or opcode == I32_CONST
        or opcode == I64_CONST
        or opcode == CALL_INDIRECT
        or opcode == BR_IF
        or opcode == FC_PREFIX
        or bool(_MEMARG_OPCODES.at(opcode))
    )


def _instruction_width_record_size(instruction_width: int) -> int:
    assert instruction_width > 0
    require_wasm(
        instruction_width <= 0xFF,
        "ERR_WASM_UNSUPPORTED_FEATURE: variable instruction width exceeds u8",
    )
    return 1


# Operand bytes of the instructions whose annotation width depends only on the opcode.
_LOCAL_ANNOTATION_BYTES = {
    LOCAL_GET: 3 + 2,  # frame word offset, local width, and the RPN ext (head or terminal)
    LOCAL_SET: 3 + 2,
    LOCAL_TEE: 3,  # frame word offset and local width
}
_FIXED_OPERAND_ANNOTATION_BYTES = {
    GLOBAL_GET: 2,  # global index
    GLOBAL_SET: 2,
    I32_CONST: 4,  # signed value
    I64_CONST: 8,  # signed value
    BR: 4,  # target block, stack base, and result width
    BR_IF: 4,  # target block, stack base, and result width
    IF: 4,
    LOOP: 2,
    ELSE: 2,
    END: 2,
    CALL: 4,  # function/type index and continuation block index
    CALL_INDIRECT: 4,  # function/type index and continuation block index
    DROP: 1,  # operand width, derived by stack type analysis
    SELECT: 1,  # operand width, derived by stack type analysis
}


def _operand_annotation_bytes(code: memoryview, instruction: Instr) -> int:
    """Bytes that the operand annotation of one instruction occupies in the pool."""
    opcode = instruction.opcode
    if opcode in _LOCAL_ANNOTATION_BYTES:
        return _LOCAL_ANNOTATION_BYTES[opcode]
    if opcode in _FIXED_OPERAND_ANNOTATION_BYTES:
        return _FIXED_OPERAND_ANNOTATION_BYTES[opcode]
    if _MEMARG_OPCODES.at(opcode):
        return 4  # effective offset
    if opcode == BR_TABLE:
        label_count, _ = decode_unsigned_full(code, instruction.offset + 1)
        require_wasm(
            label_count <= 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
        )
        return BR_TABLE_COUNT_BYTES + (label_count + 1) * BR_TABLE_TARGET_BYTES
    if opcode == FC_PREFIX:
        assert instruction.operand is not None
        # The loader rejects every other 0xFC subopcode, so the subopcode byte is always present.
        assert (
            FC_I32_TRUNC_SAT_F32_S <= instruction.operand <= FC_I64_TRUNC_SAT_F64_U
            or instruction.operand == FC_MEMORY_COPY
            or instruction.operand == FC_MEMORY_FILL
        )
        return 1  # subopcode
    return 0


def _count_interpreter_annotation_pool_bytes(code: memoryview) -> int:
    annotation_bytes = 0
    for instruction in iter_scan_instrs(code):
        annotation_bytes += _operand_annotation_bytes(code, instruction)
        if _has_instruction_width_record(instruction.opcode):
            annotation_bytes += _instruction_width_record_size(
                instruction.end_offset - instruction.offset
            )
    return annotation_bytes


# RPN expression annotations (REQ-097 D). An expression starts at a one-word local.get and covers
# one-word local.get, i32.const, the i32 binary operators (including the trapping div and rem), the
# i32 unary operators (eqz, clz, ctz, popcnt). It ends in one of two ways, never crossing a block head.
#   END (local.set): a one-word local.set that leaves one value writes the local.
#   END_STACK: the first instruction it does not cover ends it; the temporaries become the VM stack.
# An expression covers at least two instructions.
_RPN_BINOP_KIND = {
    0x6A: 0,
    0x6B: 1,
    0x6C: 2,
    0x6D: 3,
    0x6E: 4,
    0x6F: 5,
    0x70: 6,
    0x71: 7,
    0x72: 8,
    0x73: 9,
    0x74: 10,
    0x75: 11,
    0x76: 12,
    0x77: 13,
    0x78: 14,
    0x46: 15,
    0x47: 16,
    0x48: 17,
    0x49: 18,
    0x4A: 19,
    0x4B: 20,
    0x4C: 21,
    0x4D: 22,
    0x4E: 23,
    0x4F: 24,
}
# div_s, div_u, rem_s, rem_u: the element carries the instruction distance for the trap position.
_RPN_CHECKED_BINOP_KINDS = frozenset({3, 4, 5, 6})
_RPN_UNARY_KIND = {0x45: 0, 0x67: 1, 0x68: 2, 0x69: 3}
_RPN_MIN_INSTRUCTIONS = 2
_RPN_END_LOCAL_BYTES = 8  # [END][target u16][continuation i32][code_length u8]
_RPN_END_STACK_BYTES = 6  # [END_STACK][continuation i32][code_length u8]
_RPN_TAIL_BYTES = 5  # [continuation i32][code_length u8], after a specialised local write


RPN_TERMINAL_END = 0  # END element: writes the local (target u16), then the program tail
RPN_TERMINAL_TAIL = 1  # the last specialised element writes the local; the program tail follows
RPN_TERMINAL_STACK = 2  # END_STACK: the temporaries become the VM stack top
RPN_TERMINAL_STACK_TAIL = (
    3  # the last specialised element pushes to the VM stack; the program tail follows
)

_KIND_TEMP = 0  # an operand that is a temporary on the VM stack above the pointer
_KIND_LOCAL = 1  # a local that has not been read yet (value: frame word offset)
_KIND_IMM = 2  # an immediate that has not been pushed yet (value: i32 bits)
_KIND_LETTER = {_KIND_TEMP: "T", _KIND_LOCAL: "L", _KIND_IMM: "I"}


@dataclass(frozen=True, slots=True)
class RpnExpression:
    """One loader-decided expression. Offsets are function-relative code offsets.

    terminal is RPN_TERMINAL_END (the END element writes target and resumes after set_offset),
    RPN_TERMINAL_TAIL (the last specialised element writes target; the program tail follows), or
    RPN_TERMINAL_STACK (END_STACK resumes at resume_offset; set_offset is then -1).
    """

    head_offset: int
    code_length: int
    max_depth: int
    elements: bytes  # operator and push elements; the terminal is appended by the pool builder
    terminal: int
    set_offset: int
    target: int
    resume_offset: int

    @property
    def writes_local(self) -> bool:
        return self.terminal in (RPN_TERMINAL_END, RPN_TERMINAL_TAIL)

    @property
    def end_bytes(self) -> int:
        if self.terminal in (RPN_TERMINAL_TAIL, RPN_TERMINAL_STACK_TAIL):
            return _RPN_TAIL_BYTES
        return _RPN_END_LOCAL_BYTES if self.terminal == RPN_TERMINAL_END else _RPN_END_STACK_BYTES


_RPN_SPEC_CODES = {
    form: RPN_ELEM_SPEC_BASE + index for index, form in enumerate(RPN_SPECIALISED_FORMS)
}
_RPN_SPEC_VM_CODES = {
    (kind, left, right): code
    for (kind, left, right, dest), code in _RPN_SPEC_CODES.items()
    if dest == "V"
}
_RPN_SPEC_DEST_L_CODES = {
    (kind, left, right): code
    for (kind, left, right, dest), code in _RPN_SPEC_CODES.items()
    if dest == "L"
}


def _rpn_operand_bytes(entry: tuple[int, int]) -> bytes:
    kind, value = entry
    if kind == _KIND_LOCAL:
        return value.to_bytes(2, "little")
    if kind == _KIND_IMM:
        return (value & 0xFFFF_FFFF).to_bytes(4, "little")
    return b""


class _RpnScan:
    """The state of one expression scan.

    model holds the operands in order, each a temporary, a local or an immediate that is not read
    yet. A specialised element reads its local and immediate operands directly, and its temporary
    operands from the top of the VM stack. Any other operator pushes its pending operands first.
    last_op records the last operator element, so that a local.set can rewrite it to write the
    local directly. checkpoint is the last point where no operand is pending.
    """

    def __init__(
        self, code: memoryview, local_layout: LocalLayout, head_offset: int, depth_limit: int
    ) -> None:
        self.code = code
        self.local_layout = local_layout
        self.head_offset = head_offset
        self.depth_limit = depth_limit
        self.model: list[tuple[int, int]] = []
        self.elements = bytearray()
        self.covered = 0
        self.max_depth = 0
        self.last_op: tuple[int, int, int, tuple[int, int], tuple[int, int]] | None = None
        self.checkpoint: tuple[int, int, int, int] | None = None
        self.depth_exceeded = False

    def materialise(self, index: int) -> None:
        kind, value = self.model[index]
        if kind == _KIND_TEMP:
            return
        self.elements.append(RPN_ELEM_PUSH_LOCAL if kind == _KIND_LOCAL else RPN_ELEM_PUSH_IMM)
        self.elements.extend(_rpn_operand_bytes((kind, value)))
        self.model[index] = (_KIND_TEMP, 0)

    def step(self, instruction: Instr) -> bool:
        """Scan one covered instruction. False stops the expression before this instruction."""
        opcode = instruction.opcode
        if opcode == LOCAL_GET:
            if self.local_layout.size_words(instruction.operand) != 1:
                return False
            self.model.append((_KIND_LOCAL, self.local_layout.offset(instruction.operand)))
        elif opcode == I32_CONST:
            value, _ = decode_signed_full(self.code, instruction.offset + 1, bits=32)
            self.model.append((_KIND_IMM, value))
        elif opcode in _RPN_BINOP_KIND:
            if not self._binop(_RPN_BINOP_KIND[opcode], instruction.offset):
                return False
        elif opcode in _RPN_UNARY_KIND:
            if not self.model:
                return False
            self._unary(_RPN_UNARY_KIND[opcode])
        else:
            return False
        self.covered += 1
        self.max_depth = max(self.max_depth, len(self.model))
        if len(self.model) > self.depth_limit:
            self.depth_exceeded = True
            return False
        return True

    def _binop(self, kind: int, offset: int) -> bool:
        if len(self.model) < 2:
            return False
        # Pending operands below this operator are pushed first, so that no pending operand lies
        # below a temporary.
        for index in range(len(self.model) - 2):
            self.materialise(index)
        lhs, rhs = self.model[-2], self.model[-1]
        op_start = len(self.elements)
        spec_code = _RPN_SPEC_CODES.get((kind, _KIND_LETTER[lhs[0]], _KIND_LETTER[rhs[0]], "T"))
        if spec_code is not None and kind not in _RPN_CHECKED_BINOP_KINDS:
            self.elements.append(spec_code)
            self.elements.extend(_rpn_operand_bytes(lhs) + _rpn_operand_bytes(rhs))
        else:
            if lhs[0] != _KIND_TEMP and rhs[0] == _KIND_TEMP:
                return False  # a pending local below a temporary cannot be pushed in order
            self.materialise(len(self.model) - 2)
            self.materialise(len(self.model) - 1)
            self.elements.append(RPN_ELEM_BINOP_BASE + kind)
            if kind in _RPN_CHECKED_BINOP_KINDS:
                self.elements.append(offset - self.head_offset)
        self.last_op = (op_start, len(self.elements), kind, lhs, rhs)
        del self.model[-2:]
        self.model.append((_KIND_TEMP, 0))
        return True

    def _unary(self, kind: int) -> None:
        for index in range(len(self.model)):
            self.materialise(index)
        self.elements.append(RPN_ELEM_UNARY_BASE + kind)
        self.model[-1] = (_KIND_TEMP, 0)
        self.last_op = None

    def checkpoint_here(self, position: int) -> None:
        if all(kind == _KIND_TEMP for kind, _ in self.model):
            self.checkpoint = (position, len(self.elements), self.covered, self.max_depth)

    def local_write_ends_here(self, instruction: Instr) -> bool:
        return (
            len(self.model) == 1
            and self.model[0][0] == _KIND_TEMP
            and self.covered >= 1
            and self.local_layout.size_words(instruction.operand) == 1
        )

    def rewrite_last_op_to_local(self, target: int) -> bool:
        """Rewrite the last operator as one that writes the local, when that form exists."""
        if self.last_op is None or self.last_op[1] != len(self.elements):
            return False
        op_start, _op_end, kind, lhs, rhs = self.last_op
        dest_code = _RPN_SPEC_DEST_L_CODES.get((kind, _KIND_LETTER[lhs[0]], _KIND_LETTER[rhs[0]]))
        if dest_code is None:
            return False
        del self.elements[op_start:]
        self.elements.append(dest_code)
        self.elements.extend(_rpn_operand_bytes(lhs) + _rpn_operand_bytes(rhs))
        self.elements.extend(target.to_bytes(2, "little"))
        return True

    def rewrite_last_op_to_vm(self, length: int) -> bool:
        """Rewrite the operator that ends the expression as one that pushes to the VM stack."""
        if self.last_op is None or self.last_op[1] != length:
            return False
        op_start, _op_end, kind, lhs, rhs = self.last_op
        vm_code = _RPN_SPEC_VM_CODES.get((kind, _KIND_LETTER[lhs[0]], _KIND_LETTER[rhs[0]]))
        if vm_code is None:
            return False
        del self.elements[op_start:]
        self.elements.append(vm_code)
        self.elements.extend(_rpn_operand_bytes(lhs) + _rpn_operand_bytes(rhs))
        return True

    def finish(self, position: int) -> tuple[int, int, int, int] | None:
        """The expression that ends before the instruction at position, or None."""
        if not self.depth_exceeded and self.covered > 0:
            # Pending operands are pushed, so the temporaries become the VM stack top (END_STACK).
            for index in range(len(self.model)):
                self.materialise(index)
            self.checkpoint = (position, len(self.elements), self.covered, self.max_depth)
        return self.checkpoint


_RPN_OPERAND_PAYLOAD_BYTES = {"L": 2, "I": 4, "T": 0}


def _rpn_record_count(elements: bytes) -> int:
    """Count the element records of an element byte string. Each record is one runtime dispatch."""
    count = 0
    position = 0
    while position < len(elements):
        code = elements[position]
        count += 1
        if code == RPN_ELEM_PUSH_LOCAL:
            position += 1 + _RPN_OPERAND_PAYLOAD_BYTES["L"]
        elif code == RPN_ELEM_PUSH_IMM:
            position += 1 + _RPN_OPERAND_PAYLOAD_BYTES["I"]
        elif code < RPN_ELEM_UNARY_BASE:
            # A checked binary operator carries one byte of offset after its code.
            checked = code - RPN_ELEM_BINOP_BASE in _RPN_CHECKED_BINOP_KINDS
            position += 2 if checked else 1
        elif code < RPN_ELEM_SPEC_BASE:
            position += 1
        else:
            _kind, left, right, dest = RPN_SPECIALISED_FORMS[code - RPN_ELEM_SPEC_BASE]
            target_bytes = 2 if dest == "L" else 0
            position += (
                1
                + _RPN_OPERAND_PAYLOAD_BYTES[left]
                + _RPN_OPERAND_PAYLOAD_BYTES[right]
                + target_bytes
            )
    assert position == len(elements), "element records must tile the element bytes"
    return count


def _rpn_dispatch_count(elements: bytes, has_end_record: bool) -> int:
    """The runtime dispatches of an expression: the head, its element records, and its END record.

    has_end_record is true when the terminal is an END or END_STACK element. A specialised tail
    write has no terminal record of its own.
    """
    return 1 + _rpn_record_count(elements) + (1 if has_end_record else 0)


def _rpn_set_expression(
    scan: _RpnScan, head: Instr, instruction: Instr, local_layout: LocalLayout
) -> RpnExpression | None:
    """The expression that ends at the one-word local.set (END or TAIL), or None when refused.

    The local.set is covered too. The expression is kept only when its dispatches are fewer than the
    covered instructions.
    """
    code_length = instruction.end_offset - head.offset
    if code_length > 0xFF or scan.covered + 1 < _RPN_MIN_INSTRUCTIONS:
        return None
    target = local_layout.offset(instruction.operand)
    tail = scan.rewrite_last_op_to_local(target)
    terminal = RPN_TERMINAL_TAIL if tail else RPN_TERMINAL_END
    dispatches = _rpn_dispatch_count(bytes(scan.elements), terminal == RPN_TERMINAL_END)
    if dispatches >= scan.covered + 1:
        return None
    return RpnExpression(
        head_offset=head.offset,
        code_length=code_length,
        max_depth=scan.max_depth,
        elements=bytes(scan.elements),
        terminal=terminal,
        set_offset=instruction.offset,
        target=target,
        resume_offset=instruction.offset,
    )


def _scan_rpn_expression(
    code: memoryview,
    instructions: list[Instr],
    start: int,
    heads: set[int],
    local_layout: LocalLayout,
    depth_limit: int,
) -> tuple[RpnExpression, int] | None:
    """Scan one expression starting at the local.get at instructions[start].

    The expression ends at a one-word local.set of its only temporary (END or TAIL), or before the
    first instruction it does not cover (END_STACK, at the last checkpoint).
    """
    head = instructions[start]
    scan = _RpnScan(code, local_layout, head.offset, depth_limit)
    position = start
    while position < len(instructions):
        instruction = instructions[position]
        if position > start and instruction.offset in heads:
            break
        if instruction.opcode == LOCAL_SET:
            if not scan.local_write_ends_here(instruction):
                break
            expression = _rpn_set_expression(scan, head, instruction, local_layout)
            return None if expression is None else (expression, position)
        if not scan.step(instruction):
            break
        position += 1
        scan.checkpoint_here(position)
    checkpoint = scan.finish(position)
    if checkpoint is None:
        return None
    next_position, length, covered_count, depth = checkpoint
    if covered_count < _RPN_MIN_INSTRUCTIONS or next_position >= len(instructions):
        return None
    resume = instructions[next_position]
    code_length = resume.offset - head.offset
    if code_length > 0xFF:
        return None
    elements = bytes(scan.elements[:length])
    terminal = RPN_TERMINAL_STACK
    if scan.rewrite_last_op_to_vm(length):
        elements = bytes(scan.elements)
        terminal = RPN_TERMINAL_STACK_TAIL
    if _rpn_dispatch_count(elements, terminal == RPN_TERMINAL_STACK) >= covered_count:
        return None
    expression = RpnExpression(
        head_offset=head.offset,
        code_length=code_length,
        max_depth=depth,
        elements=elements,
        terminal=terminal,
        set_offset=-1,
        target=0,
        resume_offset=resume.offset,
    )
    return expression, next_position - 1


def find_rpn_expressions(
    code: memoryview,
    block_heads: list[int],
    local_layout: LocalLayout,
    stack_capacity: int,
) -> tuple[RpnExpression, ...]:
    """Find the RPN expressions of one function, in code order, never crossing a block head.

    An expression's temporaries sit above the VM stack pointer, so its depth may not exceed the
    function's operand stack capacity (RPN_MAX_DEPTH bounds it as well).
    """

    heads = set(block_heads)
    depth_limit = min(RPN_MAX_DEPTH, stack_capacity)
    instructions = list(iter_scan_instrs(code))
    found: list[RpnExpression] = []
    index = 0
    while index < len(instructions):
        if instructions[index].opcode != LOCAL_GET:
            index += 1
            continue
        scanned = _scan_rpn_expression(code, instructions, index, heads, local_layout, depth_limit)
        if scanned is None:
            index += 1
            continue
        expression, last = scanned
        found.append(expression)
        index = last + 1
    return tuple(found)


def fit_rpn_expressions(
    code: memoryview,
    block_heads: list[int],
    local_layout: LocalLayout,
    expressions: tuple[RpnExpression, ...],
) -> tuple[RpnExpression, ...]:
    """Keep the leading expressions whose program region stays addressable by a u16 position."""

    block_bytes = 0
    for block_number, start in enumerate(block_heads):
        end = block_heads[block_number + 1] if block_number + 1 < len(block_heads) else len(code)
        block_bytes += _count_interpreter_annotation_pool_bytes(code[start:end])
    kept: list[RpnExpression] = []
    cursor = block_bytes
    for expression in expressions:
        size = len(expression.elements) + expression.end_bytes
        if cursor + size > 0xFFFF:
            break
        kept.append(expression)
        cursor += size
    return tuple(kept)


def rpn_native_code(
    code: memoryview,
    expressions: tuple[RpnExpression, ...],
    local_layout: LocalLayout,
) -> bytearray | None:
    """Native-only code copy: one-word local accesses get their own opcodes, and expression heads
    carry RPN_EXPR_HEAD. Returns None when the copy would equal the original code.

    The width of a local access is fixed by the loader, so the one-word handlers need no width test.
    Only opcode bytes change, so instruction boundaries and annotation offsets are unchanged.
    """
    copy: bytearray | None = None
    for instruction in iter_scan_instrs(code):
        if instruction.opcode != LOCAL_GET and instruction.opcode != LOCAL_SET:
            continue
        if local_layout.size_words(instruction.operand) != 1:
            continue
        if copy is None:
            copy = bytearray(code)
        copy[instruction.offset] = (
            NATIVE_LOCAL_GET_W1 if instruction.opcode == LOCAL_GET else NATIVE_LOCAL_SET_W1
        )
    for expression in expressions:
        if copy is None:
            copy = bytearray(code)
        copy[expression.head_offset] = RPN_EXPR_HEAD
    return copy


def _rpn_local_set_offsets(rpn_expressions: tuple[RpnExpression, ...]) -> set[int]:
    """The local.set offsets that terminate an END expression."""
    return {expression.set_offset for expression in rpn_expressions if expression.writes_local}


def _append_little_endian(annotations: InterpreterAnnotationPool, value: int, width: int) -> None:
    for shift in range(0, 8 * width, 8):
        annotations.append_byte((value >> shift) & 0xFF)


def _append_rpn_program(
    annotations: InterpreterAnnotationPool,
    expression: RpnExpression,
    set_ends: dict[int, int],
    instruction_starts: dict[int, int],
) -> None:
    """Append one expression program: its elements, then its terminal and code length.

    END resumes after the terminal local.set annotation. END_STACK resumes at the annotation of the
    instruction that ends the expression.
    """
    for element_byte in expression.elements:
        annotations.append_byte(element_byte)
    end_position = len(annotations)
    if expression.terminal == RPN_TERMINAL_END:
        annotations.append_byte(RPN_ELEM_END)
        _append_little_endian(annotations, expression.target, 2)
    elif expression.terminal == RPN_TERMINAL_STACK:
        annotations.append_byte(RPN_ELEM_END_STACK)
    if expression.writes_local:
        resume_position = set_ends[expression.set_offset]
    else:
        resume_position = instruction_starts[expression.resume_offset]
    _append_little_endian(annotations, (resume_position - end_position) & 0xFFFF_FFFF, 4)
    annotations.append_byte(expression.code_length)


def build_interpreter_annotation_pool(
    code: memoryview,
    block_heads: SequenceView[int],
    allocator: BumpAllocator | None = None,
    control_map: ControlMap | None = None,
    block_index_base: int = 0,
    *,
    local_layout: LocalLayout,
    drop_widths: ReadOnlyFlatMapStorage[int, int] | None = None,
    select_widths: ReadOnlyFlatMapStorage[int, int] | None = None,
    rpn_expressions: tuple[RpnExpression, ...] = (),
) -> tuple[InterpreterAnnotationPool, StaticVector[annotation_offset_t]]:
    """Store runtime-derived operands and control edges in the annotation pool.

    Fixed-width values and raw LEB128 values are not retained. Variable-length
    immediates store one encoded-width byte; fixed-width instruction size comes
    from its opcode. Branch annotations store the resolved BasicBlock index so
    dispatch never decodes a branch depth or searches by PC.
    """

    assert len(block_heads) > 0
    total_bytes = 0
    for block_index, start in enumerate(block_heads):
        end = block_heads[block_index + 1] if block_index + 1 < len(block_heads) else len(code)
        assert 0 <= start <= end <= len(code)
        total_bytes += _count_interpreter_annotation_pool_bytes(code[start:end])

    program_positions: dict[int, int] = {}
    set_offsets = _rpn_local_set_offsets(rpn_expressions)
    cursor = total_bytes
    for expression in rpn_expressions:
        program_positions[expression.head_offset] = cursor
        cursor += len(expression.elements) + expression.end_bytes
    assert not rpn_expressions or cursor <= 0xFFFF, (
        "fit_rpn_expressions must bound the program region"
    )
    set_ends: dict[int, int] = {}
    block_bytes = total_bytes
    total_bytes = cursor

    annotations = InterpreterAnnotationPool(capacity=total_bytes, allocator=allocator)
    block_offsets: StaticVector[annotation_offset_t] = StaticVector(capacity=len(block_heads))

    def rpn_ext_for(function_offset: int) -> int:
        # The expression head stores the distance from its own annotation start to the program, so the
        # native head needs no pool base. Other local.get store 0.
        if function_offset not in program_positions:
            return 0
        annotation_start = len(annotations) - 4  # width, offset (u16), slot width precede the ext
        return program_positions[function_offset] - annotation_start

    def append_u32(value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF
        annotations.append(value)

    def append_u16(value: int) -> None:
        assert 0 <= value <= 0xFFFF
        annotations.append_byte(value & 0xFF)
        annotations.append_byte(value >> 8)

    def append_wide(value: int) -> None:
        bits = value & 0xFFFF_FFFF_FFFF_FFFF
        append_u32(bits & 0xFFFF_FFFF)
        append_u32(bits >> 32)

    def append_instruction_width(instruction_width: instruction_width_t) -> None:
        require_wasm(
            0 < instruction_width <= 0xFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: variable instruction width exceeds u8",
        )
        annotations.append_byte(instruction_width)

    def block_index_for_offset(offset: int) -> block_index_t:
        if control_map is None or offset >= len(code):
            return block_index_t(0xFFFF)
        from bisect import bisect_left

        position = bisect_left(block_heads, offset)
        assert position < len(block_heads) and block_heads[position] == offset
        block_index = block_index_base + position
        require_wasm(
            block_index < 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: block index exceeds u16",
        )
        return block_index_t(block_index)

    def continuation_block_index_for_offset(offset: int) -> block_index_t:
        if offset >= len(code):
            return block_index_t(0xFFFF)
        from bisect import bisect_left

        position = bisect_left(block_heads, offset)
        if position < len(block_heads) and block_heads[position] == offset:
            block_number = position
        else:
            assert position > 0
            block_number = position - 1
        block_index = block_index_base + block_number
        require_wasm(
            block_index < 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: block index exceeds u16",
        )
        return block_index_t(block_index)

    def append_branch_target(function_offset: int, target_number: int) -> None:
        if control_map is None:
            target_block_index = 0xFFFF
            stack_base_words, result_words = 0, 0
        else:
            edges = control_map.branch_edges(function_offset)
            assert target_number < len(edges)
            target_offset, stack_base_words, result_words = edges[target_number]
            target_block_index = block_index_for_offset(target_offset)
        assert stack_base_words <= 0xFF and result_words <= 2
        append_u16(target_block_index)
        annotations.append_byte(stack_base_words)
        annotations.append_byte(result_words)

    def write_local_slot(instruction: Instr, block_code: memoryview) -> None:
        local_index, _ = decode_unsigned_full(block_code, instruction.offset + 1)
        assert local_index < len(local_layout)
        frame_offset: frame_word_offset_t = local_layout.offset(local_index)
        local_width = local_layout.size_words(local_index)
        assert frame_offset <= 0xFFFF and local_width <= 0xFF
        append_u16(frame_offset)
        annotations.append_byte(local_width)

    def emit_local_get(instruction: Instr, block_code: memoryview, start: int) -> None:
        write_local_slot(instruction, block_code)
        append_u16(rpn_ext_for(start + instruction.offset))

    def emit_local_set(instruction: Instr, block_code: memoryview, start: int) -> None:
        write_local_slot(instruction, block_code)
        function_offset = start + instruction.offset
        append_u16(0xFFFF)
        if function_offset in set_offsets:
            set_ends[function_offset] = len(annotations)

    def emit_local_tee(instruction: Instr, block_code: memoryview, start: int) -> None:
        write_local_slot(instruction, block_code)

    def emit_drop(instruction: Instr, block_code: memoryview, start: int) -> None:
        function_offset = start + instruction.offset
        width = 1 if drop_widths is None else drop_widths.view().find(function_offset) or 1
        assert width == 1 or width == 2
        annotations.append_byte(width)

    def emit_select(instruction: Instr, block_code: memoryview, start: int) -> None:
        function_offset = start + instruction.offset
        width = 1 if select_widths is None else select_widths.view().find(function_offset) or 1
        assert width == 1 or width == 2
        annotations.append_byte(width)

    def emit_global(instruction: Instr, block_code: memoryview, start: int) -> None:
        raw_global_index, _ = decode_unsigned_full(block_code, instruction.offset + 1)
        global_index = global_index_t(raw_global_index)
        require_wasm(
            global_index <= 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: global index exceeds u16",
        )
        append_u16(global_index)

    def emit_i32_const(instruction: Instr, block_code: memoryview, start: int) -> None:
        value, _ = decode_signed_full(block_code, instruction.offset + 1, bits=32)
        append_u32(value & 0xFFFF_FFFF)

    def emit_i64_const(instruction: Instr, block_code: memoryview, start: int) -> None:
        value, _ = decode_signed_full(block_code, instruction.offset + 1, bits=64)
        append_wide(value)

    def emit_memarg(instruction: Instr, block_code: memoryview, start: int) -> None:
        _alignment, next_cursor = decode_unsigned_full(block_code, instruction.offset + 1)
        offset, _ = decode_unsigned_full(block_code, next_cursor)
        append_u32(offset)

    def emit_branch(instruction: Instr, block_code: memoryview, start: int) -> None:
        append_branch_target(start + instruction.offset, 0)

    def emit_br_table(instruction: Instr, block_code: memoryview, start: int) -> None:
        function_offset = start + instruction.offset
        label_count, target_cursor = decode_unsigned_full(block_code, instruction.offset + 1)
        require_wasm(
            label_count <= 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
        )
        append_u16(label_count)
        for target_number in range(label_count + 1):
            _, target_cursor = decode_unsigned_full(block_code, target_cursor)
            append_branch_target(function_offset, target_number)

    def emit_call(instruction: Instr, block_code: memoryview, start: int) -> None:
        raw_function_index, _ = decode_unsigned_full(block_code, instruction.offset + 1)
        function_index = function_index_t(raw_function_index)
        require_wasm(
            function_index <= 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: function index exceeds u16",
        )
        append_u16(function_index)
        append_u16(block_index_for_offset(start + instruction.end_offset))

    def emit_call_indirect(instruction: Instr, block_code: memoryview, start: int) -> None:
        raw_type_index, next_cursor = decode_unsigned_full(block_code, instruction.offset + 1)
        type_index = type_index_t(raw_type_index)
        require_wasm(
            type_index <= 0xFFFF,
            "ERR_WASM_UNSUPPORTED_FEATURE: type index exceeds u16",
        )
        append_u16(type_index)
        _table_index, _ = decode_unsigned_full(block_code, next_cursor)
        append_u16(block_index_for_offset(start + instruction.end_offset))

    def emit_fc_prefix(instruction: Instr, block_code: memoryview, start: int) -> None:
        subopcode, cursor = decode_unsigned_full(block_code, instruction.offset + 1)
        assert subopcode <= 0xFF
        annotations.append_byte(subopcode)
        if subopcode == FC_MEMORY_COPY:
            _destination_memory, cursor = decode_unsigned_full(block_code, cursor)
            _source_memory, _ = decode_unsigned_full(block_code, cursor)
        elif subopcode == FC_MEMORY_FILL:
            _memory_index, _ = decode_unsigned_full(block_code, cursor)

    def emit_loop(instruction: Instr, block_code: memoryview, start: int) -> None:
        append_u16(block_index_for_offset(start + instruction.end_offset))

    def emit_if(instruction: Instr, block_code: memoryview, start: int) -> None:
        function_offset = start + instruction.offset
        then_target = start + instruction.end_offset
        match = None if control_map is None else control_map.block(function_offset)
        if match is None:
            else_target = len(code)
        else:
            match_end, else_offset, _result_arity = match
            else_target = else_offset + 1 if else_offset is not None else match_end + 1
        append_u16(block_index_for_offset(then_target))
        append_u16(block_index_for_offset(else_target))

    def emit_else(instruction: Instr, block_code: memoryview, start: int) -> None:
        function_offset = start + instruction.offset
        if control_map is None:
            target = len(code)
        else:
            target = control_map.else_target(function_offset)
        append_u16(block_index_for_offset(target))

    def emit_end(instruction: Instr, block_code: memoryview, start: int) -> None:
        append_u16(continuation_block_index_for_offset(start + instruction.end_offset))

    # One procedure per opcode. The loop judges each instruction's opcode once, then the procedure
    # writes that instruction's operands and branch targets in the original order.
    handlers: dict[int, Callable[[Instr, memoryview, int], None]] = {
        LOCAL_GET: emit_local_get,
        LOCAL_SET: emit_local_set,
        LOCAL_TEE: emit_local_tee,
        DROP: emit_drop,
        SELECT: emit_select,
        GLOBAL_GET: emit_global,
        GLOBAL_SET: emit_global,
        I32_CONST: emit_i32_const,
        I64_CONST: emit_i64_const,
        BR: emit_branch,
        BR_IF: emit_branch,
        BR_TABLE: emit_br_table,
        CALL: emit_call,
        CALL_INDIRECT: emit_call_indirect,
        FC_PREFIX: emit_fc_prefix,
        LOOP: emit_loop,
        IF: emit_if,
        ELSE: emit_else,
        END: emit_end,
    }
    for memarg_opcode in range(0x100):
        if _MEMARG_OPCODES.at(memarg_opcode):
            handlers[memarg_opcode] = emit_memarg

    # Annotation offset of each instruction, keyed by function offset: an END_STACK resumes there.
    instruction_starts: dict[int, int] = {}
    for block_number, start in enumerate(block_heads):
        end = block_heads[block_number + 1] if block_number + 1 < len(block_heads) else len(code)
        appended = block_offsets.push_back(annotation_offset_t(len(annotations)))
        assert appended
        block_code = code[start:end]
        for instruction in iter_scan_instrs(block_code):
            instruction_starts[start + instruction.offset] = len(annotations)
            if _has_instruction_width_record(instruction.opcode):
                append_instruction_width(
                    instruction_width_t(instruction.end_offset - instruction.offset)
                )
            handler = handlers.get(instruction.opcode)
            if handler is not None:
                handler(instruction, block_code, start)
    assert len(annotations) == block_bytes
    for expression in rpn_expressions:
        _append_rpn_program(annotations, expression, set_ends, instruction_starts)
    return annotations, block_offsets


def iter_block_ops(
    code: memoryview, head_offset: int, byte_span: int
) -> Iterator[tuple[int, WasmOperand]]:
    """
    Streams ONE BasicBlock's compilable `(opcode, arg)` op stream directly
    from raw bytecode, scoped to exactly `[head_offset, head_offset+byte_span)`,
    one instruction at a time -- never materializes the whole block's op
    list. Called on demand, at the moment a block is actually compiled or
    interpreted (see `wasm_module.BasicBlock`). A block's own
    byte_span, by construction (see `extract_basic_blocks`), spans the full
    straight-line operation body, including operations lowered through JIT
    Runtime APIs. Every instruction decoded in range belongs in the result.
    """
    off = head_offset
    end = head_offset + byte_span
    while off < end:
        start = off
        opcode = code[off]
        off += 1
        if opcode == FC_PREFIX:
            arg, off = _decode_fc_instruction(code, off)
        elif _BLOCK_OPENERS.at(opcode):
            _, off = _decode_blocktype(code, off)
            arg = None
        elif _LEB_UNSIGNED_OPERAND.at(opcode):
            operand, off = decode_unsigned_full(code, off)
            arg: WasmOperand = operand
        elif opcode == I32_CONST or opcode == I64_CONST:
            arg, off = decode_signed_full(code, off, bits=64 if opcode == I64_CONST else 32)
        elif opcode == F32_CONST:
            assert off + 4 <= end
            arg = int.from_bytes(code[off : off + 4], "little")
            off += 4
        elif opcode == F64_CONST:
            assert off + 8 <= end
            arg = int.from_bytes(code[off : off + 8], "little")
            off += 8
        elif _MEMARG_OPCODES.at(opcode):
            _align, off = decode_unsigned_full(code, off)
            mem_offset, off = decode_unsigned_full(code, off)
            arg = mem_offset
        elif _MEMORY_INDEX_OPCODES.at(opcode):
            reserved = code[off]
            off += 1
            assert reserved == 0, "only memory index 0 is supported"
            arg = None
        elif _NO_OPERAND.at(opcode):
            arg = None
        else:
            reject_wasm(
                f"ERR_WASM_UNSUPPORTED_FEATURE: opcode 0x{opcode:02X} at offset {start} "
                "is not a supported basic-block opcode"
            )
        yield (opcode, arg)


def count_basic_blocks_and_max_control_depth(code: memoryview) -> tuple[int, int]:
    """Count block entries and maximum nesting without allocating their storage."""
    block_count = 0
    code_size = len(code)
    current_head: int | None = None
    pending_call_resume: int | None = None
    control_depth = 0
    max_control_depth = 0
    active_openers: StaticVector[Instr | None] = StaticVector.of(
        (None,) * FB_CONF_MAX_NESTING_DEPTH,
        capacity=FB_CONF_MAX_NESTING_DEPTH,
    )
    for instruction in iter_scan_instrs(code):
        if _BLOCK_OPENERS.at(instruction.opcode):
            if control_depth < FB_CONF_MAX_NESTING_DEPTH:
                active_openers[control_depth] = instruction
            control_depth += 1
            max_control_depth = max(max_control_depth, control_depth)
        if current_head is None:
            current_head = instruction.offset
        if _is_basic_block_boundary(instruction.opcode, active_openers, control_depth):
            if pending_call_resume != current_head:
                block_count += 1
            else:
                pending_call_resume = None
            current_head = None
            if instruction.opcode == CALL or instruction.opcode == CALL_INDIRECT:
                if instruction.end_offset < code_size:
                    block_count += 1
                    pending_call_resume = instruction.end_offset
        if instruction.opcode == END and control_depth > 0:
            control_depth -= 1
            if control_depth < FB_CONF_MAX_NESTING_DEPTH:
                active_openers[control_depth] = None
    if current_head is not None:
        if pending_call_resume != current_head:
            block_count += 1
    return block_count, max_control_depth


def _is_basic_block_boundary(
    opcode: int,
    active_openers: StaticVector[Instr | None],
    active_openers_depth: int,
) -> bool:
    """Apply static boundary attributes with the enclosing END kind resolved."""
    if not opcode_has_attribute(opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY):
        return False
    if opcode == END and active_openers_depth > 0:
        if active_openers_depth > FB_CONF_MAX_NESTING_DEPTH:
            return True
        opener = active_openers[active_openers_depth - 1]
        assert opener is not None
        return opener.opcode != LOOP
    return True


def count_basic_blocks(code: memoryview) -> int:
    """Count interpreter block entries without allocating their storage."""
    return count_basic_blocks_and_max_control_depth(code)[0]


def extract_basic_blocks(
    code: memoryview, control_map: ControlMap, pc_base: int = 0
) -> StaticVector[tuple[int, int | None, int | None, int, int]]:
    """Extracts straight-line BasicBlock PC ranges from WASM bytecode as a flat list.
    Each entry is: (head_pc, next_pc, loops_to, frame_depth, byte_span).
    A zero-span entry records a call-resume PC or a control boundary with no
    operation body. Both still need a loader-owned `rel` start for interpreter
    dispatch.
    head_pc = pc_base + start_offset. frame_depth is the count of
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
    from tier2_runtime.wasm.opcodes import BR, BR_IF, BR_TABLE, ELSE, END, IF, RETURN

    code_size = len(code)
    instr_stream = iter_scan_instrs(code)

    assert 0 <= pc_base <= 0xFFFF_FFFF - len(code)
    block_capacity = count_basic_blocks(code)
    blocks: StaticVector[tuple[int, int | None, int | None, int, int]] = StaticVector(
        capacity=block_capacity
    )
    cur_head: int | None = None
    cur_head_offset = 0
    cur_frame_depth = 0
    cur_span_end = 0  # local offset just past the last body instruction seen
    # Fixed-size buffer of still-open BLOCK/LOOP/IF instructions, indexed by
    # active_openers_depth (see FB_CONF_MAX_NESTING_DEPTH) -- never a
    # dynamically-growing list ({Policy_Memory}).
    active_openers: StaticVector[Instr | None] = StaticVector.of(
        (None,) * FB_CONF_MAX_NESTING_DEPTH,
        capacity=FB_CONF_MAX_NESTING_DEPTH,
    )
    active_openers_depth = 0
    pending_call_resume_block: int | None = None

    for ins in instr_stream:
        pc = pc_base + ins.offset
        if cur_head is None:
            cur_head = pc
            cur_head_offset = ins.offset
            # The nesting depth (count of enclosing BLOCK/LOOP/IF frames)
            # that must be active in the interpreter's frame.frames stack
            # once execution resumes here -- recorded from this single
            # linear scan, so it stays correct regardless of whether a
            # given visit at runtime arrives via interp.step() or a JIT
            # jump that skipped the frame push/pop entirely.
            cur_frame_depth = active_openers_depth
            cur_span_end = ins.offset

        if _BLOCK_OPENERS.at(ins.opcode):
            assert active_openers_depth < FB_CONF_MAX_NESTING_DEPTH, (
                "section-count prevalidation must cap control nesting"
            )
            active_openers[active_openers_depth] = ins
            active_openers_depth += 1

        is_block_boundary = _is_basic_block_boundary(
            ins.opcode, active_openers, active_openers_depth
        )
        if not is_block_boundary or ins.opcode == CALL:
            cur_span_end = ins.end_offset

        # Check if this instruction ends the basic block
        if is_block_boundary:
            if cur_head is not None:
                branch_target = None
                if (
                    opcode_has_attribute(ins.opcode, OpcodeAttribute.BRANCH)
                    and ins.operand is not None
                    and ins.operand < active_openers_depth
                ):
                    target = active_openers[active_openers_depth - 1 - ins.operand]
                    assert target is not None
                    if opcode_has_attribute(target.opcode, OpcodeAttribute.CONTROL_LOOP):
                        # Backward continuation: br/br_if taken jumps to the
                        # loop's own start (re-enter the loop body).
                        branch_target = pc_base + target.end_offset
                    else:
                        # Forward exit: br/br_if taken jumps past the block/if's
                        # matching END (block/if labels resume after, unlike
                        # loop labels which resume at the top).
                        match = control_map.block(target.offset)
                        if match is not None:
                            match_end_ip, _else_offset, _result_arity = match
                            branch_target = pc_base + (match_end_ip + 1)

                if ins.opcode == BR:
                    # Unconditional: the branch target is the block's only
                    # successor, never a "fallthrough" past this instruction.
                    next_pc = branch_target
                    loops_to = None
                elif ins.opcode == RETURN:
                    next_pc = None
                    loops_to = None
                elif ins.opcode == BR_TABLE:
                    # BR_TABLE has a runtime-selected target and is not part
                    # of a JIT basic-block body. A preceding trace must stop
                    # at this opcode so the interpreter can resolve its table.
                    next_pc = pc
                    loops_to = None
                elif ins.opcode == BR_IF:
                    # The false edge enters the first structural instruction
                    # after br_if. Keep its head in the block index so its END
                    # handlers can pop the active frames before the later
                    # post-control successor is reached.
                    next_pc = pc_base + ins.end_offset
                    loops_to = branch_target
                elif ins.opcode == IF:
                    # Conditional entry: the just-computed condition decides
                    # between the then-body (right after this IF) and the
                    # else-body / past-END (condition false, then-body
                    # skipped entirely) -- reuses the same cond!=0 -> loops_to
                    # / cond==0 -> next_pc contract as BR_IF.
                    then_target = pc_base + ins.end_offset
                    skip_target = then_target
                    match = control_map.block(ins.offset)
                    if match is not None:
                        match_end_ip, else_offset, _result_arity = match
                        skip_target = pc_base + (
                            (else_offset + 1) if else_offset is not None else (match_end_ip + 1)
                        )
                    next_pc = skip_target
                    loops_to = then_target
                elif ins.opcode == ELSE and active_openers_depth > 0:
                    # Finished running the then-body: never fall into the
                    # else-body behind it -- skip straight past the matching
                    # END.
                    if_opener = active_openers[active_openers_depth - 1]
                    assert if_opener is not None
                    match = control_map.block(if_opener.offset)
                    next_pc = (
                        pc_base + (match[0] + 1) if match is not None else pc_base + ins.end_offset
                    )
                    loops_to = None
                else:
                    next_pc = pc_base + ins.end_offset
                    loops_to = branch_target if ins.opcode == BR_IF else None

                byte_span = cur_span_end - cur_head_offset
                block = (cur_head, next_pc, loops_to, cur_frame_depth, byte_span)
                if (
                    pending_call_resume_block is not None
                    and blocks[pending_call_resume_block][0] == cur_head
                ):
                    blocks[pending_call_resume_block] = block
                    pending_call_resume_block = None
                elif not blocks.push_back(block):
                    assert False, "basic-block count exceeds its precomputed capacity"

            cur_head = None

            if ins.opcode == CALL or ins.opcode == CALL_INDIRECT:
                if ins.end_offset < code_size:
                    resume_head = pc_base + ins.end_offset
                    if not blocks.push_back((resume_head, None, None, active_openers_depth, 0)):
                        assert False, "basic-block count exceeds its precomputed capacity"
                    pending_call_resume_block = len(blocks) - 1

        if ins.opcode == END and active_openers_depth > 0:
            active_openers_depth -= 1
            active_openers[active_openers_depth] = None

    if cur_head is not None:
        byte_span = cur_span_end - cur_head_offset
        block = (cur_head, None, None, cur_frame_depth, byte_span)
        if (
            pending_call_resume_block is not None
            and blocks[pending_call_resume_block][0] == cur_head
        ):
            blocks[pending_call_resume_block] = block
        elif not blocks.push_back(block):
            assert False, "basic-block count exceeds its precomputed capacity"
    assert len(blocks) == block_capacity
    return blocks
