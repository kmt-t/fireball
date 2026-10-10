"""ABI adapter for Tier 3 C++ common-code generation and relocation."""

from __future__ import annotations

import ctypes
from typing import Protocol, cast

from qa.private import jit_native_abi as native_abi

from .exec_memory import ExecutableBuffer

_LAYOUT = native_abi.COMMON_LAYOUT
COMMON_CHAIN_DISPATCH_OFFSET = _LAYOUT.chain_dispatcher_offset
TRACE_BODY_OFFSET = _LAYOUT.header_bytes


class NativeTraceFunction(Protocol):
    """Four-pointer JIT entry ABI returned by the C++ relocator."""

    def __call__(
        self,
        ctx: ctypes.c_void_p,
        ip: ctypes.c_void_p,
        sp: ctypes.c_void_p,
        rel: ctypes.c_void_p,
    ) -> None: ...


class JITCodeCacheRegion:
    """Retain the platform buffer used by the Tier 3 C++ common-code implementation."""

    __slots__ = (
        "buffer",
        "chain_dispatcher_offset",
        "chain_dispatcher_size",
        "common_code_bytes",
        "region_bytes",
    )

    def __init__(self, buffer: ExecutableBuffer | None = None) -> None:
        self.region_bytes = _LAYOUT.region_bytes
        self.common_code_bytes = _LAYOUT.common_code_bytes
        self.chain_dispatcher_offset = _LAYOUT.chain_dispatcher_offset
        self.chain_dispatcher_size = _LAYOUT.chain_dispatcher_size
        self.buffer = buffer if buffer is not None else ExecutableBuffer.region()

    def install_trace(
        self,
        offset: int,
        blob: bytes,
    ) -> tuple[NativeTraceFunction, int]:
        assert self.buffer.base is not None
        entry = native_abi.install_common_trace(
            self.buffer._native,
            offset,
            blob,
        )
        fn = cast(
            NativeTraceFunction,
            ctypes.CFUNCTYPE(
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )(entry),
        )
        return fn, entry


__all__ = (
    "COMMON_CHAIN_DISPATCH_OFFSET",
    "TRACE_BODY_OFFSET",
    "JITCodeCacheRegion",
)
