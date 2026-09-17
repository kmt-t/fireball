"""Shared non-evictable APCCS code area for the fixed-size JIT cache region."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

import x64_stencils as st
from config import (
    JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES,
    JIT_CACHE_COMMON_CODE_BYTES,
    JIT_CACHE_REGION_BYTES,
    JIT_TRACE_COMMON_EPILOGUE_OFFSET,
    JIT_TRACE_COMMON_HELPER_OFFSET,
    JIT_TRACE_COMMON_PROLOGUE_OFFSET,
    JIT_X64_CHAIN_TARGET_OFFSET,
    JIT_X64_TRACE_HEADER_BYTES,
)
from exec_memory import ExecutableBuffer

IS_WINDOWS = sys.platform == "win32"

# These offsets are the only common-area locations that may be placed in a
# trace header.  They remain stable for the lifetime of the cache region.
COMMON_PROLOGUE_OFFSET = JIT_TRACE_COMMON_PROLOGUE_OFFSET
COMMON_EPILOGUE_OFFSET = JIT_TRACE_COMMON_EPILOGUE_OFFSET
COMMON_HELPER_OFFSET = JIT_TRACE_COMMON_HELPER_OFFSET
COMMON_ABSOLUTE_POOL_OFFSET = 80
TRACE_ENTRY_STUB_BYTES = 15
TRACE_BODY_OFFSET = JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES


def gen_pic_prologue() -> bytes:
    """Generate the APCCS/CPS prologue template kept in common code."""

    code = bytearray()
    code += bytes((0x53,))  # push rbx
    code += bytes((0x41, 0x54))  # push r12
    code += bytes((0x41, 0x55))  # push r13
    code += bytes((0x41, 0x56))  # push r14
    code += bytes((0x41, 0x57))  # push r15
    if IS_WINDOWS:
        # Windows x64 ABI: (RCX=ctx, RDX=sp, R8=local_base, R9=tos)
        code += bytes((0x57,))  # push rdi
        code += bytes((0x48, 0x89, 0xE7))  # mov rdi, rsp
        code += bytes((0x4D, 0x89, 0xC2))  # mov r10, r8
        code += bytes((0x49, 0x89, 0xD4))  # mov r12, rdx
        code += bytes((0x49, 0x89, 0xCD))  # mov r13, rcx
    else:
        # System V AMD64 ABI: (RDI=ctx, RSI=sp, RDX=local_base, RCX=tos)
        code += bytes((0x55,))  # push rbp
        code += bytes((0x48, 0x89, 0xE5))  # mov rbp, rsp
        code += bytes((0x49, 0x89, 0xD2))  # mov r10, rdx
        code += bytes((0x49, 0x89, 0xF4))  # mov r12, rsi
        code += bytes((0x49, 0x89, 0xFD))  # mov r13, rdi
        code += bytes((0x44, 0x89, 0xC9))  # mov r9d, ecx
    code += bytes((0xFF, 0xE0))  # jmp rax (body address supplied by entry stub)
    return bytes(code)


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


class JITCodeCacheRegion:
    """Own the contiguous 8KB executable region and its common 2KB prefix.

    The APCCS common prefix is written once and is deliberately outside the three
    rotating banks.  The remaining offsets are used by ``JITCacheBank`` for
    Active, Warm, and Oldest trace storage.  A single W^X buffer is shared by
    all installed traces in a cache instance.
    """

    __slots__ = (
        "absolute_pool_offset",
        "absolute_pool_size",
        "buffer",
        "common_code_bytes",
        "epilogue_offset",
        "epilogue_size",
        "helper_offset",
        "helper_size",
        "prologue_offset",
        "prologue_size",
        "region_bytes",
    )

    def __init__(self) -> None:
        self.region_bytes = JIT_CACHE_REGION_BYTES
        self.common_code_bytes = JIT_CACHE_COMMON_CODE_BYTES
        assert self.common_code_bytes < self.region_bytes
        self.buffer = ExecutableBuffer(self.region_bytes)

        prologue = gen_pic_prologue()
        epilogue = st.EPILOGUE_RETURN_VOID.code
        helper = st.HEADER_HELPER_TAIL_JUMP.code
        self.prologue_offset = 0
        self.prologue_size = len(prologue)
        self.epilogue_offset = COMMON_EPILOGUE_OFFSET
        self.epilogue_size = len(epilogue)
        self.helper_offset = COMMON_HELPER_OFFSET
        self.helper_size = len(helper)
        self.absolute_pool_offset = COMMON_ABSOLUTE_POOL_OFFSET
        self.absolute_pool_size = JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES
        assert self.prologue_offset + self.prologue_size <= self.epilogue_offset
        assert self.epilogue_offset + self.epilogue_size <= self.helper_offset
        assert self.helper_offset + self.helper_size <= self.common_code_bytes
        assert self.absolute_pool_offset + self.absolute_pool_size <= self.common_code_bytes

        common = bytearray(self.common_code_bytes)
        common[self.prologue_offset : self.prologue_offset + self.prologue_size] = prologue
        common[self.epilogue_offset : self.epilogue_offset + self.epilogue_size] = epilogue
        common[self.helper_offset : self.helper_offset + self.helper_size] = helper
        self.buffer.write(0, bytes(common))

    def install_trace(
        self,
        offset: int,
        blob: bytes,
        entry_body_patch_offset: int,
        entry_prologue_patch_offset: int,
        exit_patch_offset: int,
        helper_header_patch_offset: int,
        helper_exit_patch_offset: int,
        chain_header_patch_offset: int = -1,
        chain_fallback_patch_offset: int = -1,
    ) -> tuple[Callable[..., int | None], int]:
        """Relocate and copy one trace using only offsets from its header.

        The trace blob contains no inline APCCS code.  Its entry stub and exit
        stubs are patched against the common offsets serialized in the header,
        so the context does not participate in code-cache routing.
        """

        assert self.common_code_bytes <= offset < self.region_bytes
        assert offset + len(blob) <= self.region_bytes
        patched = bytearray(blob)
        common_prologue = int.from_bytes(patched[0x18:0x1C], "little")
        common_epilogue = int.from_bytes(patched[0x1C:0x20], "little")
        common_helper = int.from_bytes(patched[0x20:0x24], "little")
        assert common_prologue < self.common_code_bytes
        assert common_epilogue < self.common_code_bytes
        assert common_helper < self.common_code_bytes
        base = self.buffer.base
        assert base is not None

        def patch_rel32(patch_offset: int, target_offset: int) -> None:
            next_ip = base + offset + patch_offset + 4
            target = base + target_offset
            displacement = target - next_ip
            assert -(1 << 31) <= displacement < (1 << 31)
            patched[patch_offset : patch_offset + 4] = int(displacement).to_bytes(
                4, "little", signed=True
            )

        if entry_body_patch_offset >= 0:
            patched[entry_body_patch_offset : entry_body_patch_offset + 8] = (
                base + offset + TRACE_BODY_OFFSET
            ).to_bytes(8, "little")
        if entry_prologue_patch_offset >= 0:
            patch_rel32(entry_prologue_patch_offset, common_prologue)
        if exit_patch_offset >= 0:
            patch_rel32(exit_patch_offset, common_epilogue)
        if chain_header_patch_offset >= 0:
            header_addr = base + offset
            next_ip = base + offset + chain_header_patch_offset + 4
            displacement = header_addr - next_ip
            assert -(1 << 31) <= displacement < (1 << 31)
            patched[
                chain_header_patch_offset : chain_header_patch_offset + 4
            ] = int(displacement).to_bytes(4, "little", signed=True)
        if chain_fallback_patch_offset >= 0:
            patch_rel32(chain_fallback_patch_offset, common_epilogue)
        if helper_header_patch_offset >= 0:
            header_addr = base + offset
            next_ip = base + offset + helper_header_patch_offset + 4
            displacement = header_addr - next_ip
            assert -(1 << 31) <= displacement < (1 << 31)
            patched[
                helper_header_patch_offset : helper_header_patch_offset + 4
            ] = int(displacement).to_bytes(4, "little", signed=True)
            patch_rel32(helper_exit_patch_offset, common_helper)
        if not self.buffer.patch_in_progress:
            self.buffer.begin_jit_patch()
        self.buffer.write(offset, bytes(patched))
        self.buffer.commit_jit_patch()
        fn = self.buffer.function_at(
            offset + JIT_X64_TRACE_HEADER_BYTES,
            None,
            (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32),
        )
        return fn, self.buffer.address_of(offset + JIT_X64_TRACE_HEADER_BYTES)

    def patch_header_u64(self, trace_offset: int, value: int) -> None:
        """Patch one x64 header pointer under a complete W^X transaction."""

        assert self.common_code_bytes <= trace_offset < self.region_bytes
        assert 0 <= value <= 0xFFFF_FFFF_FFFF_FFFF
        if not self.buffer.patch_in_progress:
            self.buffer.begin_jit_patch()
        self.buffer.write(
            trace_offset + JIT_X64_CHAIN_TARGET_OFFSET,
            value.to_bytes(8, "little"),
        )
        self.buffer.commit_jit_patch()

    def read_common(self) -> bytes:
        """Read the reserved prefix for immutability checks."""

        return self.buffer.read(0, self.common_code_bytes)


__all__ = (
    "COMMON_ABSOLUTE_POOL_OFFSET",
    "COMMON_EPILOGUE_OFFSET",
    "COMMON_HELPER_OFFSET",
    "COMMON_PROLOGUE_OFFSET",
    "TRACE_BODY_OFFSET",
    "TRACE_ENTRY_STUB_BYTES",
    "JITCodeCacheRegion",
    "gen_pic_prologue",
)
