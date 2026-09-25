"""Shared non-evictable ABI and chain-dispatch code for the fixed JIT cache."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

from config import (
    JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES,
    JIT_CACHE_COMMON_CODE_BYTES,
    JIT_CACHE_REGION_BYTES,
    JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES,
    JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET,
    JIT_TRACE_COMMON_EPILOGUE_OFFSET,
    JIT_TRACE_COMMON_HELPER_OFFSET,
    JIT_TRACE_COMMON_PROLOGUE_OFFSET,
    JIT_TRACE_HELPER_ENTRY_BYTES,
    JIT_TRACE_TYPED_I32_HELPER_COUNT,
    JIT_TRACE_TYPED_I32_HELPER_OFFSET,
    JIT_TRACE_WIDE_HELPER_COUNT,
    JIT_TRACE_WIDE_HELPER_OFFSET,
    JIT_X64_CHAIN_TARGET_OFFSET,
    JIT_X64_HELPER_TARGET_OFFSET,
    JIT_X64_TRACE_HEADER_BYTES,
)

from . import native_trace_call
from .exec_memory import ExecutableBuffer

IS_WINDOWS = sys.platform == "win32"

# Fixed common-code entry offsets are selected by the build configuration.
COMMON_PROLOGUE_OFFSET = JIT_TRACE_COMMON_PROLOGUE_OFFSET
COMMON_EPILOGUE_OFFSET = JIT_TRACE_COMMON_EPILOGUE_OFFSET
COMMON_HELPER_OFFSET = JIT_TRACE_COMMON_HELPER_OFFSET
COMMON_ABSOLUTE_POOL_OFFSET = 80
COMMON_CHAIN_DISPATCH_OFFSET = JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET
TRACE_ENTRY_STUB_BYTES = 15
TRACE_BODY_OFFSET = JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES


def gen_pic_prologue() -> bytes:
    """Generate the x64 CPS prologue template kept in common code."""

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
    code += bytes((0x4C, 0x8D, 0x70, (-TRACE_BODY_OFFSET) & 0xFF))  # lea r14, [rax-header]
    code += bytes((0xFF, 0xE0))  # jmp rax (body address supplied by entry stub)
    return bytes(code)


def gen_pic_epilogue() -> bytes:
    """Generate the fixed common-code return path, outside the stencil table."""

    code = bytearray((0x31, 0xC0))  # xor eax, eax
    code += bytes((0x5F,)) if IS_WINDOWS else bytes((0x5D,))
    code += bytes((0x41, 0x5F))  # pop r15
    code += bytes((0x41, 0x5E))  # pop r14
    code += bytes((0x41, 0x5D))  # pop r13
    code += bytes((0x41, 0x5C))  # pop r12
    code += bytes((0x5B, 0xC3))  # pop rbx; ret
    return bytes(code)


def gen_helper_entry() -> bytes:
    """Generate one fixed entry for a helper contract."""

    code = bytearray()
    if IS_WINDOWS:
        code += bytes((0x4C, 0x89, 0xE9))  # mov rcx, r13
        code += bytes((0x4C, 0x89, 0xE2))  # mov rdx, r12
        code += bytes((0x4D, 0x89, 0xD0))  # mov r8, r10
    else:
        code += bytes((0x4C, 0x89, 0xEF))  # mov rdi, r13
        code += bytes((0x4C, 0x89, 0xE6))  # mov rsi, r12
        code += bytes((0x4D, 0x89, 0xD2))  # mov rdx, r10
        code += bytes((0x44, 0x89, 0xC9))  # mov ecx, r9d
    code += bytes((0x48, 0x8B, 0x80)) + JIT_X64_HELPER_TARGET_OFFSET.to_bytes(4, "little")
    if IS_WINDOWS:
        code += bytes((0x5F,))  # pop rdi
    else:
        code += bytes((0x5D,))  # pop rbp
    code += bytes((0x41, 0x5F))  # pop r15
    code += bytes((0x41, 0x5E))  # pop r14
    code += bytes((0x41, 0x5D))  # pop r13
    code += bytes((0x41, 0x5C))  # pop r12
    code += bytes((0x5B, 0xFF, 0xE0))  # pop rbx; jmp rax
    return bytes(code)


def helper_entry_offset(helper_index: int) -> int:
    """Return the fixed common-code offset for one helper contract."""

    if 0 <= helper_index < JIT_TRACE_WIDE_HELPER_COUNT:
        return JIT_TRACE_WIDE_HELPER_OFFSET + helper_index * JIT_TRACE_HELPER_ENTRY_BYTES
    typed_index = helper_index - 11
    if 0 <= typed_index < JIT_TRACE_TYPED_I32_HELPER_COUNT:
        return JIT_TRACE_TYPED_I32_HELPER_OFFSET + typed_index * JIT_TRACE_HELPER_ENTRY_BYTES
    assert False, f"unsupported helper index: {helper_index}"
    return 0


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def gen_i32_helper_entry() -> bytes:
    """Generate the x64 direct entry for two i32 inputs and one result pointer."""

    code = bytearray()
    if IS_WINDOWS:
        # R11d=lhs, R9d=rhs, R12=result slot -> RCX, RDX, R8.
        code += bytes((0x44, 0x89, 0xD9))
        code += bytes((0x44, 0x89, 0xCA))
        code += bytes((0x4D, 0x89, 0xE0))
        code += bytes((0x4C, 0x8B, 0xB0)) + JIT_X64_HELPER_TARGET_OFFSET.to_bytes(4, "little")
        code += bytes((0x48, 0x83, 0xEC, 0x28))
        code += bytes((0x41, 0xFF, 0xD6))
        code += bytes((0x48, 0x83, 0xC4, 0x28))
    else:
        # R11d=lhs, R9d=rhs, R12=result slot -> RDI, RSI, RDX.
        code += bytes((0x44, 0x89, 0xDF))
        code += bytes((0x44, 0x89, 0xCE))
        code += bytes((0x4C, 0x89, 0xE2))
        code += bytes((0x4C, 0x8B, 0xB0)) + JIT_X64_HELPER_TARGET_OFFSET.to_bytes(4, "little")
        code += bytes((0x48, 0x83, 0xEC, 0x08))
        code += bytes((0x41, 0xFF, 0xD6))
        code += bytes((0x48, 0x83, 0xC4, 0x08))
    code += bytes((0xE9, 0, 0, 0, 0))
    return bytes(code)


class JITCodeCacheRegion:
    """Own the contiguous 8KB executable region and its common 2KB prefix.

    The x64 common prefix is written once and is deliberately outside the three
    rotating banks.  The remaining offsets are used by ``JITCacheBank`` for
    Active, Warm, and Oldest trace storage.  A single W^X buffer is shared by
    all installed traces in a cache instance.
    """

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

    def __init__(self) -> None:
        self.region_bytes = JIT_CACHE_REGION_BYTES
        self.common_code_bytes = JIT_CACHE_COMMON_CODE_BYTES
        assert self.common_code_bytes < self.region_bytes
        self.buffer = ExecutableBuffer(self.region_bytes)

        prologue = gen_pic_prologue()
        epilogue = gen_pic_epilogue()
        helper = gen_helper_entry()
        i32_helper_entry = gen_i32_helper_entry()
        generated_chain_dispatch_offset, chain_dispatcher = (
            native_trace_call.common_chain_dispatcher()
        )
        assert generated_chain_dispatch_offset == COMMON_CHAIN_DISPATCH_OFFSET
        assert len(chain_dispatcher) == JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES
        assert 0 < len(helper) <= JIT_TRACE_HELPER_ENTRY_BYTES
        assert len(i32_helper_entry) == JIT_TRACE_HELPER_ENTRY_BYTES
        self.prologue_offset = 0
        self.prologue_size = len(prologue)
        self.epilogue_offset = COMMON_EPILOGUE_OFFSET
        self.epilogue_size = len(epilogue)
        self.helper_offset = JIT_TRACE_WIDE_HELPER_OFFSET
        self.helper_size = JIT_TRACE_HELPER_ENTRY_BYTES
        self.chain_dispatcher_offset = COMMON_CHAIN_DISPATCH_OFFSET
        self.chain_dispatcher_size = len(chain_dispatcher)
        self.absolute_pool_offset = COMMON_ABSOLUTE_POOL_OFFSET
        self.absolute_pool_size = JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES
        assert self.prologue_offset + self.prologue_size <= self.epilogue_offset
        assert self.epilogue_offset + self.epilogue_size <= self.helper_offset
        assert self.absolute_pool_offset + self.absolute_pool_size <= self.common_code_bytes
        assert (
            JIT_TRACE_WIDE_HELPER_OFFSET
            + JIT_TRACE_WIDE_HELPER_COUNT * JIT_TRACE_HELPER_ENTRY_BYTES
            <= self.common_code_bytes
        )
        assert (
            JIT_TRACE_TYPED_I32_HELPER_OFFSET
            + JIT_TRACE_TYPED_I32_HELPER_COUNT * JIT_TRACE_HELPER_ENTRY_BYTES
            <= self.common_code_bytes
        )
        assert self.chain_dispatcher_offset + self.chain_dispatcher_size <= self.common_code_bytes
        assert self.chain_dispatcher_offset >= (
            JIT_TRACE_WIDE_HELPER_OFFSET
            + JIT_TRACE_WIDE_HELPER_COUNT * JIT_TRACE_HELPER_ENTRY_BYTES
        )

        common = bytearray(self.common_code_bytes)
        common[self.prologue_offset : self.prologue_offset + self.prologue_size] = prologue
        common[self.epilogue_offset : self.epilogue_offset + self.epilogue_size] = epilogue
        common[COMMON_HELPER_OFFSET : COMMON_HELPER_OFFSET + len(helper)] = helper
        common[
            self.chain_dispatcher_offset : self.chain_dispatcher_offset + self.chain_dispatcher_size
        ] = chain_dispatcher
        for helper_index in range(JIT_TRACE_WIDE_HELPER_COUNT):
            helper_offset = helper_entry_offset(helper_index)
            common[helper_offset : helper_offset + len(helper)] = helper
        for helper_index in range(11, 15):
            helper_offset = helper_entry_offset(helper_index)
            entry = bytearray(i32_helper_entry)
            entry[-4:] = (COMMON_EPILOGUE_OFFSET - (helper_offset + len(entry))).to_bytes(
                4, "little", signed=True
            )
            common[helper_offset : helper_offset + len(entry)] = entry
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
        chain_dispatch_patch_offset: int = -1,
        common_helper_offset: int = COMMON_HELPER_OFFSET,
    ) -> tuple[Callable[..., int | None], int]:
        """Relocate and copy one trace against fixed shared-code entry points.

        The trace blob contains no inline ABI prologue or chain dispatcher. Its
        entry and exit stubs are patched to common code offsets selected at
        build time. The context does not participate in code-cache routing.
        """

        assert self.common_code_bytes <= offset < self.region_bytes
        assert offset + len(blob) <= self.region_bytes
        patched = bytearray(blob)
        common_prologue = COMMON_PROLOGUE_OFFSET
        common_epilogue = COMMON_EPILOGUE_OFFSET
        assert common_prologue < self.common_code_bytes
        assert common_epilogue < self.common_code_bytes
        common_helper = common_helper_offset
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
        if chain_dispatch_patch_offset >= 0:
            patch_rel32(chain_dispatch_patch_offset, self.chain_dispatcher_offset)
        if helper_header_patch_offset >= 0:
            header_addr = base + offset
            next_ip = base + offset + helper_header_patch_offset + 4
            displacement = header_addr - next_ip
            assert -(1 << 31) <= displacement < (1 << 31)
            patched[helper_header_patch_offset : helper_header_patch_offset + 4] = int(
                displacement
            ).to_bytes(4, "little", signed=True)
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
    "COMMON_CHAIN_DISPATCH_OFFSET",
    "COMMON_EPILOGUE_OFFSET",
    "COMMON_HELPER_OFFSET",
    "COMMON_PROLOGUE_OFFSET",
    "TRACE_BODY_OFFSET",
    "TRACE_ENTRY_STUB_BYTES",
    "JITCodeCacheRegion",
    "gen_pic_epilogue",
    "gen_pic_prologue",
    "helper_entry_offset",
)
