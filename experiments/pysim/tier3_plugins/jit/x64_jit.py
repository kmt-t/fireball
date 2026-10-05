"""Python binding for the native x64 Copy-and-Patch trace compiler."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from config import JIT_CACHE_ACTIVE_OFFSET_BYTES
from tier2_runtime.wasm.module import LocalWidthMap, WasmOperand

from . import native_abi
from .common_code import JITCodeCacheRegion
from .jit_cache import JITTrace


class TraceCompiler:
    """Build/install native trace bodies for the upper runtime.

    Explicit callers pass loader-owned WASM bytes in one call. The native
    JitRuntime compiles its registered blocks directly in C++.
    ``compile_instructions`` also installs a standalone trace.
    """

    __slots__ = ("_standalone_region", "compile_observer")

    def __init__(self) -> None:
        self._standalone_region: JITCodeCacheRegion | None = None
        self.compile_observer: Callable[[int, bool, int], None] | None = None

    @staticmethod
    def compile_wasm(block: native_abi.WasmBlock) -> JITTrace | None:
        """Compile loader-owned WASM bytes into a cache-ready native trace."""
        trace = JITTrace(head_pc=block.head_pc)
        compiled = native_abi.compile_wasm(block, trace._native)
        if compiled is None:
            return None
        TraceCompiler._attach_code(trace, *compiled)
        return trace

    def compile_instructions(
        self,
        *,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_length: int,
        local_layout: LocalWidthMap,
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
        compiled = native_abi.compile_instructions(
            trace._native, instructions, byte_length, local_layout, context_helper, helper_address
        )
        if compiled is None:
            return None
        self._attach_code(trace, *compiled)
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
            trace.entry_body_patch_offset,
            trace.entry_prologue_patch_offset,
            trace.exit_patch_offset,
            trace.helper_header_patch_offset,
            trace.helper_exit_patch_offset,
            trace.chain_dispatch_patch_offset,
            trace.common_helper_offset,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self._standalone_region.buffer
        return trace

    @staticmethod
    def _attach_code(trace: JITTrace, blob: bytes, fixups: native_abi.NativeTraceFixups) -> None:
        """Retain the code bytes and expose C++ relocation metadata at the ABI boundary."""
        trace.code_blob = blob
        trace.entry_body_patch_offset = fixups.entry_body
        trace.entry_prologue_patch_offset = fixups.entry_prologue
        trace.exit_patch_offset = fixups.exit
        trace.helper_header_patch_offset = fixups.helper_header
        trace.helper_exit_patch_offset = fixups.helper_exit
        trace.chain_dispatch_patch_offset = fixups.chain_dispatch
        trace.common_helper_offset = fixups.helper_offset
        trace.bind_code()
