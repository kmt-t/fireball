"""
experiments/pysim/exec_memory.py

Real executable memory, via the actual OS API (Win32 VirtualAlloc), not a
`bytes` object pretending to be code. This is the one place the "compile
Python bytes, then jump the CPU into them" boundary actually exists.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40

_kernel32 = ctypes.windll.kernel32
_kernel32.VirtualAlloc.restype = ctypes.c_void_p
_kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
_kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]


class ExecutableBuffer:
    """Owns one RWX region. Real JITs split this into "write, then flip to
    execute-only" (W^X) -- {GLOBAL_Policy_Memory}-adjacent, and
    docs/components/tier3_jit/platform_memory.md's actual MPU model does
    exactly that on the real target. This experiment keeps the page RWX
    throughout for simplicity, since the point here is proving the codegen
    correct, not re-deriving the W^X transition logic already covered by
    that design and its formal model.
    """

    def __init__(self, size: int):
        self.size = size
        addr = _kernel32.VirtualAlloc(None, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
        if not addr:
            raise MemoryError("VirtualAlloc failed to reserve executable memory")
        self.base = addr

    def write(self, offset: int, data: bytes) -> None:
        assert offset + len(data) <= self.size, "write past the end of the executable buffer"
        ctypes.memmove(self.base + offset, data, len(data))

    def function_at(self, offset: int, restype, argtypes):
        func_type = ctypes.CFUNCTYPE(restype, *argtypes)
        return func_type(self.base + offset)

    def address_of(self, offset: int) -> int:
        return self.base + offset

    def close(self) -> None:
        if self.base:
            _kernel32.VirtualFree(self.base, 0, MEM_RELEASE)
            self.base = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
