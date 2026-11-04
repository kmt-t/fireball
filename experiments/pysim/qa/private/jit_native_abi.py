"""QA-only native inspection, explicit cache operations and standalone compiler bindings."""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from tier1_core.native_printk import NativePrintkWriter
from tier2_runtime.abi.native_abi import JIT_CALL_HANDLER_TABLE

# A benchmark variant names its own build of the probe library in a separate process.
_NATIVE_LIBRARY_PATH = (
    Path(os.environ["FB_QA_JIT_PROBE_LIBRARY"])
    if "FB_QA_JIT_PROBE_LIBRARY" in os.environ
    else Path(__file__).with_name("jit_probe.dll" if sys.platform == "win32" else "libjit_probe.so")
)
_NATIVE_LIBRARY = ctypes.PyDLL(str(_NATIVE_LIBRARY_PATH))
_COMPILE_MAX_BODY_BYTES = 1536


@dataclass(frozen=True, slots=True)
class NativeByteView:
    """Borrowed bytes; the caller keeps their storage alive through compilation."""

    address: int
    byte_length: int


@dataclass(frozen=True, slots=True)
class NativeLocalLayout:
    offsets: NativeByteView
    types: NativeByteView
    local_count: int
    total_words: int


@dataclass(frozen=True, slots=True)
class WasmBlock:
    """Loader metadata consumed by the native block compiler."""

    head_pc: int
    code: NativeByteView
    offset: int
    byte_length: int
    next_pc: int | None
    loops_to: int | None
    locals: NativeLocalLayout
    frame_depth: int = 0
    block_index: int = 0xFFFF
    next_block_index: int | None = None
    loops_to_block_index: int | None = None
    interpreter_annotations: NativeByteView = NativeByteView(0, 0)
    interpreter_annotation_byte_offset: int = 0
    # Module global value widths in words, one byte per global; byte_length is the global count.
    global_widths: NativeByteView = NativeByteView(0, 0)
    # Operand stack words when control reaches this block; fixed by WASM validation.
    entry_stack_words: int = 0


class CacheField(IntEnum):
    """Scalar fields shared with the native cache ABI."""

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
    LOOKUP_CALLS = 25


class NativeTraceHeader(ctypes.Structure):
    """Physical code-adjacent PIC header written by the C++ compiler."""

    _fields_ = (
        ("chain_target_address", ctypes.c_uint64),
        ("common_code_relative", ctypes.c_int32),
        ("head_pc", ctypes.c_uint32),
        ("blob_bytes", ctypes.c_uint32),
        ("stack_words", ctypes.c_uint8),
        ("chain_words", ctypes.c_uint8),
        ("chain_body_count", ctypes.c_uint8),
        ("frame_depth", ctypes.c_uint8),
        ("exit_kind", ctypes.c_uint8),
        ("block_index", ctypes.c_uint16),
        ("const_rel_offset", ctypes.c_uint32),
    )


class NativeTraceExit(ctypes.Structure):
    """Code-adjacent metadata consumed by the native interpreter on trace exit."""

    _fields_ = (
        ("body_end_ip", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("interpreter_rel_bytes", ctypes.c_uint32),
        ("block_index", ctypes.c_uint16),
        ("frame_depth", ctypes.c_uint8),
    )


class CacheLookup(ctypes.Structure):
    """Compact QA result for a cache lookup: the resolved bank."""

    _fields_ = (("bank_id", ctypes.c_uint8),)


assert NativeTraceHeader.common_code_relative.offset == 8
assert NativeTraceHeader.head_pc.offset == 12
assert NativeTraceHeader.blob_bytes.offset == 16
assert NativeTraceHeader.stack_words.offset == 20
assert NativeTraceHeader.chain_words.offset == 21
assert NativeTraceHeader.chain_body_count.offset == 22
assert NativeTraceHeader.frame_depth.offset == 23
assert NativeTraceHeader.exit_kind.offset == 24
assert NativeTraceHeader.frame_depth.size == 1
assert NativeTraceHeader.block_index.offset == 26
assert NativeTraceHeader.const_rel_offset.offset == 28
assert ctypes.sizeof(NativeTraceHeader) == 32
assert NativeTraceExit.block_index.offset == 16
assert NativeTraceExit.frame_depth.offset == 18
assert ctypes.sizeof(NativeTraceExit) == 20


@dataclass(slots=True)
class TraceQaState:
    """Python-only observations assembled from input metadata and native snapshots."""

    head_pc: int = 0
    size_bytes: int = 0
    next_pc: int = 0xFFFF_FFFF
    loops_to: int = 0xFFFF_FFFF
    stack_words: int = 0
    byte_span: int = 0
    frame_depth: int = 0
    dispatch_next_pc: int = 0xFFFF_FFFF
    dispatch_loops_to: int = 0xFFFF_FFFF
    code_offset: int = 0xFFFF_FFFF
    chain_next_pc: int = 0xFFFF_FFFF
    entry_address: int = 0
    chain_target_address: int = 0
    code_blob: ctypes.POINTER(ctypes.c_uint8) = field(
        default_factory=lambda: ctypes.POINTER(ctypes.c_uint8)()
    )
    blob_bytes: int = 0
    chain_terminal: int | None = None
    chain_words: int = 0
    chain_bodies: int = 0
    exec_count: ctypes.POINTER(ctypes.c_uint32) = field(
        default_factory=lambda: ctypes.POINTER(ctypes.c_uint32)()
    )


class _NativeByteView(ctypes.Structure):
    _fields_ = (("address", ctypes.c_void_p), ("byte_length", ctypes.c_uint32))


class _NativeLocalLayout(ctypes.Structure):
    _fields_ = (
        ("offsets", _NativeByteView),
        ("types", _NativeByteView),
        ("count", ctypes.c_uint16),
        ("total_words", ctypes.c_uint16),
    )


class _NativeWasmBlock(ctypes.Structure):
    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("offset", ctypes.c_uint32),
        ("byte_length", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("frame_depth", ctypes.c_uint8),
        ("code", _NativeByteView),
        ("locals", _NativeLocalLayout),
        ("extension_data", ctypes.c_void_p),
        ("block_index", ctypes.c_uint16),
        ("next_block_index", ctypes.c_uint16),
        ("loops_to_block_index", ctypes.c_uint16),
        ("interpreter_annotations", _NativeByteView),
        ("interpreter_annotation_byte_offset", ctypes.c_uint32),
        ("entry_stack_words", ctypes.c_uint16),
        ("global_widths", _NativeByteView),
    )


assert _NativeLocalLayout.count.offset == 32
assert ctypes.sizeof(_NativeLocalLayout) == 40
assert _NativeWasmBlock.code.offset == 24
assert _NativeWasmBlock.locals.offset == 40
assert _NativeWasmBlock.extension_data.offset == 80
assert _NativeWasmBlock.block_index.offset == 88
assert _NativeWasmBlock.interpreter_annotations.offset == 96
assert _NativeWasmBlock.interpreter_annotation_byte_offset.offset == 112
assert _NativeWasmBlock.entry_stack_words.offset == 116
assert _NativeWasmBlock.global_widths.offset == 120
assert ctypes.sizeof(_NativeWasmBlock) == 136


RUNTIME_SIZE = _NATIVE_LIBRARY.fb_jit_runtime_size
RUNTIME_SIZE.argtypes = ()
RUNTIME_SIZE.restype = ctypes.c_size_t

RUNTIME_ALIGNMENT = _NATIVE_LIBRARY.fb_jit_runtime_alignment
RUNTIME_ALIGNMENT.argtypes = ()
RUNTIME_ALIGNMENT.restype = ctypes.c_size_t

PLUGIN_INIT = _NATIVE_LIBRARY.fb_jit_plugin_init
PLUGIN_INIT.argtypes = (
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
)
PLUGIN_INIT.restype = ctypes.c_void_p
PLUGIN_REQUIRED_BYTES = _NATIVE_LIBRARY.fb_jit_plugin_required_bytes
PLUGIN_REQUIRED_BYTES.argtypes = (ctypes.c_void_p,)
PLUGIN_REQUIRED_BYTES.restype = ctypes.c_size_t
PLUGIN_ALIGNMENT = _NATIVE_LIBRARY.fb_jit_plugin_alignment
PLUGIN_ALIGNMENT.argtypes = ()
PLUGIN_ALIGNMENT.restype = ctypes.c_size_t


RUNTIME_SCALAR = _NATIVE_LIBRARY.fb_jit_runtime_scalar


RUNTIME_SCALAR.argtypes = (ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_SCALAR.restype = ctypes.c_uint64


RUNTIME_FIND = _NATIVE_LIBRARY.fb_jit_runtime_find


RUNTIME_FIND.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(CacheLookup),
)


RUNTIME_FIND.restype = ctypes.c_int

RUNTIME_HEADER = _NATIVE_LIBRARY.fb_qa_runtime_header
RUNTIME_HEADER.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
RUNTIME_HEADER.restype = ctypes.c_void_p


RUNTIME_INSERT = _NATIVE_LIBRARY.fb_jit_runtime_insert


RUNTIME_INSERT.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
)


RUNTIME_INSERT.restype = ctypes.c_int


RUNTIME_LOOKUP = _NATIVE_LIBRARY.fb_jit_runtime_lookup


RUNTIME_LOOKUP.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(CacheLookup),
)


RUNTIME_LOOKUP.restype = ctypes.c_int

RUNTIME_ROTATE = _NATIVE_LIBRARY.fb_jit_runtime_rotate


RUNTIME_ROTATE.argtypes = (ctypes.c_void_p,)


RUNTIME_ROTATE.restype = ctypes.c_int


RUNTIME_FLUSH = _NATIVE_LIBRARY.fb_qa_runtime_flush
RUNTIME_FLUSH.argtypes = (ctypes.c_void_p,)
RUNTIME_FLUSH.restype = ctypes.c_int


AGING_STEP_UNITS = _NATIVE_LIBRARY.fb_qa_aging_step_units
AGING_STEP_UNITS.argtypes = ()
AGING_STEP_UNITS.restype = ctypes.c_uint32

AGING_STEP_SCAN_BYTES = _NATIVE_LIBRARY.fb_qa_aging_step_scan_bytes
AGING_STEP_SCAN_BYTES.argtypes = ()
AGING_STEP_SCAN_BYTES.restype = ctypes.c_uint32

RUNTIME_AGE = _NATIVE_LIBRARY.fb_jit_runtime_age
RUNTIME_AGE.argtypes = (ctypes.c_void_p,)
RUNTIME_AGE.restype = ctypes.c_int

RUNTIME_SNAPSHOT = _NATIVE_LIBRARY.fb_qa_runtime_snapshot


RUNTIME_SNAPSHOT.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


RUNTIME_SNAPSHOT.restype = ctypes.c_int


RUNTIME_RESET_COUNTS = _NATIVE_LIBRARY.fb_jit_runtime_reset_counts
RUNTIME_RESET_COUNTS.argtypes = (ctypes.c_void_p,)
RUNTIME_RESET_COUNTS.restype = None

RUNTIME_RESET_CHAIN_MEASUREMENTS = _NATIVE_LIBRARY.fb_jit_runtime_reset_chain_measurements
RUNTIME_RESET_CHAIN_MEASUREMENTS.argtypes = (ctypes.c_void_p,)
RUNTIME_RESET_CHAIN_MEASUREMENTS.restype = None

RUNTIME_YIELD = _NATIVE_LIBRARY.fb_jit_runtime_yield
RUNTIME_YIELD.argtypes = (ctypes.c_void_p,)
RUNTIME_YIELD.restype = ctypes.c_int

RUNTIME_PRINT = _NATIVE_LIBRARY.fb_qa_runtime_print_measurements
RUNTIME_PRINT.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativePrintkWriter))
RUNTIME_PRINT.restype = ctypes.c_int


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
        ("chain_dispatcher_offset", ctypes.c_uint32),
        ("chain_dispatcher_size", ctypes.c_uint32),
        ("header_bytes", ctypes.c_uint32),
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
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_size_t),
)


_COMMON_INSTALL.restype = ctypes.c_int


def install_common_trace(
    memory: NativeExecutableMemory,
    offset: int,
    blob: bytes,
    block_index: int = 0xFFFF,
) -> int:
    assert 0 <= offset <= 0xFFFF_FFFF and len(blob) <= 0xFFFF_FFFF
    assert 0 <= block_index <= 0xFFFF
    source = ctypes.cast(ctypes.c_char_p(blob), ctypes.POINTER(ctypes.c_uint8))
    entry = ctypes.c_size_t()
    result = _COMMON_INSTALL(
        ctypes.byref(memory),
        offset,
        source,
        len(blob),
        block_index,
        ctypes.byref(entry),
    )
    assert result == 1
    return entry.value


def pack_trace_header(chain: int, helper: int) -> bytes:
    assert 0 <= chain <= 0xFFFF_FFFF_FFFF_FFFF
    assert 0 <= helper <= 0xFFFF_FFFF_FFFF_FFFF
    return struct.pack("<QQiIIBBBBIII4x", chain, helper, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


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


MEMORY_NO_RWX = _NATIVE_LIBRARY.fb_jit_memory_no_rwx


MEMORY_NO_RWX.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_NO_RWX.restype = ctypes.c_int


MEMORY_VALID_PROTECTION = _NATIVE_LIBRARY.fb_jit_memory_valid_protection


MEMORY_VALID_PROTECTION.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


MEMORY_VALID_PROTECTION.restype = ctypes.c_int


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


RUNTIME_COMPILE = _NATIVE_LIBRARY.fb_qa_runtime_compile
RUNTIME_COMPILE.argtypes = (ctypes.c_void_p,)
RUNTIME_COMPILE.restype = ctypes.c_int

RUNTIME_FILTERED_LOOKUP = _NATIVE_LIBRARY.fb_jit_runtime_filtered_lookup


RUNTIME_FILTERED_LOOKUP.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(CacheLookup),
)


RUNTIME_FILTERED_LOOKUP.restype = ctypes.c_int


MEMORY_ADDRESS = _NATIVE_LIBRARY.fb_jit_memory_address


MEMORY_ADDRESS.argtypes = (ctypes.POINTER(NativeExecutableMemory), ctypes.c_uint32)


MEMORY_ADDRESS.restype = ctypes.c_size_t


_COMPILE_BLOCK = _NATIVE_LIBRARY.fb_jit_compile_block


_COMPILE_BLOCK.argtypes = (
    ctypes.POINTER(_NativeWasmBlock),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
)


_COMPILE_BLOCK.restype = ctypes.c_int


def compile_wasm(block: WasmBlock, trace: TraceQaState) -> bytes | None:
    assert 0 <= block.head_pc < 0xFFFF_FFFF
    assert 0 <= block.offset <= block.code.byte_length <= 0xFFFF_FFFF
    assert 0 < block.byte_length <= block.code.byte_length - block.offset
    assert block.code.address != 0
    assert block.next_pc is None or 0 <= block.next_pc < 0xFFFF_FFFF
    assert block.locals.offsets.byte_length == block.locals.local_count * 2
    assert block.locals.offsets.byte_length == 0 or block.locals.offsets.address != 0
    assert block.locals.types.byte_length == block.locals.local_count
    assert block.locals.types.byte_length == 0 or block.locals.types.address != 0
    assert 0 <= block.locals.local_count <= 0xFFFF_FFFF
    assert 0 <= block.locals.total_words <= 0xFFFF_FFFF
    request = marshal_block(block)
    output = (
        ctypes.c_uint8
        * (
            _COMPILE_MAX_BODY_BYTES
            + COMMON_LAYOUT.header_bytes
            + ctypes.sizeof(NativeTraceExit)
            + ctypes.alignment(ctypes.c_void_p)
            - 1
        )
    )()
    body_scratch = (ctypes.c_uint8 * _COMPILE_MAX_BODY_BYTES)()
    status = int(
        _COMPILE_BLOCK(
            ctypes.byref(request),
            output,
            len(output),
            body_scratch,
        )
    )
    if status == 0:
        return None
    assert status == 1, "native block compiler contract violation"
    header = NativeTraceHeader.from_buffer(output)
    assert COMMON_LAYOUT.header_bytes == ctypes.sizeof(NativeTraceHeader)
    trace.head_pc = header.head_pc
    trace.size_bytes = header.blob_bytes
    boundary = block.offset + block.byte_length
    boundary_opcode = (
        ctypes.c_uint8.from_address(block.code.address + boundary).value
        if boundary < block.code.byte_length
        else -1
    )
    control_boundary = boundary_opcode in (0x04, 0x0D, 0x0E, 0x0F)
    trace.next_pc = None if block.loops_to is not None or control_boundary else block.next_pc
    trace.loops_to = 0xFFFF_FFFF if block.loops_to is None else block.loops_to
    trace.stack_words = header.stack_words
    trace.byte_span = block.byte_length
    trace.frame_depth = header.frame_depth
    trace.dispatch_next_pc = 0xFFFF_FFFF if block.next_pc is None else block.next_pc
    trace.dispatch_loops_to = 0xFFFF_FFFF
    trace.chain_target_address = header.chain_target_address
    trace.chain_next_pc = 0xFFFF_FFFF
    trace.blob_bytes = header.blob_bytes
    trace.code_blob = ctypes.cast(output, ctypes.POINTER(ctypes.c_uint8))
    return bytes(output[: header.blob_bytes])


class CardViews(ctypes.Structure):
    """Addresses of the card arrays that a product runtime owns inside its region."""

    _fields_ = (
        ("states", ctypes.c_size_t),
        ("dirty", ctypes.c_size_t),
        ("trackable", ctypes.c_size_t),
        ("cursor", ctypes.c_size_t),
        ("cards", ctypes.c_uint32),
        ("state_bytes", ctypes.c_uint32),
        ("dirty_bytes", ctypes.c_uint32),
    )


ADMISSION_RECORD = _NATIVE_LIBRARY.fb_qa_admission_record
ADMISSION_RECORD.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
ADMISSION_RECORD.restype = ctypes.c_int
ADMISSION_RANK_THRESHOLD = _NATIVE_LIBRARY.fb_qa_set_admission_rank_threshold
ADMISSION_RANK_THRESHOLD.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32)
ADMISSION_RANK_THRESHOLD.restype = None
ADMISSION_WINDOW = _NATIVE_LIBRARY.fb_qa_admission_window
ADMISSION_WINDOW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
ADMISSION_WINDOW.restype = None


RUNTIME_CARD_VIEWS = _NATIVE_LIBRARY.fb_qa_runtime_card_views
RUNTIME_CARD_VIEWS.argtypes = (ctypes.c_void_p, ctypes.POINTER(CardViews))
RUNTIME_CARD_VIEWS.restype = None


class NativeRuntimeStorage:
    """A product runtime built by the plugin initializer in a QA-owned, aligned region."""

    __slots__ = ("_closed", "_region", "pointer")

    def __init__(self, view_address: int, module_id: int) -> None:
        size = int(PLUGIN_REQUIRED_BYTES(view_address))
        alignment = int(PLUGIN_ALIGNMENT())
        assert alignment > 0 and alignment & (alignment - 1) == 0
        self._region = (ctypes.c_uint8 * (size + alignment))()
        offset = -ctypes.addressof(self._region) % alignment
        storage = (ctypes.c_uint8 * size).from_buffer(self._region, offset)
        self._closed = False
        provider = PLUGIN_INIT(
            ctypes.addressof(storage), size, view_address, module_id, JIT_CALL_HANDLER_TABLE
        )
        assert provider is not None
        # The provider's first field is the owning runtime, which the QA probes take.
        self.pointer = ctypes.c_size_t.from_address(provider).value
        assert self.pointer != 0

    def card_views(self) -> CardViews:
        views = CardViews()
        RUNTIME_CARD_VIEWS(self.pointer, ctypes.byref(views))
        return views

    def close(self) -> None:
        if not self._closed:
            RUNTIME_CLOSE(self.pointer)
            self._closed = True


RUNTIME_CLOSE = _NATIVE_LIBRARY.fb_qa_runtime_close
RUNTIME_CLOSE.argtypes = (ctypes.c_void_p,)
RUNTIME_CLOSE.restype = None


def marshal_block(block: WasmBlock) -> _NativeWasmBlock:
    """Convert loader block metadata into the compiler input layout."""
    return _NativeWasmBlock(
        block.head_pc,
        block.offset,
        block.byte_length,
        0xFFFF_FFFF if block.next_pc is None else block.next_pc,
        0xFFFF_FFFF if block.loops_to is None else block.loops_to,
        block.frame_depth,
        _NativeByteView(block.code.address, block.code.byte_length),
        _NativeLocalLayout(
            _NativeByteView(block.locals.offsets.address, block.locals.offsets.byte_length),
            _NativeByteView(block.locals.types.address, block.locals.types.byte_length),
            block.locals.local_count,
            block.locals.total_words,
        ),
        0,
        block.block_index,
        0xFFFF if block.next_block_index is None else block.next_block_index,
        0xFFFF if block.loops_to_block_index is None else block.loops_to_block_index,
        _NativeByteView(
            block.interpreter_annotations.address,
            block.interpreter_annotations.byte_length,
        ),
        block.interpreter_annotation_byte_offset,
        block.entry_stack_words,
        _NativeByteView(block.global_widths.address, block.global_widths.byte_length),
    )


REGION_INIT = _NATIVE_LIBRARY.fb_jit_region_init


REGION_INIT.argtypes = (ctypes.POINTER(NativeExecutableMemory),)


REGION_INIT.restype = ctypes.c_int


OWNED_PC = _NATIVE_LIBRARY.fb_qa_owned_pc
OWNED_PC.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
OWNED_PC.restype = ctypes.c_uint64


class ResidentRecord(ctypes.Structure):
    _fields_ = (
        ("bank_id", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("pc", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
    )


RESIDENT_RECORD = _NATIVE_LIBRARY.fb_qa_resident_record
RESIDENT_RECORD.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ResidentRecord))
RESIDENT_RECORD.restype = ctypes.c_int


def resident_records(pointer: int) -> tuple[tuple[int, int, int], ...]:
    """Return (head_pc, bank_id, execution count) of every resident trace."""
    records: list[tuple[int, int, int]] = []
    record = ResidentRecord()
    while RESIDENT_RECORD(pointer, len(records), ctypes.byref(record)):
        records.append((record.pc, record.bank_id, record.count))
    return tuple(records)


RUNTIME_COMMON_TRACE_ENTRY = _NATIVE_LIBRARY.fb_qa_runtime_common_trace_entry
RUNTIME_COMMON_TRACE_ENTRY.argtypes = (ctypes.c_void_p,)
RUNTIME_COMMON_TRACE_ENTRY.restype = ctypes.c_size_t

RUNTIME_PROVIDER = _NATIVE_LIBRARY.fb_qa_runtime_provider
RUNTIME_PROVIDER.argtypes = (ctypes.c_void_p,)
RUNTIME_PROVIDER.restype = ctypes.c_void_p
