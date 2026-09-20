"""
experiments/pysim/tier3_jit/x64_stencils.py
x64 Copy-and-Patch stencils, mirroring the real design's split between
compile-time template construction and runtime copy+patch
(docs/components/tier3_executer/jit_compiler.md,
docs/components/tier3_executer/concepts/jit_copy_patch_concept.py's `Stencil`).
The real system builds each stencil once via a C++20 `constexpr` function,
baking a fixed byte array into ROM; the JIT then only ever copies that byte
array and patches a few relocation slots into it. Python has no constexpr,
so each stencil here is instead built by a **generator** that is drained
exactly once, at import time, into an immutable `bytes` object -- the
generator's single run stands in for "compile-time evaluation", and every
actual JIT compilation afterwards only ever touches the frozen result,
never re-runs the generator. This is enforced by `_materialize()` below,
not just a naming convention.
Calling convention for a compiled function is the shared CPS boundary:
`ctx` (R0) is the execution-context pointer, `sp` (R1) is the operand-stack
pointer, `local_base` (R2) points at the local-value array, and `tos` (R3) is
the top operand. The x64 body keeps the local and linear-memory pointers in
R10/R11 for the lifetime of the function body.
The prologue copies those pointers there so the incoming argument registers
stay free as general scratch, since
i32.shl/shr_s/shr_u need the shift count in CL. The WASM operand stack is
the shared Native stack addressed by R12; RSP remains the native call stack
and is never used as the WASM operand stack.
"""

from __future__ import annotations

import sys
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field
from enum import IntEnum

from system_containers import StaticVector, freeze_sequence

IS_WINDOWS = sys.platform == "win32"

# Magic sentinel value loaded into RAX when a WASM trap is triggered.
# Guaranteed never to collide with any sign-extended 32-bit integer result.
TRAP_SENTINEL: int = 0x7FFF_DEAD_BEEF_0001


class WasmTrapError(OSError):
    """Raised when WASM execution traps (e.g. out-of-bounds memory access or unreachable)."""


class Relocation(IntEnum):
    DISP = 0
    IMM = 1
    REL32 = 2
    MAX_ADDR = 3
    TRAP = 4
    ADDR = 5
    IMM64 = 6


RELOCATION_COUNT = 7
NO_RELOCATION = -1
_EMPTY_RELOC_ENTRIES: tuple[tuple[Relocation, int], ...] = ()
_EMPTY_RELOC_OFFSETS: tuple[int, ...] = (NO_RELOCATION,) * RELOCATION_COUNT


@dataclass(frozen=True)
class Stencil:
    code: bytes
    reloc_entries: tuple[tuple[Relocation, int], ...] = field(
        default_factory=lambda: _EMPTY_RELOC_ENTRIES
    )
    # Relocation IDs are dense integers, so patch lookup is one tuple index.
    reloc_offsets: tuple[int, ...] = field(default_factory=lambda: _EMPTY_RELOC_OFFSETS)

    def __len__(self) -> int:
        return len(self.code)


#: Sentinel byte patterns used only by the multi-relocation stencils below
#: (memory access, globals): each is a distinct, easily-`.find()`-able run
#: that would never otherwise appear in these short opcode sequences.
#: `_materialize_auto()` locates every sentinel actually present, records
#: its offset, and zeroes it -- computing relocation offsets from where the
#: bytes actually landed instead of a second, error-prone hand-count. This
#: exists because earlier single-reloc stencils in this file *were* hand-
#: counted, and that hand-counting produced four real encoding bugs found
#: only by executing the stencils (see test_x64_stencils.py) -- multi-reloc
#: stencils have that much more room for the same mistake, so they don't
#: get to rely on hand-counting at all.
_SENTINEL_MAX_ADDR = bytes((0xA1, 0xA1, 0xA1, 0xA1))
_SENTINEL_TRAP = bytes((0xA2, 0xA2, 0xA2, 0xA2))
_SENTINEL_DISP = bytes((0xA3, 0xA3, 0xA3, 0xA3))
_SENTINEL_ADDR64 = bytes((0xA4,) * 8)
_SENTINEL_IMM64 = bytes((0xA6,) * 8)
_RELOC_SENTINELS: tuple[tuple[Relocation, bytes], ...] = (
    (Relocation.MAX_ADDR, _SENTINEL_MAX_ADDR),
    (Relocation.TRAP, _SENTINEL_TRAP),
    (Relocation.DISP, _SENTINEL_DISP),
    (Relocation.ADDR, _SENTINEL_ADDR64),
    (Relocation.IMM64, _SENTINEL_IMM64),
)


def _materialize_auto(gen: Iterable[int]) -> Stencil:
    """
    Like _materialize(), but discovers every relocation slot in `gen`'s
        output by locating the sentinel patterns in `_RELOC_SENTINELS`, instead
        of taking hand-counted byte offsets as a parameter.
    """

    code = bytearray(gen)
    entries: StaticVector[tuple[Relocation, int]] = StaticVector(capacity=RELOCATION_COUNT)
    for reloc_name, sentinel in _RELOC_SENTINELS:
        idx = code.find(sentinel)
        if idx == -1:
            continue
        assert code.find(sentinel, idx + 1) == -1
        entries.append((reloc_name, idx))
        code[idx : idx + len(sentinel)] = bytes(len(sentinel))
    entries.sort(key=lambda e: e[0])
    reloc_entries = freeze_sequence(entries)
    reloc_offsets = _relocation_offsets(reloc_entries)
    return Stencil(code=bytes(code), reloc_entries=reloc_entries, reloc_offsets=reloc_offsets)


def _materialize(
    gen: Iterable[int],
    relocs: tuple[tuple[Relocation, int], ...] = (),
) -> Stencil:
    """
    Drains a stencil generator exactly once ("compile time") into a
    frozen Stencil. Called only at module load, never per-JIT-compilation.
    Relocations use fixed dense integer IDs and are converted immediately into
    a direct-index offset tuple.
    """

    reloc_entries = freeze_sequence(sorted(relocs, key=lambda e: e[0]))
    reloc_offsets = _relocation_offsets(reloc_entries)
    return Stencil(code=bytes(gen), reloc_entries=reloc_entries, reloc_offsets=reloc_offsets)


def _relocation_offsets(
    entries: tuple[tuple[Relocation, int], ...],
) -> tuple[int, ...]:
    """Build the dense relocation table once while materializing a stencil."""

    offsets: StaticVector[int] = StaticVector(capacity=RELOCATION_COUNT)
    for _ in range(RELOCATION_COUNT):
        offsets.append(NO_RELOCATION)
    for reloc_id, offset in entries:
        index = int(reloc_id)
        assert 0 <= index < RELOCATION_COUNT
        assert offsets[index] == NO_RELOCATION
        offsets[index] = offset
    return freeze_sequence(offsets)


# ---------------------------------------------------------------------------
# constexpr-simulating stencil generators
#
# Each of these is drained exactly once below, in the "Stencil table" section.
# Writing them as generators (rather than returning `bytes` directly) is the
# point: it mirrors a constexpr assembler emitting one encoded instruction at
# a time into a byte array under a compile-time evaluator, rather than an
# ordinary runtime function assembling a bytes object.
# ---------------------------------------------------------------------------


def _gen_prologue() -> Generator[int, None, None]:
    # Callee-saved: rbx, r12, r13, r14, r15
    # push rbx            53
    yield 0x53
    # push r12            41 54
    yield from (0x41, 0x54)
    # push r13            41 55
    yield from (0x41, 0x55)
    # push r14            41 56
    yield from (0x41, 0x56)
    # push r15            41 57
    yield from (0x41, 0x57)
    if IS_WINDOWS:
        # Microsoft x64 ABI: arg0=rcx (locals), arg1=rdx (mem)
        # push rdi            57
        yield 0x57
        # mov rdi, rsp        48 89 E7
        yield from (0x48, 0x89, 0xE7)
        # mov r10, rcx        49 89 CA  (R10 = locals)
        yield from (0x49, 0x89, 0xCA)
        # mov r11, rdx        49 89 D3  (R11 = mem)
        yield from (0x49, 0x89, 0xD3)
    else:
        # System V AMD64 ABI (Linux): arg0=rdi (locals), arg1=rsi (mem)
        # push rbp            55
        yield 0x55
        # mov rbp, rsp        48 89 E5
        yield from (0x48, 0x89, 0xE5)
        # mov r10, rdi        49 89 FA  (R10 = locals)
        yield from (0x49, 0x89, 0xFA)
        # mov r11, rsi        49 89 F3  (R11 = mem)
        yield from (0x49, 0x89, 0xF3)


def _gen_spill_result_to_sp() -> Generator[int, None, None]:
    # A compiled trace's residual value is WASM VM state (the operand stack's
    # top), not a C return value -- it has no relationship to the callee's
    # own return channel, so it is written to memory (via R12, the CPS
    # sp argument `gen_pic_prologue` maps it to) rather than left in
    # RAX for the caller to read as a return value.
    # pop rax                 58
    yield 0x58
    # mov [r12], eax          41 89 04 24
    yield from (0x41, 0x89, 0x04, 0x24)


def _gen_local_get() -> Generator[int, None, None]:
    # mov eax, [r10 + disp32]     41 8B 82 xx xx xx xx   (disp32 relocated)
    yield from (0x41, 0x8B, 0x82, 0x00, 0x00, 0x00, 0x00)
    # movsxd rax, eax             48 63 C0
    yield from (0x48, 0x63, 0xC0)
    # push rax                    50
    yield 0x50


def _gen_local_set() -> Generator[int, None, None]:
    # pop rax                     58
    yield 0x58
    # mov [r10 + disp32], eax     41 89 82 xx xx xx xx
    yield from (0x41, 0x89, 0x82, 0x00, 0x00, 0x00, 0x00)


def _gen_local_tee() -> Generator[int, None, None]:
    # mov rax, [rsp]              48 8B 04 24     (peek without popping)
    yield from (0x48, 0x8B, 0x04, 0x24)
    # mov [r10 + disp32], eax     41 89 82 xx xx xx xx
    yield from (0x41, 0x89, 0x82, 0x00, 0x00, 0x00, 0x00)


def _gen_i32_const() -> Generator[int, None, None]:
    # mov eax, imm32 (zero-extends into rax)   B8 xx xx xx xx
    yield 0xB8
    yield from (0x00, 0x00, 0x00, 0x00)
    # push rax                                  50
    yield 0x50


def _gen_i64_const() -> Generator[int, None, None]:
    # mov rax, imm64 (48 B8 imm64); push rax
    yield from (0x48, 0xB8)
    yield from _SENTINEL_IMM64
    yield 0x50


def _gen_binop(mnemonic_bytes: bytes) -> bytes:
    """
    pop rbx; pop rax; <op eax, ebx>; push rax -- the shared shape of
        every i32 binary operator stencil (second operand popped first is `b`,
        first popped after is `a`, matching WASM's a-then-b push order).
    """

    prefix = bytes((0x5B, 0x58))  # pop rbx ; pop rax
    suffix = bytes((0x50,))  # push rax
    return prefix + mnemonic_bytes + suffix


def _gen_cmp_setcc(setcc_opcode: int) -> bytes:
    """pop rbx; pop rax; cmp eax, ebx; set<cc> al; movzx eax, al; push rax."""
    return bytes(
        (
            0x5B,
            0x58,  # pop rbx ; pop rax
            0x39,
            0xD8,  # cmp eax, ebx
            0x0F,
            setcc_opcode,
            0xC0,  # set<cc> al
            0x0F,
            0xB6,
            0xC0,  # movzx eax, al
            0x50,  # push rax
        )
    )


def _gen_i32_eqz() -> Generator[int, None, None]:
    # pop rax; test eax,eax; sete al; movzx eax,al; push rax
    yield from (0x58, 0x85, 0xC0, 0x0F, 0x94, 0xC0, 0x0F, 0xB6, 0xC0, 0x50)


def _gen_i32_div_s() -> Generator[int, None, None]:
    # pop rbx (divisor); pop rax (dividend); cdq; idiv ebx; push rax
    yield from (0x5B, 0x58, 0x99, 0xF7, 0xFB, 0x50)


def _gen_i32_div_u() -> Generator[int, None, None]:
    # pop rbx; pop rax; xor edx,edx; div ebx; push rax
    yield from (0x5B, 0x58, 0x31, 0xD2, 0xF7, 0xF3, 0x50)


def _gen_i32_rem_s() -> Generator[int, None, None]:
    # pop rbx; pop rax; cdq; idiv ebx; push rdx (remainder)
    yield from (0x5B, 0x58, 0x99, 0xF7, 0xFB, 0x52)


def _gen_i32_rem_u() -> Generator[int, None, None]:
    # pop rbx; pop rax; xor edx,edx; div ebx; push rdx
    yield from (0x5B, 0x58, 0x31, 0xD2, 0xF7, 0xF3, 0x52)


def _gen_shift(shift_opcode_ext: int) -> bytes:
    """
    pop rcx (shift amount); pop rax; shl/sar/shr eax, cl; push rax.
        shift_opcode_ext selects the /reg field of D3 (SHL=4, SAR=7, SHR=5).
    """

    modrm = 0xC0 | (shift_opcode_ext << 3)  # ModRM for "D3 /ext, eax"
    return bytes((0x59, 0x58, 0xD3, modrm, 0x50))  # pop rcx; pop rax; D3 /ext eax,cl; push rax


def _gen_bounds_check() -> Generator[int, None, None]:
    """
    wasm_instruction_set.md 3.4 mandates a "比較+トラップ" (compare +
        trap) bounds check before every memory access. `max_addr` is
        `mem_size_bytes - memarg.offset - access_width`, computed once at JIT
        time (this experiment treats linear memory as fixed-size, matching its
        lack of a JIT-side memory.grow); an unsigned compare against it covers
        the memarg offset and access width in one shot, so the checked address
        itself needs no further arithmetic before the actual load/store.
        Consumes nothing, assumes the (unsigned) address is already in eax.
    """
    # cmp eax, imm32(max_addr)   3D xx xx xx xx
    yield 0x3D
    yield from _SENTINEL_MAX_ADDR
    # ja rel32(trap)             0F 87 xx xx xx xx
    yield from (0x0F, 0x87)
    yield from _SENTINEL_TRAP


def _gen_i32_load() -> Generator[int, None, None]:
    # pop rax (address, zero-extended u32 already on stack as such)
    yield 0x58
    yield from _gen_bounds_check()
    # mov eax, [r11 + rax + disp32]   41 8B 84 03 xx xx xx xx  (disp32 relocated = memarg offset)
    # REX.B only (base r11 needs the extension; index rax and reg eax don't)
    yield from (0x41, 0x8B, 0x84, 0x03)
    yield from _SENTINEL_DISP
    # movsxd rax, eax
    yield from (0x48, 0x63, 0xC0)
    # push rax
    yield 0x50


def _gen_i32_load8_u() -> Generator[int, None, None]:
    yield 0x58
    yield from _gen_bounds_check()
    # movzx eax, byte [r11+rax+disp32]   41 0F B6 84 03 xx xx xx xx
    yield from (0x41, 0x0F, 0xB6, 0x84, 0x03)
    yield from _SENTINEL_DISP
    yield 0x50


def _gen_i32_load8_s() -> Generator[int, None, None]:
    yield 0x58
    yield from _gen_bounds_check()
    # movsx eax, byte [r11+rax+disp32]   41 0F BE 84 03 xx xx xx xx
    yield from (0x41, 0x0F, 0xBE, 0x84, 0x03)
    yield from _SENTINEL_DISP
    yield from (0x48, 0x63, 0xC0)  # movsxd rax, eax
    yield 0x50


def _gen_i32_load16_u() -> Generator[int, None, None]:
    yield 0x58
    yield from _gen_bounds_check()
    # movzx eax, word [r11+rax+disp32]   41 0F B7 84 03 xx xx xx xx
    yield from (0x41, 0x0F, 0xB7, 0x84, 0x03)
    yield from _SENTINEL_DISP
    yield 0x50


def _gen_i32_load16_s() -> Generator[int, None, None]:
    yield 0x58
    yield from _gen_bounds_check()
    # movsx eax, word [r11+rax+disp32]   41 0F BF 84 03 xx xx xx xx
    yield from (0x41, 0x0F, 0xBF, 0x84, 0x03)
    yield from _SENTINEL_DISP
    yield from (0x48, 0x63, 0xC0)
    yield 0x50


def _gen_i32_store() -> Generator[int, None, None]:
    # pop rbx (value); pop rax (address)
    yield from (0x5B, 0x58)
    yield from _gen_bounds_check()
    # mov [r11 + rax + disp32], ebx   41 89 9C 03 xx xx xx xx
    yield from (0x41, 0x89, 0x9C, 0x03)
    yield from _SENTINEL_DISP


def _gen_i32_store8() -> Generator[int, None, None]:
    yield from (0x5B, 0x58)
    yield from _gen_bounds_check()
    # mov [r11+rax+disp32], bl   41 88 9C 03 xx xx xx xx
    yield from (0x41, 0x88, 0x9C, 0x03)
    yield from _SENTINEL_DISP


def _gen_i32_store16() -> Generator[int, None, None]:
    yield from (0x5B, 0x58)
    yield from _gen_bounds_check()
    # mov [r11+rax+disp32], bx   66 41 89 9C 03 xx xx xx xx  (0x66 operand-size prefix before REX)
    yield 0x66
    yield from (0x41, 0x89, 0x9C, 0x03)
    yield from _SENTINEL_DISP


def _gen_drop() -> Generator[int, None, None]:
    # add rsp, 8   48 83 C4 08
    yield from (0x48, 0x83, 0xC4, 0x08)


def _gen_select() -> Generator[int, None, None]:
    # pop rcx (cond); pop rbx (b); pop rax (a); test ecx,ecx; cmovz rax, rbx; push rax
    yield from (0x59, 0x5B, 0x58, 0x85, 0xC9, 0x48, 0x0F, 0x44, 0xC3, 0x50)


def _gen_br() -> Generator[int, None, None]:
    # jmp rel32   E9 xx xx xx xx   (relocated)
    yield 0xE9
    yield from (0x00, 0x00, 0x00, 0x00)


def _gen_br_if() -> Generator[int, None, None]:
    # pop rax; test eax,eax; jnz rel32
    yield from (0x58, 0x85, 0xC0)
    yield 0x0F
    yield 0x85
    yield from (0x00, 0x00, 0x00, 0x00)


def _gen_call() -> Generator[int, None, None]:
    # call rel32   E8 xx xx xx xx   (relocated to the callee's final address)
    yield 0xE8
    yield from (0x00, 0x00, 0x00, 0x00)


def _gen_unreachable() -> Generator[int, None, None]:
    yield from _gen_trap()


def _gen_trap() -> Generator[int, None, None]:
    """
    WASM execution trap handler.
    First snaps rsp back to the frame anchor captured right after prologue pushes
    (rdi on Windows, rbp on System V AMD64 / POSIX), unwinds the callee-saved registers,
    loads the 64-bit TRAP_SENTINEL magic into RAX, and cleanly returns.
    This provides cross-platform, deterministic trap detection (Windows and Linux/POSIX)
    without triggering OS-level SIGSEGV / Access Violation crashes or requiring debuggers.
    """
    if IS_WINDOWS:
        # mov rsp, rdi          48 89 FC
        yield from (0x48, 0x89, 0xFC)
    else:
        # mov rsp, rbp          48 89 EC
        yield from (0x48, 0x89, 0xEC)
    yield from _gen_restore_unwind_only()
    # movabs rax, imm64(TRAP_SENTINEL)    48 B8 xx*8
    yield from (0x48, 0xB8)
    yield from TRAP_SENTINEL.to_bytes(8, "little")
    # ret                                 C3
    yield 0xC3


def _gen_restore_unwind_only() -> Generator[int, None, None]:
    """
    Same register-restore sequence as _gen_restore_callee_saved_and_ret,
        minus the trailing `ret` -- shared by TRAP, which needs the stack
        unwound but must fall through into the crash instead of returning.
    """

    if IS_WINDOWS:
        yield 0x5F  # pop rdi
    else:
        yield 0x5D  # pop rbp

    yield from (0x41, 0x5F)  # pop r15
    yield from (0x41, 0x5E)  # pop r14
    yield from (0x41, 0x5D)  # pop r13
    yield from (0x41, 0x5C)  # pop r12
    yield 0x5B  # pop rbx


def _gen_i32_clz() -> Generator[int, None, None]:
    # pop rax; lzcnt eax, eax; push rax  (LZCNT returns 32 for a zero
    # input, exactly WASM's i32.clz(0) == 32 -- needs the LZCNT CPU
    # feature, ubiquitous on x86-64 hardware built in the last ~15 years)
    yield 0x58
    yield from (0xF3, 0x0F, 0xBD, 0xC0)
    yield 0x50


def _gen_i32_ctz() -> Generator[int, None, None]:
    # pop rax; tzcnt eax, eax; push rax  (TZCNT(0) == 32, matching WASM)
    yield 0x58
    yield from (0xF3, 0x0F, 0xBC, 0xC0)
    yield 0x50


def _gen_i32_popcnt() -> Generator[int, None, None]:
    yield 0x58
    yield from (0xF3, 0x0F, 0xB8, 0xC0)
    yield 0x50


def _gen_rotate(rotate_opcode_ext: int) -> bytes:
    """
    pop rcx (amount); pop rax; rol/ror eax, cl; push rax -- same D3 /ext
        shape as _gen_shift, ROL=/0, ROR=/1.
    """

    modrm = 0xC0 | (rotate_opcode_ext << 3)
    return bytes((0x59, 0x58, 0xD3, modrm, 0x50))


def _gen_global_get() -> Generator[int, None, None]:
    """
    Reads through an absolute address baked in at JIT-compile time
        (base-of-globals-array + index*8, both known once the globals buffer
        is allocated) -- simpler than threading a third persistent register
        through every function's calling convention for a per-module-not-per-
        call concept.
    """
    # mov rax, imm64(addr)   48 B8 xx*8
    yield from (0x48, 0xB8)
    yield from _SENTINEL_ADDR64
    # mov eax, [rax]         8B 00
    yield from (0x8B, 0x00)
    # movsxd rax, eax        48 63 C0
    yield from (0x48, 0x63, 0xC0)
    # push rax               50
    yield 0x50


def _gen_global_set() -> Generator[int, None, None]:
    # pop rbx (value)        5B
    yield 0x5B
    # mov rax, imm64(addr)   48 B8 xx*8
    yield from (0x48, 0xB8)
    yield from _SENTINEL_ADDR64
    # mov [rax], ebx         89 18
    yield from (0x89, 0x18)


# ---------------------------------------------------------------------------
# Stencil table -- every generator above is drained exactly once here.
# ---------------------------------------------------------------------------

PROLOGUE = _materialize(_gen_prologue())
SPILL_RESULT_TO_SP = _materialize(_gen_spill_result_to_sp())
LOCAL_GET = _materialize(_gen_local_get(), ((Relocation.DISP, 3),))
LOCAL_SET = _materialize(_gen_local_set(), ((Relocation.DISP, 4),))
LOCAL_TEE = _materialize(_gen_local_tee(), ((Relocation.DISP, 7),))
I32_CONST = _materialize(_gen_i32_const(), ((Relocation.IMM, 1),))
I64_CONST = _materialize(_gen_i64_const(), ((Relocation.IMM64, 2),))
F32_CONST = _materialize(_gen_i32_const(), ((Relocation.IMM, 1),))
F64_CONST = _materialize(_gen_i64_const(), ((Relocation.IMM64, 2),))
I32_ADD = _materialize(_gen_binop(bytes((0x01, 0xD8))))  # add eax, ebx
I32_SUB = _materialize(_gen_binop(bytes((0x29, 0xD8))))  # sub eax, ebx
I32_MUL = _materialize(_gen_binop(bytes((0x0F, 0xAF, 0xC3))))  # imul eax, ebx

I32_AND = _materialize(_gen_binop(bytes((0x21, 0xD8))))  # and eax, ebx
I32_OR = _materialize(_gen_binop(bytes((0x09, 0xD8))))  # or eax, ebx
I32_XOR = _materialize(_gen_binop(bytes((0x31, 0xD8))))  # xor eax, ebx
I32_DIV_S = _materialize(_gen_i32_div_s())
I32_DIV_U = _materialize(_gen_i32_div_u())
I32_REM_S = _materialize(_gen_i32_rem_s())
I32_REM_U = _materialize(_gen_i32_rem_u())
I32_SHL = _materialize(_gen_shift(4))
I32_SHR_S = _materialize(_gen_shift(7))
I32_SHR_U = _materialize(_gen_shift(5))
I32_EQZ = _materialize(_gen_i32_eqz())
I32_EQ = _materialize(_gen_cmp_setcc(0x94))  # sete
I32_NE = _materialize(_gen_cmp_setcc(0x95))  # setne
I32_LT_S = _materialize(_gen_cmp_setcc(0x9C))  # setl
I32_LT_U = _materialize(_gen_cmp_setcc(0x92))  # setb
I32_GT_S = _materialize(_gen_cmp_setcc(0x9F))  # setg
I32_GT_U = _materialize(_gen_cmp_setcc(0x97))  # seta
I32_LE_S = _materialize(_gen_cmp_setcc(0x9E))  # setle
I32_LE_U = _materialize(_gen_cmp_setcc(0x96))  # setbe
I32_GE_S = _materialize(_gen_cmp_setcc(0x9D))  # setge
I32_GE_U = _materialize(_gen_cmp_setcc(0x93))  # setae
I32_LOAD = _materialize_auto(_gen_i32_load())
I32_LOAD8_S = _materialize_auto(_gen_i32_load8_s())
I32_LOAD8_U = _materialize_auto(_gen_i32_load8_u())
I32_LOAD16_S = _materialize_auto(_gen_i32_load16_s())
I32_LOAD16_U = _materialize_auto(_gen_i32_load16_u())
I32_STORE = _materialize_auto(_gen_i32_store())
I32_STORE8 = _materialize_auto(_gen_i32_store8())
I32_STORE16 = _materialize_auto(_gen_i32_store16())
I32_CLZ = _materialize(_gen_i32_clz())
I32_CTZ = _materialize(_gen_i32_ctz())
I32_POPCNT = _materialize(_gen_i32_popcnt())
I32_ROTL = _materialize(_gen_rotate(0))
I32_ROTR = _materialize(_gen_rotate(1))
GLOBAL_GET = _materialize_auto(_gen_global_get())
GLOBAL_SET = _materialize_auto(_gen_global_set())
DROP = _materialize(_gen_drop())
SELECT = _materialize(_gen_select())
BR = _materialize(_gen_br(), ((Relocation.REL32, 1),))
BR_IF = _materialize(_gen_br_if(), ((Relocation.REL32, 5),))
CALL = _materialize(_gen_call(), ((Relocation.REL32, 1),))
UNREACHABLE = _materialize(_gen_unreachable())
TRAP = _materialize(_gen_trap())
