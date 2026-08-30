"""
experiments/pysim/exec_memory.py
Cross-platform executable memory with strict W^X (Write XOR Execute) lifecycle protection.
Supports Windows (VirtualAlloc/VirtualProtect/VirtualFree) and Linux/POSIX (mmap/mprotect/munmap).
Conforms strictly to docs/components/tier3_platform/platform_memory.md §9.2 and {LowLatencyJIT}.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes

# Platform detection

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
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
    _kernel32.VirtualAlloc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        wt.DWORD,
        wt.DWORD,
    ]
    _kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
    _kernel32.VirtualProtect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
    ]

else:
    # Linux / POSIX
    PROT_NONE = 0x0
    PROT_READ = 0x1
    PROT_WRITE = 0x2
    PROT_EXEC = 0x4
    MAP_PRIVATE = 0x02
    MAP_ANONYMOUS = 0x20 if sys.platform.startswith("linux") else 0x1000
    _libc = ctypes.CDLL(None)
    _libc.mmap.restype = ctypes.c_void_p
    _libc.mmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int64,
    ]
    _libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    _libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]


class ExecutableBuffer:
    """
    Owns executable memory with strict W^X (Write XOR Execute) lifecycle protection.
        Adheres to platform_memory.md §9.2 and {LowLatencyJIT}:
        - begin_jit_patch(): flips protection from RO+X to RW+XN (PAGE_READWRITE / PROT_READ|PROT_WRITE)
        - commit_jit_patch(): flips protection from RW+XN back to RO+X (PAGE_EXECUTE_READ / PROT_READ|PROT_EXEC)
        - assert_no_rwx(): verifies no state ever permits both write and execution.
    """

    def __init__(self, size: int):

        self.size = size
        self.patch_in_progress = True
        self.dsb_count = 0
        self.isb_count = 0
        if IS_WINDOWS:
            # Initial state: RW+XN (PAGE_READWRITE) for initial configuration
            addr = _kernel32.VirtualAlloc(
                None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
            )
            if not addr:
                raise MemoryError("VirtualAlloc failed to allocate executable memory")

            self.base = addr
            self.current_protection = PAGE_READWRITE

        else:
            # Initial state: PROT_READ | PROT_WRITE
            addr = _libc.mmap(
                None, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0
            )
            if addr is None or addr == -1 or addr == 0xFFFFFFFFFFFFFFFF:
                raise MemoryError("mmap failed to allocate executable memory")

            self.base = addr
            self.current_protection = PROT_READ | PROT_WRITE

    def begin_jit_patch(self) -> None:
        """Switch buffer from RO+X to RW+XN for patching."""
        assert not self.patch_in_progress, "Nested begin_jit_patch is invalid"
        if IS_WINDOWS:
            old = wt.DWORD()
            res = _kernel32.VirtualProtect(
                self.base, self.size, PAGE_READWRITE, ctypes.byref(old)
            )
            assert res != 0, "VirtualProtect to PAGE_READWRITE failed"
            self.current_protection = PAGE_READWRITE

        else:
            res = _libc.mprotect(self.base, self.size, PROT_READ | PROT_WRITE)
            assert res == 0, (
                f"mprotect to PROT_READ|PROT_WRITE failed (errno={ctypes.get_errno()})"
            )
            self.current_protection = PROT_READ | PROT_WRITE

        self.patch_in_progress = True
        self.dsb_count += 1
        self.isb_count += 1

    def commit_jit_patch(self) -> None:
        """Switch buffer from RW+XN to RO+X for execution."""
        assert self.patch_in_progress, "Cannot commit without begin_jit_patch"
        if IS_WINDOWS:
            old = wt.DWORD()
            res = _kernel32.VirtualProtect(
                self.base, self.size, PAGE_EXECUTE_READ, ctypes.byref(old)
            )
            assert res != 0, "VirtualProtect to PAGE_EXECUTE_READ failed"
            self.current_protection = PAGE_EXECUTE_READ

        else:
            res = _libc.mprotect(self.base, self.size, PROT_READ | PROT_EXEC)
            assert res == 0, (
                f"mprotect to PROT_READ|PROT_EXEC failed (errno={ctypes.get_errno()})"
            )
            self.current_protection = PROT_READ | PROT_EXEC

        self.patch_in_progress = False
        self.dsb_count += 1
        self.isb_count += 1

    def assert_no_rwx(self) -> None:
        """Strictly verify that buffer is never in RWX state."""
        if IS_WINDOWS:
            assert self.current_protection != PAGE_EXECUTE_READWRITE, (
                "Invariant violation: Buffer is in RWX state"
            )
            assert self.current_protection in (
                PAGE_READWRITE,
                PAGE_EXECUTE_READ,
                PAGE_READONLY,
            )

        else:
            assert self.current_protection != (PROT_READ | PROT_WRITE | PROT_EXEC), (
                "Invariant violation: Buffer is in RWX state"
            )
            assert self.current_protection in (
                PROT_READ | PROT_WRITE,
                PROT_READ | PROT_EXEC,
                PROT_READ,
            )

    def write(self, offset: int, data: bytes) -> None:

        assert self.patch_in_progress, (
            "Cannot write to ExecutableBuffer outside begin_jit_patch() transaction"
        )
        assert offset + len(data) <= self.size, (
            "write past the end of the executable buffer"
        )
        ctypes.memmove(self.base + offset, data, len(data))

    def read(self, offset: int, size: int) -> bytes:

        assert offset + size <= self.size, "read past the end of the executable buffer"
        buf = (ctypes.c_char * size)()
        ctypes.memmove(buf, self.base + offset, size)
        return bytes(buf)

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

        if getattr(self, "base", None):
            if IS_WINDOWS:
                _kernel32.VirtualFree(self.base, 0, MEM_RELEASE)

            else:
                _libc.munmap(self.base, self.size)

            self.base = None

    def __del__(self) -> None:

        self.close()
