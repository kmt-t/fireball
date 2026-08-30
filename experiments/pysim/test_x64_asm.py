"""
experiments/pysim/test_x64_asm.py

Spec-first tests for x64_asm.py: every encoder is assembled into a real
executable buffer and run on the CPU, never just re-derived by hand a
second time. Registers actually used by x64_jit.py's calling-convention
glue (rax, rbx, rcx, rdx, r8, r9, r10-r15) are covered; rsp/rbp are
excluded from the generic round-trip fuzz since push/pop of the stack
pointer itself has quirky, not-generically-testable semantics.

Every test body is run through `_run_u64`/`_run_ptr_out`, which wrap it
with a save/restore of every Microsoft x64 ABI *callee-saved* register
(rbx, r12-r15) around the body. This is not incidental: the very first
version of this file, with test bodies clobbering r13 and returning to
ctypes without restoring it, corrupted CPython's own register state and
segfaulted the interpreter -- a real ABI violation the JIT itself had
too (see x64_stencils.py's PROLOGUE/EPILOGUE_* for the matching fix
there). Testing that fix IS one of the things this file covers.
"""

from __future__ import annotations

import ctypes

import x64_asm as asm
from exec_memory import ExecutableBuffer

TESTED_REGS = ["rax", "rbx", "rcx", "rdx", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]

_CALLEE_SAVED = ["rbx", "r12", "r13", "r14", "r15"]


def _wrap(body: bytes) -> bytes:
    """Wraps `body` (which must leave its result in rax and must NOT itself
    contain a `ret`) with a save/restore of every callee-saved register the
    body might have clobbered, so it's safe to return straight to ctypes."""
    prologue = b"".join(asm.push_reg(r) for r in _CALLEE_SAVED)
    epilogue = b"".join(asm.pop_reg(r) for r in reversed(_CALLEE_SAVED)) + asm.ret()
    return prologue + bytes(body) + epilogue


def _run_u64(body: bytes, a: int = 0, b: int = 0) -> int:
    code = _wrap(body)
    buf = ExecutableBuffer(max(len(code), 64))
    try:
        buf.write(0, code)
        fn = buf.function_at(0, ctypes.c_uint64, [ctypes.c_uint64, ctypes.c_uint64])
        return fn(a, b)
    finally:
        buf.close()


def _run_void_ptr_arg(body: bytes, out_len: int) -> list[int]:
    """Runs `body` (rcx = pointer to an `out_len`-element u64 output
    buffer the body is expected to fill in; body must not itself `ret`)
    and returns the buffer's contents as a Python list."""
    code = _wrap(body)
    buf = ExecutableBuffer(max(len(code), 64))
    try:
        buf.write(0, code)
        out = (ctypes.c_uint64 * out_len)(*([0] * out_len))
        fn = buf.function_at(0, ctypes.c_void_p, [ctypes.c_void_p])
        fn(ctypes.cast(out, ctypes.c_void_p))
        return list(out)
    finally:
        buf.close()


def test_push_pop_roundtrip_for_every_tested_register():
    for reg in TESTED_REGS:
        code = bytearray()
        if reg != "rcx":
            code += asm.mov_reg_reg(reg, "rcx")     # load the ctypes arg into the register under test
        code += asm.push_reg(reg)
        code += asm.mov_reg_imm64(reg, 0xDEADBEEFCAFEBABE)   # smash it
        code += asm.pop_reg(reg)
        if reg != "rax":
            code += asm.mov_reg_reg("rax", reg)
        got = _run_u64(bytes(code), a=0x1122334455667788)
        assert got == 0x1122334455667788, f"push/pop round-trip failed for {reg}: got {got:#x}"


def test_mov_reg_reg_moves_the_full_64_bits_between_every_pair():
    value = 0x0123456789ABCDEF
    for src in TESTED_REGS:
        for dst in TESTED_REGS:
            if src == dst:
                continue
            code = bytearray()
            if src != "rcx":
                code += asm.mov_reg_reg(src, "rcx")
            code += asm.mov_reg_reg(dst, src)
            if dst != "rax":
                code += asm.mov_reg_reg("rax", dst)
            got = _run_u64(bytes(code), a=value)
            assert got == value, f"mov {dst}, {src} did not carry the value (got {got:#x})"


def test_mov_reg_imm64_loads_every_bit_of_a_64_bit_immediate():
    interesting = [0, 1, 0xFFFFFFFFFFFFFFFF, 0x8000000000000000, 0x0123456789ABCDEF, 0xFEDCBA9876543210]
    for reg in TESTED_REGS:
        for imm in interesting:
            code = bytearray()
            code += asm.mov_reg_imm64(reg, imm)
            if reg != "rax":
                code += asm.mov_reg_reg("rax", reg)
            got = _run_u64(bytes(code))
            assert got == imm, f"mov {reg}, {imm:#x} -> got {got:#x}"


def test_mov_store_and_load_rsp_disp32_round_trip_at_several_offsets():
    for disp in (0, 8, 40, 96):
        code = bytearray()
        code += asm.sub_rsp_imm8(disp + 8)                 # reserve room below rsp for the write
        code += asm.mov_store_rsp_disp32(disp, "rcx")       # [rsp+disp] = arg0
        code += asm.mov_load_rsp_disp32("rax", disp)        # rax = [rsp+disp]
        code += asm.add_rsp_imm8(disp + 8)
        got = _run_u64(bytes(code), a=0xAABBCCDD11223344)
        assert got == 0xAABBCCDD11223344, f"store/load round-trip at disp={disp} got {got:#x}"


def test_mov_store_rsp_disp32_uses_a_distinct_register_per_slot_without_aliasing():
    """Regression-shaped test: store three DIFFERENT register values at
    three DIFFERENT offsets and read them all back, so a copy-paste bug
    reusing the wrong offset or the wrong source register shows up as a
    wrong value at a specific slot instead of passing by coincidence."""
    code = bytearray()
    code += asm.sub_rsp_imm8(56)
    code += asm.mov_reg_reg("r13", "rcx")
    code += asm.mov_reg_imm64("r14", 0x2222222222222222)
    code += asm.mov_reg_imm64("r15", 0x3333333333333333)
    code += asm.mov_store_rsp_disp32(32, "r13")
    code += asm.mov_store_rsp_disp32(40, "r14")
    code += asm.mov_store_rsp_disp32(48, "r15")
    code += asm.mov_load_rsp_disp32("rax", 32)
    code += asm.mov_load_rsp_disp32("rbx", 40)
    code += asm.mov_load_rsp_disp32("rdx", 48)
    code += bytes((0x48, 0x31, 0xD8))    # xor rax, rbx
    code += bytes((0x48, 0x31, 0xD0))    # xor rax, rdx
    code += asm.add_rsp_imm8(56)
    got = _run_u64(bytes(code), a=0x1111111111111111)
    expected = 0x1111111111111111 ^ 0x2222222222222222 ^ 0x3333333333333333
    assert got == expected


def test_and_rsp_imm8_aligns_the_stack_pointer_down_to_16():
    """`and rsp,-16` cannot be undone with a matching `add rsp,N` -- how far
    it rounds down depends on the incoming alignment, which is exactly why
    x64_jit.py's real host-call glue restores rsp from a saved register
    instead of arithmetic. This test's own restore does the same, since an
    `add`-based "undo" here would corrupt the wrapper's own saved
    registers below and crash on return -- which is precisely how the
    first draft of this test found the bug in the first place.
    """
    # rcx = out-pointer; write [out+0]=rsp-before-align, [out+8]=rsp-after-align.
    code = bytearray()
    code += asm.mov_reg_reg("r12", "rcx")    # keep the out-pointer safe in a callee-saved reg
    code += asm.mov_reg_reg("r13", "rsp")     # save the true original rsp
    code += asm.sub_rsp_imm8(7)                # deliberately misalign
    code += asm.mov_reg_reg("rax", "rsp")
    code += asm.and_rsp_imm8(-16 & 0xFF)
    code += asm.mov_reg_reg("rbx", "rsp")
    code += asm.mov_reg_reg("rsp", "r13")      # restore exactly, not by arithmetic
    # mov [r12], rax / mov [r12+8], rbx -- r12, like rsp, has low-3-bits
    # 100 and so ALSO requires a SIB byte as a memory-operand base (the
    # same quirk mov_store_rsp_disp32() already accounts for); written by
    # hand here since this is the one place in the codebase addressing
    # through r12 rather than rsp.
    code += bytes((0x49, 0x89, 0x04, 0x24))            # mov [r12], rax      (rsp before align)
    code += bytes((0x49, 0x89, 0x5C, 0x24, 0x08))       # mov [r12+8], rbx    (rsp after align)

    before, after = _run_void_ptr_arg(bytes(code), out_len=2)
    assert after % 16 == 0, f"and rsp,-16 left rsp={after:#x}, not 16-aligned"
    assert after <= before


def test_call_reg_performs_a_real_indirect_call_and_returns_here():
    # Callee: rax = rcx * 2; ret  (a tiny, genuinely separate function)
    callee_code = asm.mov_reg_reg("rax", "rcx") + bytes((0x48, 0x01, 0xC0)) + asm.ret()  # add rax, rax
    callee_buf = ExecutableBuffer(64)
    try:
        callee_buf.write(0, callee_code)
        callee_addr = callee_buf.address_of(0)

        caller_code = bytearray()
        caller_code += asm.mov_reg_imm64("r10", callee_addr)
        caller_code += asm.call_reg("r10")
        got = _run_u64(bytes(caller_code), a=21)
        assert got == 42
    finally:
        callee_buf.close()


def test_mov_load_scaled_reads_an_array_element_by_index():
    import ctypes as _ct
    arr = (_ct.c_uint64 * 4)(0x1111, 0x2222, 0x3333, 0x4444)
    base_addr = _ct.addressof(arr)
    for i, expected in enumerate((0x1111, 0x2222, 0x3333, 0x4444)):
        code = bytearray()
        code += asm.mov_reg_imm64("rbx", base_addr)
        code += asm.mov_reg_imm64("rax", i)
        code += asm.mov_load_scaled("rax", "rbx", "rax", 8)
        got = _run_u64(bytes(code))
        assert got == expected, f"index {i}: expected {expected:#x}, got {got:#x}"


def _patch_rel32(code: bytearray, reloc_offset: int, target_offset: int) -> None:
    rel = target_offset - (reloc_offset + 4)
    code[reloc_offset:reloc_offset + 4] = (rel & 0xFFFFFFFF).to_bytes(4, "little")


def _emit_jcc(code: bytearray, condition: str) -> int:
    """Appends a placeholder Jcc and returns its reloc offset as an
    ABSOLUTE position in `code` -- jcc_rel32_placeholder()/
    jmp_rel32_placeholder() return an offset relative to the bytes they
    hand back, exactly like every stencil's `relocs` dict; forgetting to
    add the base (the position those bytes were appended at) is exactly
    the bug this helper exists to make impossible to repeat, after it
    corrupted an opcode byte one register over and crashed here first."""
    base = len(code)
    jcc_bytes, local_reloc = asm.jcc_rel32_placeholder(condition)
    code += jcc_bytes
    return base + local_reloc


def _emit_jmp(code: bytearray) -> int:
    base = len(code)
    jmp_bytes, local_reloc = asm.jmp_rel32_placeholder()
    code += jmp_bytes
    return base + local_reloc


def test_cmp_dword_scaled_imm32_sets_flags_from_a_4byte_array_element():
    import ctypes as _ct
    arr = (_ct.c_uint32 * 3)(10, 20, 30)
    base_addr = _ct.addressof(arr)
    # eax = 1 if arr[index] == expected else 0, via jcc off the SIB compare.
    for index, expected, matches in [(0, 10, True), (1, 10, False), (2, 30, True)]:
        code = bytearray()
        code += asm.mov_reg_imm64("rbx", base_addr)
        code += asm.mov_reg_imm64("rcx", index)
        code += asm.cmp_dword_scaled_imm32("rbx", "rcx", 4, expected)
        jne_reloc = _emit_jcc(code, "ne")
        code += asm.mov_reg_imm64("rax", 1)
        jmp_reloc = _emit_jmp(code)
        set_false_offset = len(code)
        code += asm.mov_reg_imm64("rax", 0)
        end_offset = len(code)
        _patch_rel32(code, jne_reloc, set_false_offset)
        _patch_rel32(code, jmp_reloc, end_offset)
        got = _run_u64(bytes(code))
        assert got == (1 if matches else 0), f"index={index} expected={expected}: got {got}"


def test_test_reg_reg_detects_zero_for_every_tested_register():
    for reg in TESTED_REGS:
        for value, expect_zero in [(0, True), (1, False), (0xFFFFFFFF00000000, False)]:
            code = bytearray()
            code += asm.mov_reg_imm64(reg, value)
            code += asm.test_reg_reg(reg)
            jz_reloc = _emit_jcc(code, "z")
            code += asm.mov_reg_imm64("rax", 0)
            jmp_reloc = _emit_jmp(code)
            zero_branch = len(code)
            code += asm.mov_reg_imm64("rax", 1)
            end = len(code)
            _patch_rel32(code, jz_reloc, zero_branch)
            _patch_rel32(code, jmp_reloc, end)
            got = _run_u64(bytes(code))
            assert got == (1 if expect_zero else 0), f"{reg}={value:#x}: expected zero={expect_zero}, got {got}"


def test_cmp_reg_imm32_works_for_every_tested_register():
    for reg in TESTED_REGS:
        code = bytearray()
        if reg != "rcx":
            code += asm.mov_reg_reg(reg, "rcx")
        code += asm.cmp_reg_imm32(reg, 100)
        je_reloc = _emit_jcc(code, "e")
        code += asm.mov_reg_imm64("rax", 0)
        jmp_reloc = _emit_jmp(code)
        eq_branch = len(code)
        code += asm.mov_reg_imm64("rax", 1)
        end = len(code)
        _patch_rel32(code, je_reloc, eq_branch)
        _patch_rel32(code, jmp_reloc, end)
        assert _run_u64(bytes(code), a=100) == 1, f"{reg}: 100==100 should match"
        assert _run_u64(bytes(code), a=99) == 0, f"{reg}: 99==100 should not match"


def test_jmp_and_jcc_rel32_placeholders_patch_to_the_correct_target():
    code = bytearray()
    jmp_reloc = _emit_jmp(code)
    code += asm.mov_reg_imm64("rax", 0xDEAD)   # skipped
    target = len(code)
    code += asm.mov_reg_imm64("rax", 0xBEEF)
    _patch_rel32(code, jmp_reloc, target)
    assert _run_u64(bytes(code)) == 0xBEEF


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} x64_asm tests passed (executed as real machine code).")
