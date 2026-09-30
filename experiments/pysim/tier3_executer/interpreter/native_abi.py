"""ctypes bindings for the standalone C++ interpreter ABI."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from pathlib import Path

from system_containers import StaticVector, freeze_sequence


class NativeDispatchCall(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_void_p),
        ("code_bytes", ctypes.c_uint64),
        ("context", ctypes.c_void_p),
        ("context_bytes", ctypes.c_uint64),
        ("stack", ctypes.c_void_p),
        ("stack_bytes", ctypes.c_uint64),
        ("locals", ctypes.c_void_p),
        ("locals_bytes", ctypes.c_uint64),
        ("control_stack", ctypes.c_void_p),
        ("control_bytes", ctypes.c_uint64),
        ("entries", ctypes.c_void_p),
        ("entry_count", ctypes.c_uint32),
        ("entries_bytes", ctypes.c_uint64),
        ("trackable_blocks", ctypes.c_void_p),
        ("trackable_count", ctypes.c_uint32),
        ("trackable_bytes", ctypes.c_uint64),
        ("block_history", ctypes.c_void_p),
        ("block_history_bytes", ctypes.c_uint64),
        ("stack_size", ctypes.c_uint32),
        ("stack_capacity", ctypes.c_uint32),
        ("initial_ip", ctypes.c_uint32),
        ("local_base", ctypes.c_uint32),
        ("local_slots", ctypes.c_uint32),
        ("control_base", ctypes.c_uint32),
        ("function_index", ctypes.c_uint32),
        ("yield_threshold", ctypes.c_uint32),
        ("execution_count", ctypes.c_uint32),
    )


class NativeStepCall(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_void_p),
        ("code_bytes", ctypes.c_uint64),
        ("context", ctypes.c_void_p),
        ("context_bytes", ctypes.c_uint64),
        ("stack", ctypes.c_void_p),
        ("stack_bytes", ctypes.c_uint64),
        ("locals", ctypes.c_void_p),
        ("locals_bytes", ctypes.c_uint64),
        ("control_stack", ctypes.c_void_p),
        ("control_bytes", ctypes.c_uint64),
        ("stack_size", ctypes.c_uint32),
        ("stack_capacity", ctypes.c_uint32),
        ("ip", ctypes.c_uint32),
        ("local_slots", ctypes.c_uint32),
        ("control_base", ctypes.c_uint32),
    )


class NativeResult(ctypes.Structure):
    _fields_ = (
        ("status", ctypes.c_uint32),
        ("ip", ctypes.c_uint32),
        ("stack_size", ctypes.c_uint32),
        ("trap_code", ctypes.c_uint32),
        ("trace_count", ctypes.c_uint32),
        ("body_count", ctypes.c_uint32),
        ("dispatcher_trace_transitions", ctypes.c_uint32),
        ("control_handler_count", ctypes.c_uint32),
        ("eligible_block_visits", ctypes.c_uint32),
        ("interpreted_block_count", ctypes.c_uint32),
        ("error_code", ctypes.c_uint32),
    )


class PythonBuffer(ctypes.Structure):
    _fields_ = (
        ("buf", ctypes.c_void_p),
        ("obj", ctypes.c_void_p),
        ("length", ctypes.c_ssize_t),
        ("itemsize", ctypes.c_ssize_t),
        ("readonly", ctypes.c_int),
        ("ndim", ctypes.c_int),
        ("format", ctypes.c_void_p),
        ("shape", ctypes.POINTER(ctypes.c_ssize_t)),
        ("strides", ctypes.POINTER(ctypes.c_ssize_t)),
        ("suboffsets", ctypes.POINTER(ctypes.c_ssize_t)),
        ("internal", ctypes.c_void_p),
    )


_PYTHON_C_API = ctypes.PyDLL(None)
_PYOBJECT_GET_BUFFER = _PYTHON_C_API.PyObject_GetBuffer
_PYOBJECT_GET_BUFFER.argtypes = (
    ctypes.py_object,
    ctypes.POINTER(PythonBuffer),
    ctypes.c_int,
)
_PYOBJECT_GET_BUFFER.restype = ctypes.c_int
_PYBUFFER_RELEASE = _PYTHON_C_API.PyBuffer_Release
_PYBUFFER_RELEASE.argtypes = (ctypes.POINTER(PythonBuffer),)
_PYBUFFER_RELEASE.restype = None


class BufferLease:
    """Hold one CPython buffer export while C++ reads or writes its bytes."""

    __slots__ = ("_active", "_view")

    def __init__(self, owner: memoryview) -> None:
        self._view = PythonBuffer()
        assert _PYOBJECT_GET_BUFFER(owner, ctypes.byref(self._view), 0) == 0
        self._active = True

    @property
    def address(self) -> int:
        assert self._active
        return 0 if self._view.buf is None else int(self._view.buf)

    @property
    def size(self) -> int:
        assert self._active
        return self._view.length

    def release(self) -> None:
        if self._active:
            _PYBUFFER_RELEASE(ctypes.byref(self._view))
            self._active = False


_LIBRARY_PATH = Path(__file__).with_name(
    "native_interpreter.dll" if sys.platform == "win32" else "libnative_interpreter.so"
)
# Keep the GIL while C++ operates on Python-owned context and stack buffers.
_LIBRARY = ctypes.PyDLL(str(_LIBRARY_PATH))


_RUN_STEP = _LIBRARY.fb_native_run_step
_RUN_STEP.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_STEP.restype = ctypes.c_int
_RUN_CONTROL_STEP = _LIBRARY.fb_native_run_control_step
_RUN_CONTROL_STEP.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_CONTROL_STEP.restype = ctypes.c_int
_RUN_DISPATCH = _LIBRARY.fb_native_run_dispatch
_RUN_DISPATCH.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_DISPATCH.restype = ctypes.c_int
_RUN_DISPATCH_STATS = _LIBRARY.fb_native_run_dispatch_stats
_RUN_DISPATCH_STATS.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_DISPATCH_STATS.restype = ctypes.c_int
_RUN_DISPATCH_HOTSPOTS = _LIBRARY.fb_native_run_dispatch_hotspots
_RUN_DISPATCH_HOTSPOTS.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_DISPATCH_HOTSPOTS.restype = ctypes.c_int
_RUN_DISPATCH_STATS_HOTSPOTS = _LIBRARY.fb_native_run_dispatch_stats_hotspots
_RUN_DISPATCH_STATS_HOTSPOTS.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
_RUN_DISPATCH_STATS_HOTSPOTS.restype = ctypes.c_int
RUNTIME_PROFILE_STATS_AVAILABLE = True
JIT_HOTSPOT_PROFILING_AVAILABLE = True


def _array_address(buffer: ctypes.Array) -> int:
    return ctypes.addressof(buffer) if len(buffer) > 0 else 0


def _run_step(
    entry: Callable[..., int],
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    stack_size: int,
    stack_capacity: int,
    ip: int,
    local_slots: int,
    control_base: int,
) -> tuple[int, int, int, int]:
    buffers = (
        BufferLease(memoryview(code)),
        BufferLease(context),
        BufferLease(stack),
        BufferLease(locals_buffer),
        BufferLease(control_stack),
    )
    try:
        code_view, context_view, stack_view, locals_view, control_view = buffers
        call = NativeStepCall(
            code_view.address,
            code_view.size,
            context_view.address,
            context_view.size,
            stack_view.address,
            stack_view.size,
            locals_view.address,
            locals_view.size,
            control_view.address,
            control_view.size,
            stack_size,
            stack_capacity,
            ip,
            local_slots,
            control_base,
        )
        result = NativeResult()
        status = entry(ctypes.byref(call), ctypes.byref(result))
        assert status == 1, (
            f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
        )
        return result.status, result.ip, result.stack_size, result.trap_code
    finally:
        for buffer in buffers:
            buffer.release()


def run_step(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    stack_size: int,
    stack_capacity: int,
    ip: int,
    local_slots: int,
    control_base: int,
) -> tuple[int, int, int, int]:
    return _run_step(
        _RUN_STEP,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        stack_size,
        stack_capacity,
        ip,
        local_slots,
        control_base,
    )


def run_control_step(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    stack_size: int,
    stack_capacity: int,
    ip: int,
    local_slots: int,
    control_base: int,
) -> tuple[int, int, int, int]:
    return _run_step(
        _RUN_CONTROL_STEP,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        stack_size,
        stack_capacity,
        ip,
        local_slots,
        control_base,
    )


NativeDispatchResult = tuple[
    int, int, int, int, int, int, int, int, int, int, tuple[tuple[int, int], ...]
]
NativeDispatchEntryPoint = Callable[..., NativeDispatchResult]


def _run_dispatch(
    entry: Callable[..., int],
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    entries: ctypes.Array,
    trackable_blocks: ctypes.Array,
    block_history: ctypes.Array,
    entry_count: int,
    trackable_count: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    execution_count: int,
) -> NativeDispatchResult:
    buffers = (
        BufferLease(memoryview(code)),
        BufferLease(context),
        BufferLease(stack),
        BufferLease(locals_buffer),
        BufferLease(control_stack),
    )
    try:
        code_view, context_view, stack_view, locals_view, control_view = buffers
        call = NativeDispatchCall(
            code_view.address,
            code_view.size,
            context_view.address,
            context_view.size,
            stack_view.address,
            stack_view.size,
            locals_view.address,
            locals_view.size,
            control_view.address,
            control_view.size,
            _array_address(entries),
            entry_count,
            ctypes.sizeof(entries),
            _array_address(trackable_blocks),
            trackable_count,
            ctypes.sizeof(trackable_blocks),
            _array_address(block_history),
            ctypes.sizeof(block_history),
            stack_size,
            stack_capacity,
            initial_ip,
            local_base,
            local_slots,
            control_base,
            function_index,
            yield_threshold,
            execution_count,
        )
        result = NativeResult()
        status = entry(ctypes.byref(call), ctypes.byref(result))
        assert status == 1, (
            f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
        )
        assert result.eligible_block_visits <= len(block_history)
        visit_entries: StaticVector[tuple[int, int]] = StaticVector(
            capacity=result.eligible_block_visits
        )
        for index in range(result.eligible_block_visits):
            visit_entries.append((int(block_history[index]), 1))
        visits = freeze_sequence(visit_entries)
        return (
            result.status,
            result.ip,
            result.stack_size,
            result.trap_code,
            result.trace_count,
            result.body_count,
            result.dispatcher_trace_transitions,
            result.control_handler_count,
            result.eligible_block_visits,
            result.interpreted_block_count,
            visits,
        )
    finally:
        for buffer in buffers:
            buffer.release()


def run_native_dispatch(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    entries: ctypes.Array,
    trackable_blocks: ctypes.Array,
    block_history: ctypes.Array,
    entry_count: int,
    trackable_count: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    execution_count: int,
) -> NativeDispatchResult:
    return _run_dispatch(
        _RUN_DISPATCH,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        entries,
        trackable_blocks,
        block_history,
        entry_count,
        trackable_count,
        stack_size,
        stack_capacity,
        initial_ip,
        local_base,
        local_slots,
        control_base,
        function_index,
        yield_threshold,
        execution_count,
    )


def run_native_dispatch_stats(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    entries: ctypes.Array,
    trackable_blocks: ctypes.Array,
    block_history: ctypes.Array,
    entry_count: int,
    trackable_count: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    execution_count: int,
) -> NativeDispatchResult:
    return _run_dispatch(
        _RUN_DISPATCH_STATS,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        entries,
        trackable_blocks,
        block_history,
        entry_count,
        trackable_count,
        stack_size,
        stack_capacity,
        initial_ip,
        local_base,
        local_slots,
        control_base,
        function_index,
        yield_threshold,
        execution_count,
    )


def run_native_dispatch_hotspots(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    entries: ctypes.Array,
    trackable_blocks: ctypes.Array,
    block_history: ctypes.Array,
    entry_count: int,
    trackable_count: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    execution_count: int,
) -> NativeDispatchResult:
    return _run_dispatch(
        _RUN_DISPATCH_HOTSPOTS,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        entries,
        trackable_blocks,
        block_history,
        entry_count,
        trackable_count,
        stack_size,
        stack_capacity,
        initial_ip,
        local_base,
        local_slots,
        control_base,
        function_index,
        yield_threshold,
        execution_count,
    )


def run_native_dispatch_stats_hotspots(
    code: bytes,
    context: memoryview,
    stack: memoryview,
    locals_buffer: memoryview,
    control_stack: memoryview,
    entries: ctypes.Array,
    trackable_blocks: ctypes.Array,
    block_history: ctypes.Array,
    entry_count: int,
    trackable_count: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    execution_count: int,
) -> NativeDispatchResult:
    return _run_dispatch(
        _RUN_DISPATCH_STATS_HOTSPOTS,
        code,
        context,
        stack,
        locals_buffer,
        control_stack,
        entries,
        trackable_blocks,
        block_history,
        entry_count,
        trackable_count,
        stack_size,
        stack_capacity,
        initial_ip,
        local_base,
        local_slots,
        control_base,
        function_index,
        yield_threshold,
        execution_count,
    )
