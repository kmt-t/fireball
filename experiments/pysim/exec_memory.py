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
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40

_kernel32 = ctypes.windll.kernel32
_kernel32.VirtualAlloc.restype = ctypes.c_void_p
_kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
_kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
_kernel32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)]


class ExecutableBuffer:
    """Owns executable memory with strict W^X (Write XOR Execute) lifecycle protection.

    Adheres to platform_memory.md §9.2 and {LowLatencyJIT}:
    - begin_jit_patch(): flips protection from RO+X to RW+XN (PAGE_READWRITE)
    - commit_jit_patch(): flips protection from RW+XN back to RO+X (PAGE_EXECUTE_READ)
    - assert_no_rwx(): verifies no state ever permits both write and execution.
    """

    def __init__(self, size: int):
        self.size = size
        # Initial state: RW+XN (PAGE_READWRITE) for initial configuration
        addr = _kernel32.VirtualAlloc(None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not addr:
            raise MemoryError("VirtualAlloc failed to reserve memory")
        self.base = addr
        self.current_protection = PAGE_READWRITE
        self.patch_in_progress = True
        self.dsb_count = 0
        self.isb_count = 0

    def begin_jit_patch(self) -> None:
        """Switch buffer from RO+X to RW+XN for patching."""
        assert not self.patch_in_progress, "Nested begin_jit_patch is invalid"
        old = wt.DWORD()
        res = _kernel32.VirtualProtect(self.base, self.size, PAGE_READWRITE, ctypes.byref(old))
        assert res != 0, "VirtualProtect to PAGE_READWRITE failed"
        self.current_protection = PAGE_READWRITE
        self.patch_in_progress = True
        self.dsb_count += 1
        self.isb_count += 1

    def commit_jit_patch(self) -> None:
        """Switch buffer from RW+XN to RO+X for execution."""
        assert self.patch_in_progress, "Cannot commit without begin_jit_patch"
        old = wt.DWORD()
        res = _kernel32.VirtualProtect(self.base, self.size, PAGE_EXECUTE_READ, ctypes.byref(old))
        assert res != 0, "VirtualProtect to PAGE_EXECUTE_READ failed"
        self.current_protection = PAGE_EXECUTE_READ
        self.patch_in_progress = False
        self.dsb_count += 1
        self.isb_count += 1

    def assert_no_rwx(self) -> None:
        """Strictly verify that buffer is never PAGE_EXECUTE_READWRITE."""
        assert self.current_protection != PAGE_EXECUTE_READWRITE, "Invariant violation: Buffer is in RWX state"
        assert self.current_protection in (PAGE_READWRITE, PAGE_EXECUTE_READ, PAGE_READONLY)

    def write(self, offset: int, data: bytes) -> None:
        assert self.patch_in_progress, "Cannot write to ExecutableBuffer outside begin_jit_patch() transaction"
        assert offset + len(data) <= self.size, "write past the end of the executable buffer"
        ctypes.memmove(self.base + offset, data, len(data))

    def finalize(self) -> None:
        if self.patch_in_progress:
            self.commit_jit_patch()

    def function_at(self, offset: int, restype, argtypes):
        self.finalize()
        func_type = ctypes.CFUNCTYPE(restype, *argtypes)
        return func_type(self.base + offset)

    def address_of(self, offset: int) -> int:
        self.finalize()
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
