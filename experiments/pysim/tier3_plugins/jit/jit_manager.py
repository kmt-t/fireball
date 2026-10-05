"""Lifetime and execution adapter for an opaque native plugin."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from bump_allocator import BumpAllocator
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
        "_scratch_allocator",
        "_scratch_bytes",
        "_scratch_locations",
        "_scratch_offset",
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
        self._scratch_allocator: BumpAllocator | None = None
        self._scratch_offset: int | None = None
        self._scratch_bytes: ctypes.Array | None = None
        self._scratch_locations: ctypes.Array | None = None
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
        allocator = execution.bump_allocator
        assert allocator is not None, "native module execution must own a runtime arena"
        scratch_offset = allocator.acquire(native_abi.COMPILE_SCRATCH_BYTES, alignment=8)
        self._scratch_allocator = allocator
        self._scratch_offset = scratch_offset
        try:
            size = native_abi.REQUIRED_BYTES(view)
            region = self._region_provider(size, native_abi.REGION_ALIGNMENT())
            assert not region.readonly and region.c_contiguous, (
                "plugin region must be writable and contiguous"
            )
            assert region.nbytes == size, "plugin region size must match its request"
            self._lease = BufferLease(region)
            scratch_bytes = (
                ctypes.c_uint64
                * (native_abi.COMPILE_BYTE_STORAGE_BYTES // ctypes.sizeof(ctypes.c_uint64))
            )()
            scratch_locations = (ctypes.c_int16 * native_abi.COMPILE_MAX_STACK_DEPTH)()
            self._scratch_bytes = scratch_bytes
            self._scratch_locations = scratch_locations
            pointer = native_abi.RUNTIME_INIT(
                self._lease.address,
                region.nbytes,
                view,
                1,
                self.module_id,
                ctypes.cast(dispatcher, ctypes.c_void_p),
                ctypes.cast(scratch_bytes, ctypes.POINTER(ctypes.c_uint8)),
                native_abi.COMPILE_BYTE_STORAGE_BYTES,
                scratch_locations,
                native_abi.COMPILE_MAX_STACK_DEPTH,
            )
            assert pointer is not None
        except Exception:
            self.close()
            raise
        self._pointer = pointer
        self._execution = execution
        return self._pointer

    native_entry = native_abi.RUNTIME_RUN

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
        if self._scratch_allocator is not None and self._scratch_offset is not None:
            self._scratch_allocator.release(
                self._scratch_offset, native_abi.COMPILE_SCRATCH_BYTES, alignment=8
            )
        self._scratch_allocator = None
        self._scratch_offset = None
        self._scratch_bytes = None
        self._scratch_locations = None
        self._execution = None

    def __del__(self) -> None:
        self.close()
