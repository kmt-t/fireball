"""Lifetime and execution adapter for an opaque native plugin."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.interpreter import NativeModuleExecution
from tier2_runtime.wasm.module import Module

from . import native_abi


class JITRuntimeManager:
    """Own native storage and borrow the runtime's module execution view."""

    __slots__ = (
        "_execution",
        "_lease",
        "_pointer",
        "_region_provider",
        "module",
        "module_id",
    )

    def __init__(
        self,
        region_provider: Callable[[int, int], memoryview],
        module_id: int = 0,
    ) -> None:
        assert 0 <= module_id <= 0xFFFFFFFF
        self.module_id = module_id
        self._region_provider = region_provider
        self._pointer: int | None = None
        self._lease: BufferLease | None = None
        self._execution: NativeModuleExecution | None = None
        self.module: Module | None = None

    def register_module(self, module: Module) -> None:
        if self.module is module:
            return
        self.close()
        self.module = module

    def bind_execution(self, execution: NativeModuleExecution) -> int:
        assert execution is not None and execution.module is self.module
        if self._pointer is not None:
            return self._pointer
        assert self._lease is None and self._execution is None
        view = ctypes.byref(execution._module_view)
        size = native_abi.REQUIRED_BYTES(view)
        region = self._region_provider(size, native_abi.REGION_ALIGNMENT())
        assert not region.readonly and region.c_contiguous, (
            "plugin region must be writable and contiguous"
        )
        assert region.nbytes == size, "plugin region size must match its request"
        lease = BufferLease(region)
        pointer = native_abi.RUNTIME_INIT(lease.address, region.nbytes, view, self.module_id)
        assert pointer is not None
        self._lease = lease
        self._pointer = pointer
        self._execution = execution
        return pointer

    native_entry = native_abi.NATIVE_DISPATCH

    def idle_hook(self, budget: int = 4) -> int:
        assert 0 <= budget <= 0x7FFF_FFFF
        if self._pointer is None:
            return 0
        result = int(native_abi.RUNTIME_COMPILE(self._pointer, budget))
        assert result >= 0
        return result

    def flush_all(self) -> None:
        if self._pointer is None:
            return
        result = native_abi.RUNTIME_FLUSH(self._pointer)
        assert result == 1

    def close(self) -> None:
        if self._pointer is not None:
            native_abi.RUNTIME_CLOSE(self._pointer)
            self._pointer = None
        if self._lease is not None:
            self._lease.release()
            self._lease = None
        self._execution = None

    def __del__(self) -> None:
        self.close()
