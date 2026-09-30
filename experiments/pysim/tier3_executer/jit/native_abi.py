"""ctypes binding for the standalone C++ JIT compiler ABI."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterable
from pathlib import Path

from wasm_module import LocalWidthMap, WasmOperand


class JitInstruction(ctypes.Structure):
    _fields_ = (
        ("opcode", ctypes.c_uint32),
        ("has_operand", ctypes.c_uint32),
        ("operand", ctypes.c_uint64),
    )


class JitCompileResult(ctypes.Structure):
    _fields_ = (
        ("body_bytes", ctypes.c_uint32),
        ("helper_index", ctypes.c_int32),
        ("helper_words", ctypes.c_uint32),
        ("max_spilled_words", ctypes.c_uint32),
        ("stack_location_count", ctypes.c_uint32),
        ("helper_header_patch_offset", ctypes.c_int32),
        ("helper_exit_patch_offset", ctypes.c_int32),
        ("exit_patch_offset", ctypes.c_int32),
        ("chain_dispatch_patch_offset", ctypes.c_int32),
    )


_NATIVE_LIBRARY_PATH = Path(__file__).with_name(
    "trace_compiler.dll" if sys.platform == "win32" else "libtrace_compiler.so"
)
_NATIVE_LIBRARY = ctypes.PyDLL(str(_NATIVE_LIBRARY_PATH))
_COMPILE_TRACE = _NATIVE_LIBRARY.fb_jit_compile_trace
_COMPILE_TRACE.argtypes = (
    ctypes.POINTER(JitInstruction),
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.POINTER(JitCompileResult),
)
_COMPILE_TRACE.restype = ctypes.c_int
_CHAIN_DISPATCHER = _NATIVE_LIBRARY.fb_jit_common_chain_dispatcher
_CHAIN_DISPATCHER.argtypes = (
    ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint32),
)
_CHAIN_DISPATCHER.restype = None

_NATIVE_CACHE_PATH = Path(__file__).with_name(
    "fast_cache.dll" if sys.platform == "win32" else "libfast_cache.so"
)
_NATIVE_CACHE_LIBRARY = ctypes.PyDLL(str(_NATIVE_CACHE_PATH))
_FAST_CACHE_SLOT_COUNT = _NATIVE_CACHE_LIBRARY.fb_jit_fast_cache_slot_count
_FAST_CACHE_SLOT_COUNT.argtypes = ()
_FAST_CACHE_SLOT_COUNT.restype = ctypes.c_uint32
_FAST_CACHE_SLOT = _NATIVE_CACHE_LIBRARY.fb_jit_fast_cache_slot
_FAST_CACHE_SLOT.argtypes = (ctypes.c_uint32,)
_FAST_CACHE_SLOT.restype = ctypes.c_uint32
_FAST_CACHE_LOOKUP = _NATIVE_CACHE_LIBRARY.fb_jit_fast_cache_lookup
_FAST_CACHE_LOOKUP.argtypes = (
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint64),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
)
_FAST_CACHE_LOOKUP.restype = ctypes.c_uint64
_FAST_CACHE_STORE = _NATIVE_CACHE_LIBRARY.fb_jit_fast_cache_store
_FAST_CACHE_STORE.argtypes = (
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint64),
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_uint32,
    ctypes.c_uint64,
)
_FAST_CACHE_STORE.restype = ctypes.c_int
_FAST_CACHE_CLEAR = _NATIVE_CACHE_LIBRARY.fb_jit_fast_cache_clear
_FAST_CACHE_CLEAR.argtypes = (
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint64),
    ctypes.POINTER(ctypes.c_uint8),
)
_FAST_CACHE_CLEAR.restype = ctypes.c_int

FAST_CACHE_SLOT_COUNT = int(_FAST_CACHE_SLOT_COUNT())


def compile_trace(
    instructions: Iterable[tuple[int, WasmOperand]],
    next_pc: int | None,
    loops_to: int | None,
    byte_span: int,
    local_widths: LocalWidthMap,
    *,
    tail_context_helper: bool = False,
    helper_target_addr: int = 0,
) -> tuple[bytes, int, int, int, int, int, int, int, int] | None:
    """Marshal one bounded trace into the C++ compiler ABI."""

    assert byte_span > 0
    native_instructions = (JitInstruction * byte_span)()
    instruction_count = 0
    for opcode, operand in instructions:
        assert instruction_count < byte_span
        record = native_instructions[instruction_count]
        record.opcode = opcode
        if operand is None:
            record.has_operand = 0
            record.operand = 0
        else:
            record.has_operand = 1
            record.operand = operand & 0xFFFF_FFFF_FFFF_FFFF
        instruction_count += 1

    widths_view = local_widths.raw_view
    widths = (
        (ctypes.c_uint8 * len(widths_view)).from_buffer(widths_view)
        if len(widths_view) > 0
        else (ctypes.c_uint8 * 0)()
    )
    output = (ctypes.c_uint8 * 8192)()
    result = JitCompileResult()
    status = _COMPILE_TRACE(
        native_instructions,
        instruction_count,
        next_pc is not None,
        0 if next_pc is None else next_pc,
        loops_to is not None,
        0 if loops_to is None else loops_to,
        byte_span,
        widths,
        len(widths_view),
        len(local_widths),
        local_widths.slot_words,
        tail_context_helper,
        helper_target_addr,
        output,
        len(output),
        ctypes.byref(result),
    )
    if status == 0:
        return None
    assert status == 1, f"C++ JIT compiler ABI rejected call with status {status}"
    body = bytes(output[: result.body_bytes])
    return (
        body,
        result.helper_index,
        result.helper_words,
        result.max_spilled_words,
        result.stack_location_count,
        result.helper_header_patch_offset,
        result.helper_exit_patch_offset,
        result.exit_patch_offset,
        result.chain_dispatch_patch_offset,
    )


def common_chain_dispatcher() -> tuple[int, bytes]:
    """Copy the immutable chain dispatcher bytes through the C ABI."""

    data = ctypes.POINTER(ctypes.c_uint8)()
    size = ctypes.c_uint32()
    offset = ctypes.c_uint32()
    _CHAIN_DISPATCHER(ctypes.byref(data), ctypes.byref(size), ctypes.byref(offset))
    assert data and size.value > 0
    return offset.value, ctypes.string_at(data, size.value)


class NativeFastCacheStorage:
    """Fixed arrays owned by Python and operated on through the C++ cache ABI."""

    __slots__ = ("_handles", "_keys", "_occupied")

    def __init__(self) -> None:
        self._keys = (ctypes.c_uint32 * FAST_CACHE_SLOT_COUNT)()
        self._handles = (ctypes.c_uint64 * FAST_CACHE_SLOT_COUNT)()
        self._occupied = (ctypes.c_uint8 * FAST_CACHE_SLOT_COUNT)()
        assert _FAST_CACHE_CLEAR(self._keys, self._handles, self._occupied) == 1

    def slot(self, pc: int) -> int:
        return int(_FAST_CACHE_SLOT(pc))

    def lookup(self, pc: int) -> int:
        return int(_FAST_CACHE_LOOKUP(self._keys, self._handles, self._occupied, pc))

    def store(self, pc: int, handle: int) -> None:
        assert _FAST_CACHE_STORE(self._keys, self._handles, self._occupied, pc, handle) == 1

    def clear(self) -> None:
        assert _FAST_CACHE_CLEAR(self._keys, self._handles, self._occupied) == 1
