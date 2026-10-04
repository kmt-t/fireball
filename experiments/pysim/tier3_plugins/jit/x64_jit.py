"""Python binding for the native x64 Copy-and-Patch trace compiler."""

from __future__ import annotations

from collections.abc import Iterable

from config import JIT_CACHE_ACTIVE_OFFSET_BYTES, JIT_X64_TRACE_HEADER_BYTES
from tier2_runtime.wasm.module import LocalWidthMap, WasmOperand

from . import native_abi
from .common_code import JITCodeCacheRegion, helper_entry_offset
from .jit_cache import JITTrace, JITTraceHeader


class TraceCompiler:
    """Build/install native trace bodies for the upper runtime.

    The Tier 3 runtime manager passes loader-owned WASM bytes to the native
    compiler in one call. ``compile_trace`` remains the standalone compiler
    entry used by explicit compiler callers.
    """

    __slots__ = ("_standalone_region",)

    def __init__(self) -> None:
        self._standalone_region = JITCodeCacheRegion()

    @staticmethod
    def compile_wasm_trace(
        code_address: int,
        code_bytes: int,
        code_offset: int,
        byte_span: int,
        next_pc: int | None,
        loops_to: int | None,
        local_width_address: int,
        local_width_bytes: int,
        local_count: int,
        slot_words: int,
    ) -> tuple[bytes, int, int, int, int, int, int, int, int] | None:
        """Decode and compile one block directly from loader-owned WASM bytes."""

        return native_abi.compile_wasm_trace(
            code_address,
            code_bytes,
            code_offset,
            byte_span,
            next_pc,
            loops_to,
            local_width_address,
            local_width_bytes,
            local_count,
            slot_words,
        )

    def compile_trace(
        self,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_widths: LocalWidthMap,
        *,
        tail_context_helper: bool = False,
        helper_target_addr: int = 0,
    ) -> JITTrace | None:
        """Compile one loader-owned block and install its standalone trace."""

        native_result = native_abi.compile_trace(
            instructions,
            next_pc,
            loops_to,
            byte_span,
            local_widths,
            tail_context_helper=tail_context_helper,
            helper_target_addr=helper_target_addr,
        )
        if native_result is None:
            return None
        trace = self.build_runtime_trace(
            head_pc,
            native_result,
            next_pc,
            loops_to,
            helper_target_addr=helper_target_addr,
        )
        assert trace is not None
        assert (
            JIT_CACHE_ACTIVE_OFFSET_BYTES + trace.size_bytes <= self._standalone_region.region_bytes
        )
        trace.code_offset = JIT_CACHE_ACTIVE_OFFSET_BYTES
        fn, raw_addr = self._standalone_region.install_trace(
            trace.code_offset,
            trace.code_blob,
            trace.entry_body_patch_offset,
            trace.entry_prologue_patch_offset,
            trace.exit_patch_offset,
            trace.helper_header_patch_offset,
            trace.helper_exit_patch_offset,
            trace.chain_dispatch_patch_offset,
            trace.header.common_helper_offset,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self._standalone_region.buffer
        return trace

    @staticmethod
    def build_runtime_trace(
        head_pc: int,
        native_result: tuple[bytes, int, int, int, int, int, int, int, int],
        next_pc: int | None,
        loops_to: int | None,
        *,
        helper_target_addr: int = 0,
    ) -> JITTrace | None:
        """Create a cache-ready trace descriptor from a C++ compiler result."""

        (
            native_body,
            helper_index,
            helper_words,
            max_spilled_words,
            stack_location_count,
            helper_header_patch_offset,
            helper_exit_patch_offset,
            exit_patch_offset,
            chain_dispatch_patch_offset,
        ) = native_result
        header = JITTraceHeader(head_wasm_pc=head_pc)
        if helper_index >= 0:
            header.common_helper_offset = helper_entry_offset(helper_index)
        header.helper_target_addr = helper_target_addr
        total_size = JIT_X64_TRACE_HEADER_BYTES + len(native_body)
        header.trace_byte_size = total_size
        full_blob = bytearray(header.pack()) + native_body
        result_words = (
            2 if helper_index >= 0 and (helper_index <= 2 or 7 <= helper_index <= 10) else 1
        )
        trace = JITTrace(
            head_pc=head_pc,
            size_bytes=total_size,
            next_pc=next_pc,
            loops_to=loops_to,
            has_return_val=stack_location_count != 0 or helper_index >= 0,
            result_words=result_words,
            stack_words=max(max_spilled_words, helper_words, result_words),
            code_blob=bytes(full_blob),
            entry_body_patch_offset=JIT_X64_TRACE_HEADER_BYTES + 2,
            entry_prologue_patch_offset=JIT_X64_TRACE_HEADER_BYTES + 11,
            exit_patch_offset=exit_patch_offset,
            helper_header_patch_offset=helper_header_patch_offset,
            helper_exit_patch_offset=helper_exit_patch_offset,
            chain_dispatch_patch_offset=chain_dispatch_patch_offset,
            helper_target_addr=helper_target_addr,
        )
        trace.header = header
        return trace
