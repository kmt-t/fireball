"""Opaque native plugin entry points and aligned storage lifetime."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_library = ctypes.PyDLL(
    str(
        Path(__file__).with_name(
            "trace_compiler.dll" if sys.platform == "win32" else "libtrace_compiler.so"
        )
    )
)
REGION_ALIGNMENT = _library.fb_jit_plugin_alignment
REGION_ALIGNMENT.argtypes = ()
REGION_ALIGNMENT.restype = ctypes.c_size_t
REQUIRED_BYTES = _library.fb_jit_plugin_required_bytes
REQUIRED_BYTES.argtypes = (ctypes.c_void_p,)
REQUIRED_BYTES.restype = ctypes.c_size_t
RUNTIME_INIT = _library.fb_jit_plugin_init
RUNTIME_INIT.argtypes = (
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
)
RUNTIME_INIT.restype = ctypes.c_void_p
RUNTIME_RUN = _library.fb_jit_runtime_run
RUNTIME_RUN.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
RUNTIME_RUN.restype = ctypes.c_int
RUNTIME_CLOSE = _library.fb_jit_runtime_close
RUNTIME_CLOSE.argtypes = (ctypes.c_void_p,)
RUNTIME_CLOSE.restype = None
RUNTIME_FLUSH = _library.fb_jit_runtime_flush
RUNTIME_FLUSH.argtypes = (ctypes.c_void_p,)
RUNTIME_FLUSH.restype = ctypes.c_int
RUNTIME_YIELD = _library.fb_jit_runtime_yield
RUNTIME_YIELD.argtypes = (ctypes.c_void_p,)
RUNTIME_YIELD.restype = ctypes.c_int
RUNTIME_COMPILE = _library.fb_jit_runtime_compile
RUNTIME_COMPILE.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
RUNTIME_COMPILE.restype = ctypes.c_int
RUNTIME_RESET_COUNTS = _library.fb_jit_runtime_reset_counts
RUNTIME_RESET_COUNTS.argtypes = (ctypes.c_void_p,)
RUNTIME_RESET_COUNTS.restype = None
