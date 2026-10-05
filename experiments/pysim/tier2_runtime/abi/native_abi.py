"""ctypes bindings for the standalone C++ interpreter ABI."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from pathlib import Path

from tier1_core.native_buffer import BufferLease as BufferLease


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
        ("extension", ctypes.c_void_p),
        ("owner", ctypes.c_size_t),
        ("idle_budget", ctypes.c_uint32),
        ("stack_size", ctypes.c_uint32),
        ("stack_capacity", ctypes.c_uint32),
        ("initial_ip", ctypes.c_uint32),
        ("local_base", ctypes.c_uint32),
        ("local_slots", ctypes.c_uint32),
        ("control_base", ctypes.c_uint32),
        ("function_index", ctypes.c_uint32),
        ("yield_threshold", ctypes.c_uint32),
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
        ("error_code", ctypes.c_uint32),
    )


_NATIVE_LIBRARY_NAME = (
    "native_interpreter.dll" if sys.platform == "win32" else "libnative_interpreter.so"
)
_LIBRARY_PATH = Path(__file__).parent.parent / "interpreter" / _NATIVE_LIBRARY_NAME
# Keep the GIL while C++ operates on Python-owned context and stack buffers.
_LIBRARY = ctypes.PyDLL(str(_LIBRARY_PATH))


RUN_STEP = _LIBRARY.fb_native_run_step
RUN_STEP.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_STEP.restype = ctypes.c_int
RUN_CONTROL_STEP = _LIBRARY.fb_native_run_control_step
RUN_CONTROL_STEP.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_CONTROL_STEP.restype = ctypes.c_int
RUN_DEBUG_DISPATCH = _LIBRARY.fb_native_run_debug_dispatch
RUN_DEBUG_DISPATCH.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_DEBUG_DISPATCH.restype = ctypes.c_int
RUN_DISPATCH = _LIBRARY.fb_native_run_dispatch
RUN_DISPATCH.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_DISPATCH.restype = ctypes.c_int
RUN_DISPATCH_EXTENSION = _LIBRARY.fb_native_run_dispatch_extension
RUN_DISPATCH_EXTENSION.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_DISPATCH_EXTENSION.restype = ctypes.c_int


def run_step(
    entry: Callable[..., int],
    code_address: int,
    code_bytes: int,
    context_address: int,
    context_bytes: int,
    stack_address: int,
    stack_bytes: int,
    locals_address: int,
    locals_bytes: int,
    control_address: int,
    control_bytes: int,
    stack_size: int,
    stack_capacity: int,
    ip: int,
    local_slots: int,
    control_base: int,
    *,
    call: NativeStepCall,
    result: NativeResult,
) -> tuple[int, int, int, int]:
    """Call the native step ABI with addresses owned by the active context."""
    call.code = code_address
    call.code_bytes = code_bytes
    call.context = context_address
    call.context_bytes = context_bytes
    call.stack = stack_address
    call.stack_bytes = stack_bytes
    call.locals = locals_address
    call.locals_bytes = locals_bytes
    call.control_stack = control_address
    call.control_bytes = control_bytes
    call.stack_size = stack_size
    call.stack_capacity = stack_capacity
    call.ip = ip
    call.local_slots = local_slots
    call.control_base = control_base
    status = entry(ctypes.byref(call), ctypes.byref(result))
    assert status == 1, (
        f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
    )
    return result.status, result.ip, result.stack_size, result.trap_code


NativeDispatchResult = tuple[int, int, int, int]
NativeDispatchEntryPoint = Callable[..., int]


def run_native_dispatch(
    entry: Callable[..., int],
    code_address: int,
    code_bytes: int,
    context_address: int,
    context_bytes: int,
    stack_address: int,
    stack_bytes: int,
    locals_address: int,
    locals_bytes: int,
    control_address: int,
    control_bytes: int,
    stack_size: int,
    stack_capacity: int,
    initial_ip: int,
    local_base: int,
    local_slots: int,
    control_base: int,
    function_index: int,
    yield_threshold: int,
    *,
    call: NativeDispatchCall,
    result: NativeResult,
    owner: int = 0,
    idle_budget: int = 0,
) -> NativeDispatchResult:
    """Run the dispatcher over stable addresses without exporting buffers."""
    call.code = code_address
    call.code_bytes = code_bytes
    call.context = context_address
    call.context_bytes = context_bytes
    call.stack = stack_address
    call.stack_bytes = stack_bytes
    call.locals = locals_address
    call.locals_bytes = locals_bytes
    call.control_stack = control_address
    call.control_bytes = control_bytes
    call.extension = 0
    call.owner = owner
    call.idle_budget = idle_budget
    call.stack_size = stack_size
    call.stack_capacity = stack_capacity
    call.initial_ip = initial_ip
    call.local_base = local_base
    call.local_slots = local_slots
    call.control_base = control_base
    call.function_index = function_index
    call.yield_threshold = yield_threshold
    status = entry(ctypes.byref(call), ctypes.byref(result))
    assert status == 1, (
        f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
    )
    return (
        result.status,
        result.ip,
        result.stack_size,
        result.trap_code,
    )
