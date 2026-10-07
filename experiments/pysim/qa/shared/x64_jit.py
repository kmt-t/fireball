"""Python binding for the native x64 Copy-and-Patch trace compiler."""

from __future__ import annotations

from collections.abc import Iterable

from config import JIT_CACHE_ACTIVE_OFFSET_BYTES, JIT_X64_TRACE_HEADER_BYTES
from qa.private import jit_native_abi as native_abi
from tier2_runtime.wasm.module import LocalLayout, WasmOperand

from .common_code import JITCodeCacheRegion
from .jit_cache import JITTrace


class TraceCompiler:
    """Build/install native trace bodies for the upper runtime.

    Explicit callers pass loader-owned WASM bytes in one call. The native
    JitRuntime compiles its registered blocks directly in C++.
    ``compile_instructions`` also installs a standalone trace.
    """

    __slots__ = ("_standalone_region",)

    def __init__(self) -> None:
        self._standalone_region: JITCodeCacheRegion | None = None

    @staticmethod
    def compile_wasm(block: native_abi.WasmBlock) -> JITTrace | None:
        """Compile loader-owned WASM bytes into a cache-ready native trace."""
        trace = JITTrace(head_pc=block.head_pc)
        blob = native_abi.compile_wasm(block, trace._native)
        if blob is None:
            return None
        TraceCompiler._attach_code(trace, blob)
        return trace

    def compile_instructions(
        self,
        *,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_length: int,
        local_layout: LocalLayout,
        context_helper: bool = False,
        helper_address: int = 0,
    ) -> JITTrace | None:
        """Compile one loader-owned block and install its standalone trace."""

        trace = JITTrace(
            head_pc=head_pc,
            next_pc=next_pc,
            loops_to=loops_to,
            helper_target_addr=helper_address,
        )
        blob = native_abi.compile_instructions(
            trace._native, instructions, byte_length, local_layout, context_helper, helper_address
        )
        if blob is None:
            return None
        self._attach_code(trace, blob)
        assert trace.code_blob is not None
        if self._standalone_region is None:
            self._standalone_region = JITCodeCacheRegion()
        assert (
            JIT_CACHE_ACTIVE_OFFSET_BYTES + trace.size_bytes <= self._standalone_region.region_bytes
        )
        trace.code_offset = JIT_CACHE_ACTIVE_OFFSET_BYTES
        fn, raw_addr = self._standalone_region.install_trace(
            trace.code_offset,
            trace.code_blob,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self._standalone_region.buffer
        trace.header._native = native_abi.NativeTraceHeader.from_address(
            raw_addr - JIT_X64_TRACE_HEADER_BYTES
        )
        return trace

    @staticmethod
    def _attach_code(trace: JITTrace, blob: bytes) -> None:
        """Retain a cache-ready blob; relocation state is carried by its physical header."""
        trace.code_blob = blob
        header = native_abi.NativeTraceHeader.from_buffer_copy(blob[:JIT_X64_TRACE_HEADER_BYTES])
        trace.size_bytes = header.blob_bytes
        trace.stack_words = header.stack_words
        trace.result_words = header.result_words
        trace.has_return_val = bool(header.has_return_value)
        trace.header._native = header
        trace.bind_code()
