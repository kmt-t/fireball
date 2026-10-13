"""Python lifetime/ABI adapter for Tier 3 C++ executable-memory ownership."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from typing import Protocol

from qa.private import jit_native_abi as native_abi


class NativeDynamicFunction(Protocol):
    """Machine-code entry whose argument vector is described by its ctypes ABI."""

    def __call__(self, *args: int) -> int | None: ...


class ExecutableBuffer:
    """Retain the native mapping descriptor; C++ owns permissions and bounds."""

    __slots__ = ("_native",)

    def __init__(self, size: int):
        self._native = native_abi.NativeExecutableMemory()
        assert 0 < size <= 0xFFFF_FFFF
        status = native_abi.MEMORY_INIT(ctypes.byref(self._native), size)
        assert status == 1

    @classmethod
    def region(cls) -> ExecutableBuffer:
        buffer = cls.__new__(cls)
        buffer._native = native_abi.NativeExecutableMemory()
        status = native_abi.REGION_INIT(ctypes.byref(buffer._native))
        assert status == 1
        return buffer

    @property
    def base(self) -> int | None:
        return self._native.base or None

    @property
    def size(self) -> int:
        return self._native.size

    def begin_jit_patch(self) -> None:
        assert self._native.base != 0 and self._native.patching == 0, "Invalid begin_jit_patch"
        status = native_abi.MEMORY_BEGIN(ctypes.byref(self._native))
        assert status == 1

    def commit_jit_patch(self) -> None:
        assert self._native.base != 0 and self._native.patching == 1, "Invalid commit_jit_patch"
        status = native_abi.MEMORY_COMMIT(ctypes.byref(self._native))
        assert status == 1

    def write(self, offset: int, data: bytes) -> None:
        assert (
            self._native.base != 0
            and self._native.patching == 1
            and 0 <= offset <= self.size
            and len(data) <= self.size - offset
        ), "Cannot write to ExecutableBuffer outside a valid patch transaction and bounds"
        source = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        status = native_abi.MEMORY_WRITE(ctypes.byref(self._native), offset, source, len(data))
        assert status == 1

    def finalize(self) -> None:
        assert self._native.base != 0
        status = native_abi.MEMORY_FINALIZE(ctypes.byref(self._native))
        assert status == 1

    def function_at(
        self, offset: int, restype: type | None, argtypes: Sequence[type]
    ) -> NativeDynamicFunction:
        address = self.address_of(offset)
        return ctypes.CFUNCTYPE(restype, *argtypes)(address)

    def address_of(self, offset: int) -> int:
        assert self._native.base != 0 and 0 <= offset < self.size
        address = int(native_abi.MEMORY_ADDRESS(ctypes.byref(self._native), offset))
        assert address != 0
        return address

    def close(self) -> None:
        status = native_abi.MEMORY_CLOSE(ctypes.byref(self._native))
        assert status == 1

    def __del__(self) -> None:
        self.close()
