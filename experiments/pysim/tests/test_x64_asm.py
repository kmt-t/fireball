"""


experiments/pysim/test_x64_asm.py





Spec-first tests for x64_asm.py: every encoder is assembled into a real


executable buffer and run on the CPU, never just re-derived by hand a


second time. Supports Windows x64 ABI and Linux System V AMD64 ABI.


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

import sys

from pathlib import Path


import sys


from pathlib import Path


import sys


from pathlib import Path


import ctypes


import sys


import x64_asm as asm


from exec_memory import ExecutableBuffer


IS_WINDOWS = sys.platform == "win32"


ARG0 = "rcx" if IS_WINDOWS else "rdi"


ARG1 = "rdx" if IS_WINDOWS else "rsi"


TESTED_REGS = [
    "rax",
    "rbx",
    "rcx",
    "rdx",
    "r8",
    "r9",
    "r10",
    "r11",
    "r12",
    "r13",
    "r14",
    "r15",
]


_CALLEE_SAVED = ["rbx", "r12", "r13", "r14", "r15"]


if IS_WINDOWS:
    _CALLEE_SAVED = ["rbx", "r12", "r13", "r14", "r15", "rdi", "rsi"]


else:
    _CALLEE_SAVED = ["rbx", "r12", "r13", "r14", "r15", "rbp"]


def _wrap(body: bytes) -> bytes:
    """Wraps `body` with save/restore of callee-saved registers."""

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
    """Runs `body` (ARG0 = pointer to output buffer) and returns contents."""

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

        if reg != ARG0:
            code += asm.mov_reg_reg(reg, ARG0)

        code += asm.push_reg(reg)

        code += asm.mov_reg_imm64(reg, 0xDEADBEEFCAFEBABE)

        code += asm.pop_reg(reg)

        if reg != "rax":
            code += asm.mov_reg_reg("rax", reg)

        got = _run_u64(bytes(code), a=0x1122334455667788)

        assert got == 0x1122334455667788, (
            f"push/pop round-trip failed for {reg}: got {got:#x}"
        )


def test_mov_reg_reg_moves_the_full_64_bits_between_every_pair():

    value = 0x0123456789ABCDEF

    for src in TESTED_REGS:
        for dst in TESTED_REGS:
            if src == dst:
                continue

            code = bytearray()

            if src != ARG0:
                code += asm.mov_reg_reg(src, ARG0)

            code += asm.mov_reg_reg(dst, src)

            if dst != "rax":
                code += asm.mov_reg_reg("rax", dst)

            got = _run_u64(bytes(code), a=value)

            assert got == value, (
                f"mov {dst}, {src} did not carry the value (got {got:#x})"
            )


def test_mov_reg_imm64_loads_every_bit_of_a_64_bit_immediate():

    interesting = [
        0,
        1,
        0xFFFFFFFFFFFFFFFF,
        0x8000000000000000,
        0x0123456789ABCDEF,
        0xFEDCBA9876543210,
    ]

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

        code += asm.sub_rsp_imm8(disp + 8)

        code += asm.mov_store_rsp_disp32(disp, ARG0)

        code += asm.mov_load_rsp_disp32("rax", disp)

        code += asm.add_rsp_imm8(disp + 8)

        got = _run_u64(bytes(code), a=0xAABBCCDD11223344)

        assert got == 0xAABBCCDD11223344, (
            f"store/load round-trip at disp={disp} got {got:#x}"
        )


def test_mov_store_rsp_disp32_uses_a_distinct_register_per_slot_without_aliasing():

    code = bytearray()

    code += asm.sub_rsp_imm8(56)

    code += asm.mov_reg_reg("r13", ARG0)

    code += asm.mov_reg_imm64("r14", 0x2222222222222222)

    code += asm.mov_reg_imm64("r15", 0x3333333333333333)

    code += asm.mov_store_rsp_disp32(32, "r13")

    code += asm.mov_store_rsp_disp32(40, "r14")

    code += asm.mov_store_rsp_disp32(48, "r15")

    code += asm.mov_load_rsp_disp32("rax", 32)

    code += asm.mov_load_rsp_disp32("rbx", 40)

    code += asm.mov_load_rsp_disp32("rdx", 48)

    code += bytes((0x48, 0x31, 0xD8))  # xor rax, rbx

    code += bytes((0x48, 0x31, 0xD0))  # xor rax, rdx

    code += asm.add_rsp_imm8(56)

    got = _run_u64(bytes(code), a=0x1111111111111111)

    expected = 0x1111111111111111 ^ 0x2222222222222222 ^ 0x3333333333333333

    assert got == expected


def test_and_rsp_imm8_aligns_the_stack_pointer_down_to_16():

    code = bytearray()

    code += asm.mov_reg_reg("r12", ARG0)

    code += asm.mov_reg_reg("r13", "rsp")

    code += asm.sub_rsp_imm8(7)

    code += asm.mov_reg_reg("rax", "rsp")

    code += asm.and_rsp_imm8(-16 & 0xFF)

    code += asm.mov_reg_reg("rbx", "rsp")

    code += asm.mov_reg_reg("rsp", "r13")

    code += bytes((0x49, 0x89, 0x04, 0x24))

    code += bytes((0x49, 0x89, 0x5C, 0x24, 0x08))

    before, after = _run_void_ptr_arg(bytes(code), out_len=2)

    assert after % 16 == 0, f"and rsp,-16 left rsp={after:#x}, not 16-aligned"

    assert after <= before


def test_call_reg_performs_a_real_indirect_call_and_returns_here():

    callee_code = asm.mov_reg_reg("rax", ARG0) + bytes((0x48, 0x01, 0xC0)) + asm.ret()

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

        code += asm.mov_reg_imm64("rcx", i)

        code += asm.mov_load_scaled("rax", "rbx", "rcx", scale=8)

        got = _run_u64(bytes(code))

        assert got == expected, (
            f"scale=8 failed at idx {i}: got {got:#x}, expected {expected:#x}"
        )


def test_cmp_dword_scaled_imm32():

    import ctypes as _ct

    arr = (_ct.c_uint32 * 4)(10, 20, 30, 40)

    base_addr = _ct.addressof(arr)

    code = bytearray()

    code += asm.mov_reg_imm64("rbx", base_addr)

    code += asm.mov_reg_imm64("rcx", 2)  # index 2 -> value 30

    code += asm.cmp_dword_scaled_imm32("rbx", "rcx", scale=4, imm32=30)

    # If equal, rax = 1, else 0

    code += bytes((0x0F, 0x94, 0xC0))  # sete al

    code += bytes((0x0F, 0xB6, 0xC0))  # movzx eax, al

    got = _run_u64(bytes(code))

    assert got == 1


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()

        print(f"[PASS] {t.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} x64 assembly encoder tests passed.")
