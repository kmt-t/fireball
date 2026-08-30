"""
experiments/pysim/x64_stencils.py
x64 Copy-and-Patch stencils, mirroring the real design's split between
compile-time template construction and runtime copy+patch
(docs/components/tier3_jit/jit_compiler.md,
docs/components/tier3_jit/concepts/jit_copy_patch_concept.py's `Stencil`).
The real system builds each stencil once via a C++20 `constexpr` function,
baking a fixed byte array into ROM; the JIT then only ever copies that byte
array and patches a few relocation slots into it. Python has no constexpr,
so each stencil here is instead built by a **generator** that is drained
exactly once, at import time, into an immutable `bytes` object -- the
generator's single run stands in for "compile-time evaluation", and every
actual JIT compilation afterwards only ever touches the frozen result,
never re-runs the generator. This is enforced by `_materialize()` below,
not just a naming convention.
Calling convention for a compiled function (Microsoft x64 ABI, since the
host is Windows): RCX = pointer to this function's [params..., locals...]
array (int64 slots), RDX = pointer to the linear memory byte buffer.
R10/R11 hold those two values for the lifetime of the function body (the
prologue copies them there) so RCX/RDX stay free as general scratch, since
i32.shl/shr_s/shr_u need the shift count in CL. The WASM operand stack is
the real x64 hardware stack (PUSH/POP), one 8-byte slot per WASM value.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from dataclasses import dataclass, field
from typing import Generator, Iterable

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Stencil:
    name: str
    code: bytes
    # name -> byte offset within `code` of a 4-byte little-endian relocation slot.
    relocs: dict[str, int] = field(default_factory=dict)

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
_RELOC_SENTINELS = {
    "max_addr": _SENTINEL_MAX_ADDR,
    "trap": _SENTINEL_TRAP,
    "disp": _SENTINEL_DISP,
    "addr": _SENTINEL_ADDR64,
}


def _materialize_auto(
    name: str, gen: Generator[int, None, None] | Iterable[int]
) -> "Stencil":
    """
    Like _materialize(), but discovers every relocation slot in `gen`'s
        output by locating the sentinel patterns in `_RELOC_SENTINELS`, instead
        of taking hand-counted byte offsets as a parameter.
    """

    code = bytearray(gen)
    relocs: dict[str, int] = {}
    for reloc_name, sentinel in _RELOC_SENTINELS.items():
        idx = code.find(sentinel)
        if idx == -1:
            continue

        assert code.find(sentinel, idx + 1) == -1, (
            f"stencil {name!r}: sentinel for {reloc_name!r} appears more than once"
        )
        relocs[reloc_name] = idx
        code[idx : idx + len(sentinel)] = bytes(len(sentinel))

    return Stencil(name=name, code=bytes(code), relocs=relocs)


def _materialize(
    name: str,
    gen: Generator[int, None, dict[str, int]] | Iterable[int],
    relocs: dict[str, int] | None = None,
) -> Stencil:
    """
    Drains a stencil generator exactly once ("compile time") into a
        frozen Stencil. Called only at module load, never per-JIT-compilation.
    """

    return Stencil(name=name, code=bytes(gen), relocs=dict(relocs or {}))


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


def _gen_epilogue_return_i32() -> Generator[int, None, None]:

    # pop rax             58
    yield 0x58
    # movsxd rax, eax  (sign-extend the i32 result into rax)  48 63 C0
    yield from (0x48, 0x63, 0xC0)
    yield from _gen_restore_callee_saved_and_ret()


def _gen_epilogue_return_void() -> Generator[int, None, None]:

    # xor eax, eax        31 C0
    yield from (0x31, 0xC0)
    yield from _gen_restore_callee_saved_and_ret()


def _gen_restore_callee_saved_and_ret() -> Generator[int, None, None]:

    # pop rdi / r15 / r14 / r13 / r12 / rbx -- exact reverse of the
    # prologue's push order -- then ret.
    yield from _gen_restore_unwind_only()
    yield 0xC3  # ret


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
    return bytes(
        (0x59, 0x58, 0xD3, modrm, 0x50)
    )  # pop rcx; pop rax; D3 /ext eax,cl; push rax


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
    Deliberate null-pointer dereference: on Windows this reliably
        raises a real, catchable access violation (Python's ctypes surfaces it
        as `OSError`) rather than requiring an attached debugger the way `int3`
        would -- the same trap target `unreachable` and every bounds-checked
        memory stencil jump to.
        First snaps rsp back to rdi (the restore point PROLOGUE captured right
        after its pushes) and unwinds those same 6 registers, exactly like a
        normal return -- so the fault always occurs at the identical, fixed
        native stack depth relative to this function's own entry, no matter
        how deep the WASM operand stack had grown at the trapping instruction.
        See _gen_prologue's comment for why this consistency is load-bearing:
        without it, Windows' unwinder only recovers by accident.
    """
    # mov rsp, rdi          48 89 FC
    yield from (0x48, 0x89, 0xFC)
    yield from _gen_restore_unwind_only()
    # mov rax, 0            48 C7 C0 00 00 00 00
    yield from (0x48, 0xC7, 0xC0, 0x00, 0x00, 0x00, 0x00)
    # mov [rax], rax        48 89 00
    yield from (0x48, 0x89, 0x00)


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

PROLOGUE = _materialize("prologue", _gen_prologue())
EPILOGUE_RETURN_I32 = _materialize("epilogue_return_i32", _gen_epilogue_return_i32())
EPILOGUE_RETURN_VOID = _materialize("epilogue_return_void", _gen_epilogue_return_void())
LOCAL_GET = _materialize("local_get", _gen_local_get(), {"disp": 3})
LOCAL_SET = _materialize("local_set", _gen_local_set(), {"disp": 4})
LOCAL_TEE = _materialize("local_tee", _gen_local_tee(), {"disp": 7})
I32_CONST = _materialize("i32_const", _gen_i32_const(), {"imm": 1})
I32_ADD = _materialize("i32_add", _gen_binop(bytes((0x01, 0xD8))))  # add eax, ebx
I32_SUB = _materialize("i32_sub", _gen_binop(bytes((0x29, 0xD8))))  # sub eax, ebx
I32_MUL = _materialize(
    "i32_mul", _gen_binop(bytes((0x0F, 0xAF, 0xC3)))
)  # imul eax, ebx

I32_AND = _materialize("i32_and", _gen_binop(bytes((0x21, 0xD8))))  # and eax, ebx
I32_OR = _materialize("i32_or", _gen_binop(bytes((0x09, 0xD8))))  # or eax, ebx
I32_XOR = _materialize("i32_xor", _gen_binop(bytes((0x31, 0xD8))))  # xor eax, ebx
I32_DIV_S = _materialize("i32_div_s", _gen_i32_div_s())
I32_DIV_U = _materialize("i32_div_u", _gen_i32_div_u())
I32_REM_S = _materialize("i32_rem_s", _gen_i32_rem_s())
I32_REM_U = _materialize("i32_rem_u", _gen_i32_rem_u())
I32_SHL = _materialize("i32_shl", _gen_shift(4))
I32_SHR_S = _materialize("i32_shr_s", _gen_shift(7))
I32_SHR_U = _materialize("i32_shr_u", _gen_shift(5))
I32_EQZ = _materialize("i32_eqz", _gen_i32_eqz())
I32_EQ = _materialize("i32_eq", _gen_cmp_setcc(0x94))  # sete
I32_NE = _materialize("i32_ne", _gen_cmp_setcc(0x95))  # setne
I32_LT_S = _materialize("i32_lt_s", _gen_cmp_setcc(0x9C))  # setl
I32_LT_U = _materialize("i32_lt_u", _gen_cmp_setcc(0x92))  # setb
I32_GT_S = _materialize("i32_gt_s", _gen_cmp_setcc(0x9F))  # setg
I32_GT_U = _materialize("i32_gt_u", _gen_cmp_setcc(0x97))  # seta
I32_LE_S = _materialize("i32_le_s", _gen_cmp_setcc(0x9E))  # setle
I32_LE_U = _materialize("i32_le_u", _gen_cmp_setcc(0x96))  # setbe
I32_GE_S = _materialize("i32_ge_s", _gen_cmp_setcc(0x9D))  # setge
I32_GE_U = _materialize("i32_ge_u", _gen_cmp_setcc(0x93))  # setae
I32_LOAD = _materialize_auto("i32_load", _gen_i32_load())
I32_LOAD8_S = _materialize_auto("i32_load8_s", _gen_i32_load8_s())
I32_LOAD8_U = _materialize_auto("i32_load8_u", _gen_i32_load8_u())
I32_LOAD16_S = _materialize_auto("i32_load16_s", _gen_i32_load16_s())
I32_LOAD16_U = _materialize_auto("i32_load16_u", _gen_i32_load16_u())
I32_STORE = _materialize_auto("i32_store", _gen_i32_store())
I32_STORE8 = _materialize_auto("i32_store8", _gen_i32_store8())
I32_STORE16 = _materialize_auto("i32_store16", _gen_i32_store16())
I32_CLZ = _materialize("i32_clz", _gen_i32_clz())
I32_CTZ = _materialize("i32_ctz", _gen_i32_ctz())
I32_POPCNT = _materialize("i32_popcnt", _gen_i32_popcnt())
I32_ROTL = _materialize("i32_rotl", _gen_rotate(0))
I32_ROTR = _materialize("i32_rotr", _gen_rotate(1))
GLOBAL_GET = _materialize_auto("global_get", _gen_global_get())
GLOBAL_SET = _materialize_auto("global_set", _gen_global_set())
DROP = _materialize("drop", _gen_drop())
SELECT = _materialize("select", _gen_select())
BR = _materialize("br", _gen_br(), {"rel32": 1})
BR_IF = _materialize("br_if", _gen_br_if(), {"rel32": 5})
CALL = _materialize("call", _gen_call(), {"rel32": 1})
UNREACHABLE = _materialize("unreachable", _gen_unreachable())
TRAP = _materialize("trap", _gen_trap())
