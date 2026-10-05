"""Bindings for the shared native printk implementation."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

PRINTK_WRITE = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)


class NativePrintkWriter(ctypes.Structure):
    _fields_ = (("owner", ctypes.c_size_t), ("write", PRINTK_WRITE))


_library = ctypes.PyDLL(
    str(Path(__file__).with_name("printk.dll" if sys.platform == "win32" else "libprintk.so"))
)
BASE64 = _library.fb_printk_base64
BASE64.argtypes = (
    ctypes.POINTER(NativePrintkWriter),
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)
BASE64.restype = ctypes.c_int
EVENT = _library.fb_printk_event
EVENT.argtypes = (
    ctypes.POINTER(NativePrintkWriter),
    ctypes.c_void_p,
    ctypes.c_uint8,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
)
EVENT.restype = ctypes.c_size_t

U64 = _library.fb_printk_u64
U64.argtypes = (
    ctypes.POINTER(NativePrintkWriter),
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_uint64,
)
U64.restype = ctypes.c_int
