"""ABI adapter for Tier 3 C++ common-code generation and relocation."""

from __future__ import annotations

import ctypes
from typing import Protocol, cast

from . import native_abi
from .exec_memory import ExecutableBuffer

_LAYOUT = native_abi.COMMON_LAYOUT
COMMON_PROLOGUE_OFFSET = _LAYOUT.prologue_offset
COMMON_EPILOGUE_OFFSET = _LAYOUT.epilogue_offset
COMMON_HELPER_OFFSET = _LAYOUT.context_helper_offset
COMMON_ABSOLUTE_POOL_OFFSET = _LAYOUT.absolute_pool_offset
COMMON_CHAIN_DISPATCH_OFFSET = _LAYOUT.chain_dispatcher_offset
TRACE_ENTRY_STUB_BYTES = _LAYOUT.entry_stub_bytes
TRACE_BODY_OFFSET = _LAYOUT.header_bytes + _LAYOUT.entry_stub_bytes


class NativeTraceFunction(Protocol):
    """Fixed four-argument trace ABI returned by the C++ relocator."""

    def __call__(
        self, ctx: ctypes.c_void_p, sp: ctypes.c_void_p, local_base: ctypes.c_void_p, tos: int
    ) -> int | None: ...


class JITCodeCacheRegion:
    """Retain the platform buffer used by the Tier 3 C++ common-code implementation."""

    __slots__ = (
        "absolute_pool_offset",
        "absolute_pool_size",
        "buffer",
        "chain_dispatcher_offset",
        "chain_dispatcher_size",
        "common_code_bytes",
        "epilogue_offset",
        "epilogue_size",
        "helper_offset",
        "helper_size",
        "prologue_offset",
        "prologue_size",
        "region_bytes",
    )

    def __init__(self, buffer: ExecutableBuffer | None = None) -> None:
        self.region_bytes = _LAYOUT.region_bytes
        self.common_code_bytes = _LAYOUT.common_code_bytes
        self.prologue_offset = _LAYOUT.prologue_offset
        self.prologue_size = _LAYOUT.prologue_size
        self.epilogue_offset = _LAYOUT.epilogue_offset
        self.epilogue_size = _LAYOUT.epilogue_size
        self.helper_offset = _LAYOUT.helper_offset
        self.helper_size = _LAYOUT.helper_size
        self.chain_dispatcher_offset = _LAYOUT.chain_dispatcher_offset
        self.chain_dispatcher_size = _LAYOUT.chain_dispatcher_size
        self.absolute_pool_offset = _LAYOUT.absolute_pool_offset
        self.absolute_pool_size = _LAYOUT.absolute_pool_size
        self.buffer = buffer if buffer is not None else ExecutableBuffer.region()

    def install_trace(
        self,
        offset: int,
        blob: bytes,
        entry_body_patch_offset: int,
        entry_prologue_patch_offset: int,
        exit_patch_offset: int,
        helper_header_patch_offset: int,
        helper_exit_patch_offset: int,
        chain_dispatch_patch_offset: int = -1,
        common_helper_offset: int = COMMON_HELPER_OFFSET,
    ) -> tuple[NativeTraceFunction, int]:
        fixups = native_abi.NativeTraceFixups(
            entry_body_patch_offset,
            entry_prologue_patch_offset,
            exit_patch_offset,
            helper_header_patch_offset,
            helper_exit_patch_offset,
            chain_dispatch_patch_offset,
            common_helper_offset,
        )
        assert self.buffer.base is not None
        entry = native_abi.install_common_trace(
            self.buffer._native,
            offset,
            blob,
            fixups,
        )
        fn = cast(
            NativeTraceFunction,
            ctypes.CFUNCTYPE(
                None,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
            )(entry),
        )
        return fn, entry

    def read_common(self) -> bytes:
        return self.buffer.read(0, self.common_code_bytes)


__all__ = (
    "COMMON_ABSOLUTE_POOL_OFFSET",
    "COMMON_CHAIN_DISPATCH_OFFSET",
    "COMMON_EPILOGUE_OFFSET",
    "COMMON_HELPER_OFFSET",
    "COMMON_PROLOGUE_OFFSET",
    "TRACE_BODY_OFFSET",
    "TRACE_ENTRY_STUB_BYTES",
    "JITCodeCacheRegion",
)
