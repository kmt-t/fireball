"""ctypes bindings for the standalone C++ interpreter ABI."""

from __future__ import annotations

import ctypes
import sys
from _ctypes import CFuncPtr
from collections.abc import Callable
from pathlib import Path

from tier1_core.native_buffer import BufferLease as BufferLease


class NativeDispatchCall(ctypes.Structure):
    _fields_ = (
        ("context", ctypes.c_void_p),
        ("stack", ctypes.c_void_p),
        ("locals", ctypes.c_void_p),
        ("jit_provider", ctypes.c_void_p),
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
        ("stack_size", ctypes.c_uint32),
        ("stack_capacity", ctypes.c_uint32),
        ("ip", ctypes.c_uint32),
        ("local_slots", ctypes.c_uint16),
        ("rel", ctypes.c_void_p),
    )


class NativeResult(ctypes.Structure):
    _fields_ = (
        ("status", ctypes.c_uint32),
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
RUN_DISPATCH_JIT = _LIBRARY.fb_native_run_dispatch_jit
RUN_DISPATCH_JIT.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeResult))
RUN_DISPATCH_JIT.restype = ctypes.c_int


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
    stack_size: int,
    stack_capacity: int,
    ip: int,
    local_slots: int,
    *,
    call: NativeStepCall,
    result: NativeResult,
    rel: int = 0,
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
    call.stack_size = stack_size
    call.stack_capacity = stack_capacity
    call.ip = ip
    call.local_slots = local_slots
    call.rel = rel
    status = entry(ctypes.byref(call), ctypes.byref(result))
    assert status == 1, (
        f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
    )
    from tier2_runtime.abi.interpreter_abi import ExecutionContextABI

    context = ctypes.cast(context_address, ctypes.POINTER(ExecutionContextABI)).contents
    stack_size = (int(context.stack_pointer or 0) - stack_address) // 4
    return result.status, context.ip, stack_size, context.trap_code


NativeDispatchResult = tuple[int, int, int, int]
# The composition boundary borrows a C entry address, not a Python callback.
NativeDispatchEntryPoint = CFuncPtr


def run_native_dispatch(
    entry: NativeDispatchEntryPoint,
    context_address: int,
    stack_address: int,
    locals_address: int,
    *,
    call: NativeDispatchCall,
    result: NativeResult,
    jit_provider: int = 0,
) -> NativeDispatchResult:
    """Run native dispatch from its context-owned state and borrowed value stacks."""
    call.context = context_address
    call.stack = stack_address
    call.locals = locals_address
    call.jit_provider = jit_provider
    status = entry(ctypes.byref(call), ctypes.byref(result))
    assert status == 1, (
        f"C++ interpreter ABI rejected call with status {status} and error {result.error_code}"
    )
    from tier2_runtime.abi.interpreter_abi import ExecutionContextABI

    context = ctypes.cast(context_address, ctypes.POINTER(ExecutionContextABI)).contents
    stack_size = (int(context.stack_pointer or 0) - stack_address) // 4
    return result.status, context.ip, stack_size, context.trap_code
