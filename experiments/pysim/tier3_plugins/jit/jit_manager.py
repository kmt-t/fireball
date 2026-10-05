"""Lifetime and execution adapter for an opaque native plugin."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from config import FB_CONF_RUNTIME_YIELD_THRESHOLD
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.interpreter import NativeDispatchEntryPoint, NativeModuleExecution
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
        "yield_threshold",
    )

    def __init__(
        self,
        region_provider: Callable[[int, int], memoryview],
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        module_id: int = 0,
    ) -> None:
        assert 1 <= yield_threshold <= 0xFFFFFFFF
        assert 0 <= module_id <= 0xFFFFFFFF
        self.yield_threshold = yield_threshold
        self.module_id = module_id
        self._region_provider = region_provider
        self._pointer: int | None = None
        self._lease: BufferLease | None = None
        self._execution: NativeModuleExecution | None = None
        self.module: Module | None = None

    def register_module(self, module: Module) -> None:
        self.close()
        self.module = module

    def bind_execution(
        self, dispatcher: NativeDispatchEntryPoint, execution: NativeModuleExecution
    ) -> int:
        assert execution is not None and execution.module is self.module
        if self._pointer is not None:
            return self._pointer
        view = ctypes.byref(execution._module_view)
        self.close()
        size = native_abi.REQUIRED_BYTES(view)
        region = self._region_provider(size, native_abi.REGION_ALIGNMENT())
        assert not region.readonly and region.c_contiguous, (
            "plugin region must be writable and contiguous"
        )
        assert region.nbytes == size, "plugin region size must match its request"
        self._lease = BufferLease(region)
        self._pointer = native_abi.RUNTIME_INIT(
            self._lease.address,
            region.nbytes,
            view,
            1,
            self.module_id,
            ctypes.cast(dispatcher, ctypes.c_void_p),
        )
        assert self._pointer is not None
        self._execution = execution
        return self._pointer

    native_entry = native_abi.RUNTIME_RUN

    def on_yield(self) -> None:
        if self._pointer is None:
            return
        result = native_abi.RUNTIME_YIELD(self._pointer)
        assert result == 1

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

    def reset_stats(self) -> None:
        if self._pointer is not None:
            native_abi.RUNTIME_RESET_COUNTS(self._pointer)

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
