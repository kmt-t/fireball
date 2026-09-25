"""uvwasi-backed WASI Preview 1 driver.

The Fireball platform owns only its console/logging extension.  All other
Preview 1 operations are routed through this driver.  The native path calls
the uvwasi C API through a small ctypes surface; when the native library is
not configured, the explicit unavailable backend returns ``ENOSYS`` instead
of silently reintroducing a second fake WASI implementation.
"""

from __future__ import annotations

import ctypes
import os
import struct
from typing import Protocol

from hostcall import WasiErrno

UVWASI_MAX_IOVECS = 64
UVWASI_CLOCK_REALTIME = 0
UVWASI_CLOCK_MONOTONIC = 1


class WasiPreview1Backend(Protocol):
    """Backend contract for all non-Fireball Preview 1 operations."""

    __slots__ = ()

    def fd_read(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        """Read guest iovecs from uvwasi."""

    def fd_write(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        """Write guest iovecs through uvwasi."""

    def fd_close(self, fd: int) -> int:
        """Close one uvwasi descriptor."""

    def clock_time_get(self, clock_id: int, precision: int, memory: bytearray, time_ptr: int) -> int:
        """Write a uvwasi clock value into guest memory."""

    def random_get(self, memory: bytearray, buf_ptr: int, buf_len: int) -> int:
        """Fill guest memory with uvwasi randomness."""

    def close(self) -> None:
        """Release the uvwasi embedder context."""


class UnavailableUvwasiBackend:
    """Fail-closed backend used until the uvwasi native library is configured."""

    __slots__ = ()

    def fd_read(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        return int(WasiErrno.NOSYS)

    def fd_write(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        return int(WasiErrno.NOSYS)

    def fd_close(self, fd: int) -> int:
        return int(WasiErrno.NOSYS)

    def clock_time_get(self, clock_id: int, precision: int, memory: bytearray, time_ptr: int) -> int:
        return int(WasiErrno.NOSYS)

    def random_get(self, memory: bytearray, buf_ptr: int, buf_len: int) -> int:
        return int(WasiErrno.NOSYS)

    def close(self) -> None:
        """The unavailable backend owns no native context."""


class _UvwasiIovec(ctypes.Structure):
    _fields_ = (
        ("buf", ctypes.c_void_p),
        ("buf_len", ctypes.c_uint32),
    )


class _UvwasiContext(ctypes.Structure):
    _fields_ = (
        ("fds", ctypes.c_void_p),
        ("argc", ctypes.c_uint32),
        ("argv", ctypes.c_void_p),
        ("argv_buf", ctypes.c_void_p),
        ("argv_buf_size", ctypes.c_uint32),
        ("envc", ctypes.c_uint32),
        ("env", ctypes.c_void_p),
        ("env_buf", ctypes.c_void_p),
        ("env_buf_size", ctypes.c_uint32),
        ("allocator", ctypes.c_void_p),
        ("loop", ctypes.c_void_p),
    )


class _UvwasiOptions(ctypes.Structure):
    _fields_ = (
        ("fd_table_size", ctypes.c_uint32),
        ("preopenc", ctypes.c_uint32),
        ("preopens", ctypes.c_void_p),
        ("preopen_socketc", ctypes.c_uint32),
        ("preopen_sockets", ctypes.c_void_p),
        ("argc", ctypes.c_uint32),
        ("argv", ctypes.c_void_p),
        ("envp", ctypes.c_void_p),
        ("in_fd", ctypes.c_uint32),
        ("out_fd", ctypes.c_uint32),
        ("err_fd", ctypes.c_uint32),
        ("allocator", ctypes.c_void_p),
    )


class UvwasiBackend:
    """ctypes adapter for the uvwasi embedder and Preview 1 APIs."""

    __slots__ = ("_closed", "_context", "_library", "_memory_array")

    def __init__(self, library_path: str) -> None:
        assert library_path != ""
        self._library = ctypes.CDLL(library_path)
        self._configure_signatures()
        self._context = _UvwasiContext()
        options = _UvwasiOptions()
        self._library.uvwasi_options_init(ctypes.byref(options))
        options.fd_table_size = 64
        options.in_fd = 0
        options.out_fd = 1
        options.err_fd = 2
        status = int(self._library.uvwasi_init(ctypes.byref(self._context), ctypes.byref(options)))
        assert status == int(WasiErrno.SUCCESS), f"uvwasi_init failed: {status}"
        self._memory_array: ctypes.Array[ctypes.c_ubyte] | None = None
        self._closed = False

    @classmethod
    def from_environment(cls) -> "UvwasiBackend":
        """Load the native library named by ``FIREBALL_UVWASI_LIBRARY``."""
        library_path = os.environ.get("FIREBALL_UVWASI_LIBRARY")
        assert library_path is not None, "FIREBALL_UVWASI_LIBRARY is not configured"
        return cls(library_path)

    def _configure_signatures(self) -> None:
        library = self._library
        library.uvwasi_init.argtypes = (
            ctypes.POINTER(_UvwasiContext),
            ctypes.POINTER(_UvwasiOptions),
        )
        library.uvwasi_init.restype = ctypes.c_uint16
        library.uvwasi_options_init.argtypes = (ctypes.POINTER(_UvwasiOptions),)
        library.uvwasi_options_init.restype = None
        library.uvwasi_destroy.argtypes = (ctypes.POINTER(_UvwasiContext),)
        library.uvwasi_destroy.restype = None
        library.uvwasi_fd_read.argtypes = (
            ctypes.POINTER(_UvwasiContext),
            ctypes.c_uint32,
            ctypes.POINTER(_UvwasiIovec),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        library.uvwasi_fd_read.restype = ctypes.c_uint16
        library.uvwasi_fd_write.argtypes = (
            ctypes.POINTER(_UvwasiContext),
            ctypes.c_uint32,
            ctypes.POINTER(_UvwasiIovec),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        library.uvwasi_fd_write.restype = ctypes.c_uint16
        library.uvwasi_fd_close.argtypes = (ctypes.POINTER(_UvwasiContext), ctypes.c_uint32)
        library.uvwasi_fd_close.restype = ctypes.c_uint16
        library.uvwasi_clock_time_get.argtypes = (
            ctypes.POINTER(_UvwasiContext),
            ctypes.c_uint32,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )
        library.uvwasi_clock_time_get.restype = ctypes.c_uint16
        library.uvwasi_random_get.argtypes = (
            ctypes.POINTER(_UvwasiContext),
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        library.uvwasi_random_get.restype = ctypes.c_uint16

    def _memory_view(self, memory: bytearray) -> ctypes.Array[ctypes.c_ubyte]:
        self._memory_array = (ctypes.c_ubyte * len(memory)).from_buffer(memory)
        return self._memory_array

    @staticmethod
    def _valid_range(memory: bytearray, offset: int, length: int) -> bool:
        return offset >= 0 and length >= 0 and offset <= len(memory) and length <= len(memory) - offset

    def _iovecs(
        self,
        memory: bytearray,
        iovs_ptr: int,
        iovs_len: int,
    ) -> tuple[ctypes.Array[_UvwasiIovec] | None, ctypes.Array[ctypes.c_ubyte] | None, int]:
        if iovs_len < 0 or iovs_len > UVWASI_MAX_IOVECS or not self._valid_range(memory, iovs_ptr, iovs_len * 8):
            return None, None, int(WasiErrno.FAULT)
        memory_array = self._memory_view(memory)
        iovecs = (_UvwasiIovec * UVWASI_MAX_IOVECS)()
        for index in range(iovs_len):
            base, length = struct.unpack_from("<II", memory, iovs_ptr + index * 8)
            if not self._valid_range(memory, base, length):
                return None, None, int(WasiErrno.FAULT)
            iovecs[index].buf = ctypes.cast(ctypes.byref(memory_array, base), ctypes.c_void_p)
            iovecs[index].buf_len = length
        return iovecs, memory_array, int(WasiErrno.SUCCESS)

    def fd_read(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        if not self._valid_range(memory, nread_ptr, 4):
            return int(WasiErrno.FAULT)
        iovecs, memory_array, status = self._iovecs(memory, iovs_ptr, iovs_len)
        if status != int(WasiErrno.SUCCESS) or iovecs is None or memory_array is None:
            return status
        nread = ctypes.c_uint32(0)
        result = int(self._library.uvwasi_fd_read(ctypes.byref(self._context), fd, iovecs, iovs_len, ctypes.byref(nread)))
        if result == int(WasiErrno.SUCCESS):
            struct.pack_into("<I", memory, nread_ptr, int(nread.value))
        return result

    def fd_write(self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        if not self._valid_range(memory, nwritten_ptr, 4):
            return int(WasiErrno.FAULT)
        iovecs, memory_array, status = self._iovecs(memory, iovs_ptr, iovs_len)
        if status != int(WasiErrno.SUCCESS) or iovecs is None or memory_array is None:
            return status
        nwritten = ctypes.c_uint32(0)
        result = int(self._library.uvwasi_fd_write(ctypes.byref(self._context), fd, iovecs, iovs_len, ctypes.byref(nwritten)))
        if result == int(WasiErrno.SUCCESS):
            struct.pack_into("<I", memory, nwritten_ptr, int(nwritten.value))
        return result

    def fd_close(self, fd: int) -> int:
        return int(self._library.uvwasi_fd_close(ctypes.byref(self._context), fd))

    def clock_time_get(self, clock_id: int, precision: int, memory: bytearray, time_ptr: int) -> int:
        if not self._valid_range(memory, time_ptr, 8):
            return int(WasiErrno.FAULT)
        value = ctypes.c_uint64(0)
        result = int(
            self._library.uvwasi_clock_time_get(
                ctypes.byref(self._context), clock_id, precision, ctypes.byref(value)
            )
        )
        if result == int(WasiErrno.SUCCESS):
            struct.pack_into("<Q", memory, time_ptr, int(value.value))
        return result

    def random_get(self, memory: bytearray, buf_ptr: int, buf_len: int) -> int:
        if not self._valid_range(memory, buf_ptr, buf_len):
            return int(WasiErrno.FAULT)
        memory_array = self._memory_view(memory)
        return int(
            self._library.uvwasi_random_get(
                ctypes.byref(self._context), ctypes.byref(memory_array, buf_ptr), buf_len
            )
        )

    def close(self) -> None:
        if not self._closed:
            self._library.uvwasi_destroy(ctypes.byref(self._context))
            self._closed = True


def create_uvwasi_backend() -> WasiPreview1Backend:
    """Create uvwasi from the configured native library, fail-closed otherwise."""
    library_path = os.environ.get("FIREBALL_UVWASI_LIBRARY")
    if library_path is None:
        return UnavailableUvwasiBackend()
    return UvwasiBackend(library_path)
