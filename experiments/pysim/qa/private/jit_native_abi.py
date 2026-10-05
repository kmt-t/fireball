"""QA-only native inspection, explicit cache operations and standalone compiler bindings."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from bump_allocator import BumpAllocator
from tier1_core.native_printk import NativePrintkWriter
from tier2_runtime.wasm.module import LocalWidthMap, WasmOperand

_NATIVE_LIBRARY_PATH = Path(__file__).with_name(
    "jit_probe.dll" if sys.platform == "win32" else "libjit_probe.so"
)
_NATIVE_LIBRARY = ctypes.PyDLL(str(_NATIVE_LIBRARY_PATH))
_COMPILE_MAX_BODY_BYTES = 96
_COMPILE_BYTE_STORAGE_BYTES = 16 + _COMPILE_MAX_BODY_BYTES * 2
_COMPILE_STACK_DEPTH = 16


class JitInstruction(ctypes.Structure):
    _fields_ = (
        ("opcode", ctypes.c_uint32),
        ("has_operand", ctypes.c_uint32),
        ("operand", ctypes.c_uint64),
    )


@dataclass(frozen=True, slots=True)
class NativeByteView:
    """Borrowed bytes; the caller keeps their storage alive through compilation."""

    address: int
    byte_length: int


@dataclass(frozen=True, slots=True)
class NativeLocalLayout:
    widths: NativeByteView
    local_count: int
    slot_words: int


@dataclass(frozen=True, slots=True)
class WasmBlock:
    """Loader metadata consumed by the native block compiler."""

    head_pc: int
    code: NativeByteView
    offset: int
    byte_length: int
    next_pc: int | None
    locals: NativeLocalLayout


class CacheField(IntEnum):
    """Scalar fields shared with the native cache ABI."""

    PROMOTIONS = 3
    EVICTIONS = 4
    GENERATION = 5
    AGING_STEPS = 6
    AGING_UNITS = 7
    AGING_SCANNED = 8
    BANK_USED = 9
    BANK_ENTRY_CAPACITY = 11
    EXECUTION_COUNT = 15
    HISTORY_OVERWRITTEN = 16
    HISTORY_APPROXIMATE = 17
    TRACKABLE_GENERATION = 18
    QUEUE_COUNT = 19
    RESIDENT_COUNT = 20
    ROTATIONS = 21
    AGING_NS = 22
    COMPILE_ATTEMPTS = 23
    COMPILE_NS = 24


class NativeTraceFixups(ctypes.Structure):
    _fields_ = (
        ("entry_body", ctypes.c_int32),
        ("entry_prologue", ctypes.c_int32),
        ("exit", ctypes.c_int32),
        ("helper_header", ctypes.c_int32),
        ("helper_exit", ctypes.c_int32),
        ("chain_dispatch", ctypes.c_int32),
        ("helper_offset", ctypes.c_uint32),
    )


class NativeCacheTrace(ctypes.Structure):
    """Stable descriptor borrowed and mutated by the Tier 3 C++ cache."""

    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("size_bytes", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("has_return_value", ctypes.c_uint32),
        ("result_words", ctypes.c_uint32),
        ("stack_words", ctypes.c_uint32),
        ("byte_span", ctypes.c_uint32),
        ("frame_depth", ctypes.c_uint32),
        ("dispatch_next_pc", ctypes.c_uint32),
        ("dispatch_loops_to", ctypes.c_uint32),
        ("code_offset", ctypes.c_uint32),
        ("chain_next_pc", ctypes.c_uint32),
        ("entry_address", ctypes.c_size_t),
        ("chain_target_address", ctypes.c_uint64),
        ("code_blob", ctypes.POINTER(ctypes.c_uint8)),
        ("blob_bytes", ctypes.c_uint32),
        ("fixups", NativeTraceFixups),
        ("chain_terminal", ctypes.c_void_p),
        ("chain_words", ctypes.c_uint32),
        ("chain_bodies", ctypes.c_uint32),
        ("exec_count", ctypes.POINTER(ctypes.c_uint32)),
    )


class _NativeByteView(ctypes.Structure):
    _fields_ = (("address", ctypes.c_void_p), ("byte_length", ctypes.c_uint32))


class _NativeLocalLayout(ctypes.Structure):
    _fields_ = (
        ("widths", _NativeByteView),
        ("count", ctypes.c_uint32),
        ("slot_words", ctypes.c_uint32),
    )


class _NativeWasmBlock(ctypes.Structure):
    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("offset", ctypes.c_uint32),
        ("byte_length", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("frame_depth", ctypes.c_uint32),
        ("jit_score", ctypes.c_int64),
        ("code", _NativeByteView),
        ("locals", _NativeLocalLayout),
    )


class NativeProfile(ctypes.Structure):
    _fields_ = (
        ("trackable", ctypes.POINTER(ctypes.c_uint8)),
        ("mask_bytes", ctypes.c_uint32),
        ("module_id", ctypes.c_uint32),
        ("enabled", ctypes.c_uint32),
        ("candidate_threshold", ctypes.c_uint32),
        ("min_trace_bytes", ctypes.c_uint32),
        ("code_bytes", ctypes.c_uint64),
        ("history_capacity", ctypes.c_uint32),
        ("queue_capacity", ctypes.c_uint32),
        ("compiler_enabled", ctypes.c_uint32),
        ("blocks", ctypes.POINTER(_NativeWasmBlock)),
        ("block_count", ctypes.c_uint32),
    )


RUNTIME_SIZE = _NATIVE_LIBRARY.fb_jit_runtime_size
RUNTIME_SIZE.argtypes = ()
RUNTIME_SIZE.restype = ctypes.c_size_t

RUNTIME_ALIGNMENT = _NATIVE_LIBRARY.fb_jit_runtime_alignment
RUNTIME_ALIGNMENT.argtypes = ()
RUNTIME_ALIGNMENT.restype = ctypes.c_size_t

RUNTIME_INIT = _NATIVE_LIBRARY.fb_jit_runtime_init
RUNTIME_INIT.argtypes = (
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_size_t,
)
RUNTIME_INIT.restype = ctypes.c_void_p

RUNTIME_ERROR = _NATIVE_LIBRARY.fb_jit_runtime_error
RUNTIME_ERROR.argtypes = (ctypes.c_void_p,)
RUNTIME_ERROR.restype = ctypes.c_int

RUNTIME_SCALAR = _NATIVE_LIBRARY.fb_jit_runtime_scalar


RUNTIME_SCALAR.argtypes = (ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_SCALAR.restype = ctypes.c_uint64


RUNTIME_FIND = _NATIVE_LIBRARY.fb_jit_runtime_find


RUNTIME_FIND.argtypes = (ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_FIND.restype = ctypes.c_uint64


RUNTIME_HAS_TOKEN = _NATIVE_LIBRARY.fb_jit_runtime_has_token


RUNTIME_HAS_TOKEN.argtypes = (ctypes.c_void_p, ctypes.c_uint64)


RUNTIME_HAS_TOKEN.restype = ctypes.c_int


RUNTIME_INSERT = _NATIVE_LIBRARY.fb_jit_runtime_insert


RUNTIME_INSERT.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeCacheTrace), ctypes.c_uint64)


RUNTIME_INSERT.restype = ctypes.c_int


RUNTIME_LOOKUP = _NATIVE_LIBRARY.fb_jit_runtime_lookup


RUNTIME_LOOKUP.argtypes = (ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_LOOKUP.restype = ctypes.c_uint64


RUNTIME_ROTATE = _NATIVE_LIBRARY.fb_jit_runtime_rotate


RUNTIME_ROTATE.argtypes = (ctypes.c_void_p,)


RUNTIME_ROTATE.restype = ctypes.c_int


RUNTIME_FLUSH = _NATIVE_LIBRARY.fb_jit_runtime_flush
RUNTIME_FLUSH.argtypes = (ctypes.c_void_p,)
RUNTIME_FLUSH.restype = ctypes.c_int

RUNTIME_BIND_CARDS = _NATIVE_LIBRARY.fb_jit_runtime_bind_cards
RUNTIME_BIND_CARDS.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_uint32,
    ctypes.c_uint32,
)
RUNTIME_BIND_CARDS.restype = ctypes.c_int

RUNTIME_AGE = _NATIVE_LIBRARY.fb_jit_runtime_age
RUNTIME_AGE.argtypes = (ctypes.c_void_p,)
RUNTIME_AGE.restype = ctypes.c_int

RUNTIME_AUTO_AGE = _NATIVE_LIBRARY.fb_jit_runtime_auto_age


RUNTIME_AUTO_AGE.argtypes = (ctypes.c_void_p, ctypes.c_int)


RUNTIME_AUTO_AGE.restype = None


RUNTIME_SNAPSHOT = _NATIVE_LIBRARY.fb_qa_runtime_snapshot


RUNTIME_SNAPSHOT.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_SNAPSHOT.restype = ctypes.c_int


RUNTIME_RESET_COUNTS = _NATIVE_LIBRARY.fb_jit_runtime_reset_counts
RUNTIME_RESET_COUNTS.argtypes = (ctypes.c_void_p,)
RUNTIME_RESET_COUNTS.restype = None

RUNTIME_YIELD = _NATIVE_LIBRARY.fb_jit_runtime_yield
RUNTIME_YIELD.argtypes = (ctypes.c_void_p,)
RUNTIME_YIELD.restype = ctypes.c_int

RUNTIME_PRINT = _NATIVE_LIBRARY.fb_qa_runtime_print_measurements
RUNTIME_PRINT.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativePrintkWriter))
RUNTIME_PRINT.restype = ctypes.c_int


class NativeRuntimeStorage:
    """Fixed, aligned caller storage containing the complete C++ JitRuntime object."""

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_closed",
        "_compile_byte_storage",
        "_compile_stack_locations",
        "_cursor",
        "_dirty",
        "_states",
        "_storage",
        "arena_size",
        "pointer",
    )

    def __init__(self, capacity: int, entries: int, offsets: tuple[int, int, int]) -> None:
        self.pointer = None
        self._closed = False
        self.arena_size = int(RUNTIME_SIZE())
        assert int(RUNTIME_ALIGNMENT()) <= ctypes.alignment(ctypes.c_uint64)
        self._storage = (ctypes.c_uint64 * ((self.arena_size + 7) // 8))()
        self._compile_byte_storage = (
            ctypes.c_uint64 * (_COMPILE_BYTE_STORAGE_BYTES // ctypes.sizeof(ctypes.c_uint64))
        )()
        self._compile_stack_locations = (ctypes.c_int16 * _COMPILE_STACK_DEPTH)()
        native_offsets = (ctypes.c_uint32 * 3)(*offsets)
        self.pointer = RUNTIME_INIT(
            ctypes.cast(self._storage, ctypes.POINTER(ctypes.c_uint8)),
            ctypes.sizeof(self._storage),
            capacity,
            entries,
            native_offsets,
            ctypes.cast(self._compile_byte_storage, ctypes.POINTER(ctypes.c_uint8)),
            _COMPILE_BYTE_STORAGE_BYTES,
            self._compile_stack_locations,
            _COMPILE_STACK_DEPTH,
        )
        assert self.pointer is not None
        self._allocator: BumpAllocator | None = None
        self._arena_offset: int | None = None
        self._states: ctypes.Array | None = None
        self._dirty: ctypes.Array | None = None
        self._cursor: ctypes.c_uint32 | None = None

    def close(self) -> None:
        if not self._closed and self.pointer is not None:
            RUNTIME_CLOSE(self.pointer)
            self._closed = True

    def __del__(self) -> None:
        self.close()

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        if self._allocator is allocator:
            return
        self._arena_offset = allocator.allocate(self.arena_size, alignment=8)
        self._allocator = allocator

    def bind_cards(
        self,
        states: bytearray,
        dirty: bytearray,
        cards: int,
        shift: int,
        cursor: ctypes.c_uint32,
        units: int,
        scan_bytes: int,
    ) -> None:
        assert 0 <= cards <= 0xFFFF_FFFF
        assert 1 <= units <= 0xFFFF_FFFF and 1 <= scan_bytes <= 0xFFFF_FFFF
        state_view = (ctypes.c_uint8 * len(states)).from_buffer(states)
        dirty_view = (ctypes.c_uint8 * len(dirty)).from_buffer(dirty)
        assert (
            RUNTIME_BIND_CARDS(
                self.pointer,
                state_view,
                len(states),
                dirty_view,
                len(dirty),
                cards,
                shift,
                ctypes.byref(cursor),
                units,
                scan_bytes,
            )
            == 1
        )
        self._states, self._dirty, self._cursor = state_view, dirty_view, cursor


class NativeExecutableMemory(ctypes.Structure):
    _fields_ = (
        ("base", ctypes.c_size_t),
        ("size", ctypes.c_uint32),
        ("protection", ctypes.c_uint32),
        ("patching", ctypes.c_uint32),
    )


class NativeCommonLayout(ctypes.Structure):
    _fields_ = (
        ("region_bytes", ctypes.c_uint32),
        ("common_code_bytes", ctypes.c_uint32),
        ("prologue_offset", ctypes.c_uint32),
        ("prologue_size", ctypes.c_uint32),
        ("epilogue_offset", ctypes.c_uint32),
        ("epilogue_size", ctypes.c_uint32),
        ("helper_offset", ctypes.c_uint32),
        ("helper_size", ctypes.c_uint32),
        ("chain_dispatcher_offset", ctypes.c_uint32),
        ("chain_dispatcher_size", ctypes.c_uint32),
        ("absolute_pool_offset", ctypes.c_uint32),
        ("absolute_pool_size", ctypes.c_uint32),
        ("context_helper_offset", ctypes.c_uint32),
        ("header_bytes", ctypes.c_uint32),
        ("entry_stub_bytes", ctypes.c_uint32),
    )


_COMMON_LAYOUT = _NATIVE_LIBRARY.fb_jit_common_layout


_COMMON_LAYOUT.argtypes = (ctypes.POINTER(NativeCommonLayout),)


_COMMON_LAYOUT.restype = None


COMMON_LAYOUT = NativeCommonLayout()


_COMMON_LAYOUT(ctypes.byref(COMMON_LAYOUT))


_COMMON_INSTALL = _NATIVE_LIBRARY.fb_jit_common_install


_COMMON_INSTALL.argtypes = (
    ctypes.POINTER(NativeExecutableMemory),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(NativeTraceFixups),
    ctypes.POINTER(ctypes.c_size_t),
)


_COMMON_INSTALL.restype = ctypes.c_int


def install_common_trace(
    memory: NativeExecutableMemory, offset: int, blob: bytes, fixups: NativeTraceFixups
) -> int:
    assert 0 <= offset <= 0xFFFF_FFFF and len(blob) <= 0xFFFF_FFFF
    source = ctypes.cast(ctypes.c_char_p(blob), ctypes.POINTER(ctypes.c_uint8))
    entry = ctypes.c_size_t()
    assert (
        _COMMON_INSTALL(
            ctypes.byref(memory),
            offset,
            source,
            len(blob),
            ctypes.byref(fixups),
            ctypes.byref(entry),
        )
        == 1
    )
    return entry.value


_PACK_HEADER = _NATIVE_LIBRARY.fb_jit_pack_header


_PACK_HEADER.argtypes = (
    ctypes.c_uint64,
    ctypes.c_uint64,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
)


_PACK_HEADER.restype = ctypes.c_int


def pack_trace_header(chain: int, helper: int) -> bytes:
    output = (ctypes.c_uint8 * COMMON_LAYOUT.header_bytes)()
    assert _PACK_HEADER(chain, helper, output, len(output)) == 1
    return bytes(output)


MEMORY_INIT = _NATIVE_LIBRARY.fb_jit_memory_init


MEMORY_INIT.argtypes = (ctypes.POINTER(NativeExecutableMemory), ctypes.c_uint32)


MEMORY_INIT.restype = ctypes.c_int


MEMORY_BEGIN = _NATIVE_LIBRARY.fb_jit_memory_begin


MEMORY_BEGIN.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_BEGIN.restype = ctypes.c_int


MEMORY_COMMIT = _NATIVE_LIBRARY.fb_jit_memory_commit


MEMORY_COMMIT.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_COMMIT.restype = ctypes.c_int


MEMORY_FINALIZE = _NATIVE_LIBRARY.fb_jit_memory_finalize


MEMORY_FINALIZE.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_FINALIZE.restype = ctypes.c_int


MEMORY_WRITE = _NATIVE_LIBRARY.fb_jit_memory_write


MEMORY_WRITE.argtypes = (
    ctypes.POINTER(NativeExecutableMemory),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
)


MEMORY_WRITE.restype = ctypes.c_int


MEMORY_CLOSE = _NATIVE_LIBRARY.fb_jit_memory_close


MEMORY_CLOSE.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_CLOSE.restype = ctypes.c_int


REGION_SIZE = _NATIVE_LIBRARY.fb_jit_region_size
REGION_SIZE.argtypes = (
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint64),
)
REGION_SIZE.restype = ctypes.c_int

CARD_COUNT = _NATIVE_LIBRARY.fb_jit_card_count
CARD_COUNT.argtypes = (
    ctypes.c_uint64,
    ctypes.c_uint32,
)
CARD_COUNT.restype = ctypes.c_int64

CARD_INDEX = _NATIVE_LIBRARY.fb_jit_card_index


CARD_INDEX.argtypes = (
    ctypes.c_uint64,
    ctypes.c_uint32,
    ctypes.c_uint32,
)


CARD_INDEX.restype = ctypes.c_int64


BITS = _NATIVE_LIBRARY.fb_jit_bits


BITS.argtypes = (
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
)


BITS.restype = ctypes.c_int


RUNTIME_BIND_PROFILE = _NATIVE_LIBRARY.fb_jit_runtime_bind_profile
RUNTIME_BIND_PROFILE.argtypes = (
    ctypes.c_void_p,
    NativeProfile,
)
RUNTIME_BIND_PROFILE.restype = ctypes.c_int

RUNTIME_SUPPRESS = _NATIVE_LIBRARY.fb_jit_runtime_suppress


RUNTIME_SUPPRESS.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
)


RUNTIME_SUPPRESS.restype = ctypes.c_int


RUNTIME_RECORD = _NATIVE_LIBRARY.fb_jit_runtime_record


RUNTIME_RECORD.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
)


RUNTIME_RECORD.restype = ctypes.c_int


RUNTIME_VISITS = _NATIVE_LIBRARY.fb_qa_runtime_visits


RUNTIME_VISITS.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_uint32,
    ctypes.c_uint64,
)


RUNTIME_VISITS.restype = ctypes.c_int


RUNTIME_ANALYZE = _NATIVE_LIBRARY.fb_qa_runtime_analyze


RUNTIME_ANALYZE.argtypes = (
    ctypes.c_void_p,
    ctypes.c_int,
)


RUNTIME_ANALYZE.restype = ctypes.c_int


RUNTIME_COMPILE = _NATIVE_LIBRARY.fb_jit_runtime_compile
RUNTIME_COMPILE.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
)
RUNTIME_COMPILE.restype = ctypes.c_int

RUNTIME_FILTERED_LOOKUP = _NATIVE_LIBRARY.fb_jit_runtime_filtered_lookup


RUNTIME_FILTERED_LOOKUP.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
)


RUNTIME_FILTERED_LOOKUP.restype = ctypes.c_uint64


MEMORY_ADDRESS = _NATIVE_LIBRARY.fb_jit_memory_address


MEMORY_ADDRESS.argtypes = (ctypes.POINTER(NativeExecutableMemory), ctypes.c_uint32)


MEMORY_ADDRESS.restype = ctypes.c_size_t


_COMPILE_BLOCK = _NATIVE_LIBRARY.fb_jit_compile_block


_COMPILE_BLOCK.argtypes = (
    ctypes.POINTER(_NativeWasmBlock),
    ctypes.POINTER(NativeCacheTrace),
    ctypes.POINTER(NativeTraceFixups),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_uint32,
)


_COMPILE_BLOCK.restype = ctypes.c_int


def compile_wasm(
    block: WasmBlock, trace: NativeCacheTrace
) -> tuple[bytes, NativeTraceFixups] | None:
    assert 0 <= block.head_pc < 0xFFFF_FFFF
    assert 0 <= block.offset <= block.code.byte_length <= 0xFFFF_FFFF
    assert 0 < block.byte_length <= block.code.byte_length - block.offset
    assert block.code.address != 0
    assert block.next_pc is None or 0 <= block.next_pc < 0xFFFF_FFFF
    assert 0 <= block.locals.widths.byte_length <= 0xFFFF_FFFF
    assert block.locals.widths.byte_length == 0 or block.locals.widths.address != 0
    assert 0 <= block.locals.local_count <= 0xFFFF_FFFF
    assert 0 <= block.locals.slot_words <= 0xFFFF_FFFF
    request = marshal_block(block)
    output = (ctypes.c_uint8 * (_COMPILE_MAX_BODY_BYTES + COMMON_LAYOUT.header_bytes))()
    body_scratch = (ctypes.c_uint8 * _COMPILE_MAX_BODY_BYTES)()
    stack_locations = (ctypes.c_int16 * 16)()
    fixups = NativeTraceFixups()
    status = int(
        _COMPILE_BLOCK(
            ctypes.byref(request),
            ctypes.byref(trace),
            ctypes.byref(fixups),
            output,
            len(output),
            body_scratch,
            stack_locations,
            len(stack_locations),
        )
    )
    if status == 0:
        return None
    assert status == 1, "native block compiler contract violation"
    return bytes(output[: trace.size_bytes]), fixups


RUNTIME_TRACE = _NATIVE_LIBRARY.fb_jit_runtime_trace


RUNTIME_TRACE.argtypes = (ctypes.c_void_p, ctypes.c_uint64)


RUNTIME_TRACE.restype = ctypes.POINTER(NativeCacheTrace)


RUNTIME_CLOSE = _NATIVE_LIBRARY.fb_jit_runtime_close
RUNTIME_CLOSE.argtypes = (ctypes.c_void_p,)
RUNTIME_CLOSE.restype = None


def marshal_block(
    block: WasmBlock, loops_to: int | None = None, frame_depth: int = 0, jit_score: int = 0
) -> _NativeWasmBlock:
    """Convert loader metadata once into the module's borrowed registration table."""
    return _NativeWasmBlock(
        block.head_pc,
        block.offset,
        block.byte_length,
        0xFFFF_FFFF if block.next_pc is None else block.next_pc,
        0xFFFF_FFFF if loops_to is None else loops_to,
        frame_depth,
        jit_score,
        _NativeByteView(block.code.address, block.code.byte_length),
        _NativeLocalLayout(
            _NativeByteView(block.locals.widths.address, block.locals.widths.byte_length),
            block.locals.local_count,
            block.locals.slot_words,
        ),
    )


REGION_INIT = _NATIVE_LIBRARY.fb_jit_region_init


REGION_INIT.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


REGION_INIT.restype = ctypes.c_int


class _NativeInstructionBlock(ctypes.Structure):
    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("byte_span", ctypes.c_uint32),
        ("context_helper", ctypes.c_uint32),
        ("helper_target", ctypes.c_uint64),
        ("instructions", ctypes.POINTER(JitInstruction)),
        ("instruction_count", ctypes.c_uint32),
        ("locals", _NativeLocalLayout),
    )


_COMPILE_INSTRUCTIONS = _NATIVE_LIBRARY.fb_jit_compile_instructions


_COMPILE_INSTRUCTIONS.argtypes = (
    ctypes.POINTER(_NativeInstructionBlock),
    ctypes.POINTER(NativeCacheTrace),
    ctypes.POINTER(NativeTraceFixups),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_uint32,
)


_COMPILE_INSTRUCTIONS.restype = ctypes.c_int


def compile_instructions(
    trace: NativeCacheTrace,
    instructions: Iterable[tuple[int, WasmOperand]],
    byte_length: int,
    local_layout: LocalWidthMap,
    context_helper: bool,
    helper_address: int,
) -> tuple[bytes, NativeTraceFixups] | None:
    """Marshal explicit instructions through one complete native compiler boundary."""
    assert 0 < byte_length <= 0xFFFF_FFFF
    assert 0 <= helper_address <= 0xFFFF_FFFF_FFFF_FFFF
    records = (JitInstruction * byte_length)()
    count = 0
    for opcode, operand in instructions:
        assert count < byte_length and 0 <= opcode <= 0xFFFF_FFFF
        records[count] = JitInstruction(
            opcode,
            int(operand is not None),
            0 if operand is None else operand & 0xFFFF_FFFF_FFFF_FFFF,
        )
        count += 1
    widths = local_layout.raw_view
    width_buffer = (ctypes.c_uint8 * len(widths)).from_buffer(widths)
    request = _NativeInstructionBlock(
        trace.head_pc,
        trace.next_pc,
        trace.loops_to,
        byte_length,
        int(context_helper),
        helper_address,
        records,
        count,
        _NativeLocalLayout(
            _NativeByteView(ctypes.addressof(width_buffer), len(widths)),
            local_layout.count,
            local_layout.slot_words,
        ),
    )
    output = (ctypes.c_uint8 * (_COMPILE_MAX_BODY_BYTES + COMMON_LAYOUT.header_bytes))()
    body_scratch = (ctypes.c_uint8 * _COMPILE_MAX_BODY_BYTES)()
    stack_locations = (ctypes.c_int16 * 16)()
    fixups = NativeTraceFixups()
    status = int(
        _COMPILE_INSTRUCTIONS(
            ctypes.byref(request),
            ctypes.byref(trace),
            ctypes.byref(fixups),
            output,
            len(output),
            body_scratch,
            stack_locations,
            len(stack_locations),
        )
    )
    if status == 0:
        return None
    assert status == 1, "native instruction compiler contract violation"
    return bytes(output[: trace.size_bytes]), fixups


RUNTIME_RUN = _NATIVE_LIBRARY.fb_jit_runtime_run
RUNTIME_RUN.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
RUNTIME_RUN.restype = ctypes.c_int


OWNED_PC = _NATIVE_LIBRARY.fb_qa_owned_pc
OWNED_PC.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
OWNED_PC.restype = ctypes.c_uint64


class ResidentRecord(ctypes.Structure):
    _fields_ = (("token", ctypes.c_uint64), ("pc", ctypes.c_uint32), ("count", ctypes.c_uint32))


RESIDENT_RECORD = _NATIVE_LIBRARY.fb_qa_resident_record
RESIDENT_RECORD.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ResidentRecord))
RESIDENT_RECORD.restype = ctypes.c_int


def resident_records(pointer: int) -> tuple[tuple[int, int, int], ...]:
    records: list[tuple[int, int, int]] = []
    record = ResidentRecord()
    while RESIDENT_RECORD(pointer, len(records), ctypes.byref(record)):
        records.append((record.token, record.pc, record.count))
    return tuple(records)


RUNTIME_BIND_DISPATCH = _NATIVE_LIBRARY.fb_qa_runtime_bind_dispatch
RUNTIME_BIND_DISPATCH.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
RUNTIME_BIND_DISPATCH.restype = None
