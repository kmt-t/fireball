"""QA-only diagnostic dispatch entry points."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from tier2_runtime.abi import native_abi
from tier2_runtime.interpreter.interpreter import NativeDispatchEntryPoint


class NativeDiagnosticResult(native_abi.NativeResult):
    """QA-owned result suffix; product results contain execution state only."""

    _fields_ = (
        ("trace_count", ctypes.c_uint32),
        ("body_count", ctypes.c_uint32),
        ("dispatcher_trace_transitions", ctypes.c_uint32),
        ("control_handler_count", ctypes.c_uint32),
        ("eligible_block_visits", ctypes.c_uint32),
        ("interpreted_block_count", ctypes.c_uint32),
    )

    def reset_counters(self) -> None:
        self.trace_count = 0
        self.body_count = 0
        self.dispatcher_trace_transitions = 0
        self.control_handler_count = 0
        self.eligible_block_visits = 0
        self.interpreted_block_count = 0


_library = ctypes.PyDLL(
    str(
        Path(__file__).with_name(
            "interpreter_probe.dll" if sys.platform == "win32" else "libinterpreter_probe.so"
        )
    )
)
RUN_DISPATCH = _library.fb_qa_run_dispatch
RUN_DISPATCH.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeDiagnosticResult))
RUN_DISPATCH.restype = ctypes.c_int
RUN_DISPATCH_EXTENSION = _library.fb_qa_run_dispatch_extension
RUN_DISPATCH_EXTENSION.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeDiagnosticResult))
RUN_DISPATCH_EXTENSION.restype = ctypes.c_int
RUN_DISPATCH_STATS = _library.fb_qa_run_dispatch_stats
RUN_DISPATCH_STATS.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeDiagnosticResult))
RUN_DISPATCH_STATS.restype = ctypes.c_int
RUN_DISPATCH_STATS_EXTENSION = _library.fb_qa_run_dispatch_stats_extension
RUN_DISPATCH_STATS_EXTENSION.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeDiagnosticResult))
RUN_DISPATCH_STATS_EXTENSION.restype = ctypes.c_int
RUN_DISPATCH_JIT = _library.fb_qa_run_dispatch_jit
RUN_DISPATCH_JIT.argtypes = (ctypes.c_void_p, ctypes.POINTER(NativeDiagnosticResult))
RUN_DISPATCH_JIT.restype = ctypes.c_int
RUN_DISPATCH_JIT_EXTENSION = _library.fb_qa_run_dispatch_jit_extension
RUN_DISPATCH_JIT_EXTENSION.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(NativeDiagnosticResult),
)
RUN_DISPATCH_JIT_EXTENSION.restype = ctypes.c_int
RUN_DISPATCH_JIT_STATS = _library.fb_qa_run_dispatch_jit_stats
RUN_DISPATCH_JIT_STATS.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(NativeDiagnosticResult),
)
RUN_DISPATCH_JIT_STATS.restype = ctypes.c_int
NATIVE_RUNTIME_PROFILE_STATS_AVAILABLE = True


def select_native_dispatch_entry(
    collect_stats: bool, with_extension: bool
) -> NativeDispatchEntryPoint:
    if collect_stats:
        return RUN_DISPATCH_STATS_EXTENSION if with_extension else RUN_DISPATCH_STATS
    return RUN_DISPATCH_EXTENSION if with_extension else RUN_DISPATCH


def select_runtime_dispatch_entry(
    collect_stats: bool, with_jit: bool
) -> NativeDispatchEntryPoint:
    """Select QA dispatch instrumentation independently from the JIT provider."""
    if with_jit:
        return RUN_DISPATCH_JIT_STATS if collect_stats else RUN_DISPATCH_JIT
    return RUN_DISPATCH_STATS if collect_stats else RUN_DISPATCH


def select_native_jit_dispatch_entry(
    collect_stats: bool,
) -> NativeDispatchEntryPoint:
    """Select the JIT+QA-extension dispatcher used by snapshot-driven tests."""
    return (
        RUN_DISPATCH_JIT_STATS_EXTENSION
        if collect_stats
        else RUN_DISPATCH_JIT_EXTENSION
    )
