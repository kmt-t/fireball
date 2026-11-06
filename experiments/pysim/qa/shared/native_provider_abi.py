"""The one Python copy of the Tier 2 ABI struct fb_native_jit_provider (tier2_runtime/abi/native_abi.hxx).

This module imports only ctypes. The composer test uses it without the JIT library, and the JIT
test checks it against the compiler's offsets (TEST-JITR-116). Function pointers are c_void_p fields.
"""

from __future__ import annotations

import ctypes


class NativeJitProvider(ctypes.Structure):
    _fields_ = (
        ("owner", ctypes.c_size_t),
        ("lookup", ctypes.c_void_p),
        ("observe_block", ctypes.c_void_p),
        ("finish_boundary", ctypes.c_void_p),
        ("trace_entry_address", ctypes.c_size_t),
    )


FIELD_NAMES = (
    "owner",
    "lookup",
    "observe_block",
    "finish_boundary",
    "trace_entry_address",
)
