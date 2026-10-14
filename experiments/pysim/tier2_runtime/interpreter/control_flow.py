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

from collections.abc import Iterator
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
    NOP,
    RETURN,
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
        (BLOCK, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
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
        (FC_PREFIX, OpcodeAttribute.BASIC_BLOCK_BOUNDARY),
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
    """Return whether native dispatch must delegate this block boundary."""

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


def _count_interpreter_annotation_pool_bytes(code: memoryview) -> int:
    annotation_bytes = 0
    for instruction in iter_scan_instrs(code):
        opcode = instruction.opcode
        if opcode == LOCAL_GET or opcode == LOCAL_SET or opcode == LOCAL_TEE:
            annotation_bytes += 3  # frame word offset and local width
        elif opcode == GLOBAL_GET or opcode == GLOBAL_SET:
            annotation_bytes += 2  # global index
        elif opcode == I32_CONST:
            annotation_bytes += 4  # signed value
        elif opcode == I64_CONST:
            annotation_bytes += 8  # signed value
        elif _MEMARG_OPCODES.at(opcode):
            annotation_bytes += 4  # effective offset
        elif opcode == BR:
            annotation_bytes += 4  # target block, stack base, and result width
        elif opcode == BR_IF:
            annotation_bytes += 4  # target block, stack base, and result width
        elif opcode == BR_TABLE:
            label_count, _ = decode_unsigned_full(code, instruction.offset + 1)
            require_wasm(
                label_count <= 0xFFFF,
                "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
            )
            annotation_bytes += BR_TABLE_COUNT_BYTES + (label_count + 1) * BR_TABLE_TARGET_BYTES
        elif opcode == IF:
            annotation_bytes += 4
        elif opcode == BLOCK or opcode == LOOP or opcode == ELSE or opcode == END:
            annotation_bytes += 2
        elif opcode == CALL or opcode == CALL_INDIRECT:
            annotation_bytes += 4  # function/type index and continuation block index
        elif opcode == FC_PREFIX:
            assert instruction.operand is not None
            if (
                FC_I32_TRUNC_SAT_F32_S <= instruction.operand <= FC_I64_TRUNC_SAT_F64_U
                or instruction.operand == FC_MEMORY_COPY
                or instruction.operand == FC_MEMORY_FILL
            ):
                annotation_bytes += 3  # subopcode and successor block index
        elif opcode == DROP or opcode == SELECT:
            annotation_bytes += 1  # operand width, derived by stack type analysis
        if _has_instruction_width_record(opcode):
            annotation_bytes += _instruction_width_record_size(
                instruction.end_offset - instruction.offset
            )
    return annotation_bytes


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

    annotations = InterpreterAnnotationPool(capacity=total_bytes, allocator=allocator)
    block_offsets: StaticVector[annotation_offset_t] = StaticVector(capacity=len(block_heads))

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

    for block_number, start in enumerate(block_heads):
        end = block_heads[block_number + 1] if block_number + 1 < len(block_heads) else len(code)
        appended = block_offsets.push_back(annotation_offset_t(len(annotations)))
        assert appended
        block_code = code[start:end]
        for instruction in iter_scan_instrs(block_code):
            cursor = instruction.offset + 1
            function_offset = start + instruction.offset
            opcode = instruction.opcode
            if _has_instruction_width_record(opcode):
                append_instruction_width(
                    instruction_width_t(instruction.end_offset - instruction.offset)
                )
            if opcode == LOCAL_GET or opcode == LOCAL_SET or opcode == LOCAL_TEE:
                local_index, _ = decode_unsigned_full(block_code, cursor)
                assert local_index < len(local_layout)
                frame_offset: frame_word_offset_t = local_layout.offset(local_index)
                local_width = local_layout.size_words(local_index)
                assert frame_offset <= 0xFFFF and local_width <= 0xFF
                append_u16(frame_offset)
                annotations.append_byte(local_width)
            elif opcode == DROP:
                width = 1 if drop_widths is None else drop_widths.view().find(function_offset) or 1
                assert width == 1 or width == 2
                annotations.append_byte(width)
            elif opcode == SELECT:
                width = (
                    1 if select_widths is None else select_widths.view().find(function_offset) or 1
                )
                assert width == 1 or width == 2
                annotations.append_byte(width)
            elif opcode == GLOBAL_GET or opcode == GLOBAL_SET:
                raw_global_index, _ = decode_unsigned_full(block_code, cursor)
                global_index = global_index_t(raw_global_index)
                require_wasm(
                    global_index <= 0xFFFF,
                    "ERR_WASM_UNSUPPORTED_FEATURE: global index exceeds u16",
                )
                append_u16(global_index)
            elif opcode == I32_CONST:
                value, _ = decode_signed_full(block_code, cursor, bits=32)
                append_u32(value & 0xFFFF_FFFF)
            elif opcode == I64_CONST:
                value, _ = decode_signed_full(block_code, cursor, bits=64)
                append_wide(value)
            elif _MEMARG_OPCODES.at(opcode):
                _alignment, next_cursor = decode_unsigned_full(block_code, cursor)
                offset, _ = decode_unsigned_full(block_code, next_cursor)
                append_u32(offset)
            elif opcode == BR_TABLE:
                label_count, next_cursor = decode_unsigned_full(block_code, cursor)
                require_wasm(
                    label_count <= 0xFFFF,
                    "ERR_WASM_UNSUPPORTED_FEATURE: br_table label count exceeds u16",
                )
                append_u16(label_count)
                cursor = next_cursor
            elif opcode == CALL_INDIRECT:
                raw_type_index, next_cursor = decode_unsigned_full(block_code, cursor)
                type_index = type_index_t(raw_type_index)
                require_wasm(
                    type_index <= 0xFFFF,
                    "ERR_WASM_UNSUPPORTED_FEATURE: type index exceeds u16",
                )
                append_u16(type_index)
                _table_index, _ = decode_unsigned_full(block_code, next_cursor)
            elif opcode == CALL:
                raw_function_index, _ = decode_unsigned_full(block_code, cursor)
                function_index = function_index_t(raw_function_index)
                require_wasm(
                    function_index <= 0xFFFF,
                    "ERR_WASM_UNSUPPORTED_FEATURE: function index exceeds u16",
                )
                append_u16(function_index)
            elif opcode == FC_PREFIX:
                subopcode, cursor = decode_unsigned_full(block_code, cursor)
                assert subopcode <= 0xFF
                annotations.append_byte(subopcode)
                if subopcode == FC_MEMORY_COPY:
                    _destination_memory, cursor = decode_unsigned_full(block_code, cursor)
                    _source_memory, _ = decode_unsigned_full(block_code, cursor)
                elif subopcode == FC_MEMORY_FILL:
                    _memory_index, _ = decode_unsigned_full(block_code, cursor)
            if opcode == BLOCK or opcode == LOOP:
                append_u16(block_index_for_offset(start + instruction.end_offset))
            elif opcode == IF:
                then_target = start + instruction.end_offset
                match = None if control_map is None else control_map.block(function_offset)
                if match is None:
                    else_target = len(code)
                else:
                    match_end, else_offset, _result_arity = match
                    else_target = else_offset + 1 if else_offset is not None else match_end + 1
                append_u16(block_index_for_offset(then_target))
                append_u16(block_index_for_offset(else_target))
            elif opcode == ELSE:
                if control_map is None:
                    target = len(code)
                else:
                    target = control_map.else_target(function_offset)
                append_u16(block_index_for_offset(target))
            elif opcode == END:
                append_u16(block_index_for_offset(start + instruction.end_offset))
            elif opcode == BR:
                append_branch_target(function_offset, 0)
            elif opcode == BR_IF:
                append_branch_target(function_offset, 0)
            elif opcode == BR_TABLE:
                label_count, target_cursor = decode_unsigned_full(
                    block_code, instruction.offset + 1
                )
                for target_number in range(label_count + 1):
                    _, target_cursor = decode_unsigned_full(block_code, target_cursor)
                    append_branch_target(function_offset, target_number)
            elif opcode == CALL or opcode == CALL_INDIRECT:
                append_u16(block_index_for_offset(start + instruction.end_offset))
            elif (
                opcode == FC_PREFIX
                and instruction.operand is not None
                and (
                    FC_I32_TRUNC_SAT_F32_S <= instruction.operand <= FC_I64_TRUNC_SAT_F64_U
                    or instruction.operand == FC_MEMORY_COPY
                    or instruction.operand == FC_MEMORY_FILL
                )
            ):
                append_u16(block_index_for_offset(start + instruction.end_offset))

    assert len(annotations) == total_bytes
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
    for instruction in iter_scan_instrs(code):
        if instruction.opcode == BLOCK or instruction.opcode == LOOP or instruction.opcode == IF:
            control_depth += 1
            max_control_depth = max(max_control_depth, control_depth)
        elif instruction.opcode == END and control_depth > 0:
            control_depth -= 1
        if current_head is None:
            current_head = instruction.offset
        if opcode_has_attribute(instruction.opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY):
            if pending_call_resume != current_head:
                block_count += 1
            else:
                pending_call_resume = None
            current_head = None
            if instruction.opcode == CALL or instruction.opcode == CALL_INDIRECT:
                if instruction.end_offset < code_size:
                    block_count += 1
                    pending_call_resume = instruction.end_offset
    if current_head is not None:
        if pending_call_resume != current_head:
            block_count += 1
    return block_count, max_control_depth


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

        if (
            not opcode_has_attribute(ins.opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY)
            or ins.opcode == CALL
            or ins.opcode == FC_PREFIX
        ):
            cur_span_end = ins.end_offset

        # Check if this instruction ends the basic block
        if opcode_has_attribute(ins.opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY):
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
                elif ins.opcode == FC_PREFIX:
                    # The Runtime API executes the prefixed operation before
                    # handing off to the next indexed instruction boundary.
                    next_pc = pc_base + ins.end_offset
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
