"""
docs/components/tier3_jit/concepts/jit_assembler_constexpr_concept.py
Reference Concept Implementation: Full-Set C++20 constexpr Thumb-2 Assembler DSL & Static Validator
- Type-safe Register Enums (R0-R15, Low Regs R0-R7, High Regs R8-R15)
- Compile-time instruction encoding & range validation (static_assert emulation)
- Full ARMv8-M Mainline (Cortex-M33) Thumb-1 (16-bit) & Thumb-2 (32-bit) instruction sets
- Encoder output checked against known-correct ARM reference bit patterns below.
  The actual cross-check against the jit_stencil_catalog byte strings used by the
  JIT engine lives in jit_copy_patch_concept.py (test_stencil_catalog_matches_assembler),
  which imports this module's Thumb2Assembler directly rather than duplicating literals.
"""

from enum import IntEnum
from typing import NamedTuple, Union
import struct


# ==============================================================================
# 1. Type-Safe Enums and Validation Constants
# ==============================================================================

class Reg(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4
    R5 = 5
    R6 = 6
    R7 = 7   # Frame Pointer (AAPCS)
    R8 = 8
    R9 = 9
    R10 = 10
    R11 = 11
    R12 = 12 # IP (Intra-procedure scratch)
    SP = 13  # R13
    LR = 14  # R14
    PC = 15  # R15


class Cond(IntEnum):
    EQ = 0b0000  # Equal
    NE = 0b0001  # Not equal
    CS = 0b0010  # Carry set / unsigned higher or same (HS)
    HS = 0b0010
    CC = 0b0011  # Carry clear / unsigned lower (LO)
    LO = 0b0011
    MI = 0b0100  # MInus / negative
    PL = 0b0101  # Plus / positive or zero
    VS = 0b0110  # Overflow
    VC = 0b0111  # No overflow
    HI = 0b1000  # Unsigned higher
    LS = 0b1001  # Unsigned lower or same
    GE = 0b1010  # Signed greater than or equal
    LT = 0b1011  # Signed less than
    GT = 0b1100  # Signed greater than
    LE = 0b1101  # Signed less than or equal
    AL = 0b1110  # Always


class AssemblerError(Exception):
    pass


# ==============================================================================
# 2. Instruction Encoding Helpers & Static Validation
# ==============================================================================

def _check_low_reg(reg: Reg, msg: str = "Register must be R0-R7 (Low Register)"):
    if reg > Reg.R7:
        raise AssemblerError(f"COMPILE-TIME ERROR: {msg}, got {reg.name}")


def _check_imm(val: int, bits: int, signed: bool = False, msg: str = "Immediate out of range"):
    if signed:
        min_v = -(1 << (bits - 1))
        max_v = (1 << (bits - 1)) - 1
        if not (min_v <= val <= max_v):
            raise AssemblerError(f"COMPILE-TIME ERROR: {msg} (signed {bits}-bit: {min_v}..{max_v}, got {val})")
    else:
        max_v = (1 << bits) - 1
        if not (0 <= val <= max_v):
            raise AssemblerError(f"COMPILE-TIME ERROR: {msg} (unsigned {bits}-bit: 0..{max_v}, got {val})")


# ==============================================================================
# 3. Full-Set Thumb-2 constexpr Assembler DSL Class
# ==============================================================================

class Thumb2Assembler:
    """
    Simulates C++20 constexpr Thumb-2 Assembler functions.
    All methods return byte arrays in Little-Endian byte order.
    """

    # --- 16-bit Thumb-1 Instructions ---

    @staticmethod
    def mov_reg(rd: Reg, rm: Reg) -> bytes:
        """MOV Rd, Rm (16-bit) -> 46xx"""
        d = 1 if rd >= Reg.R8 else 0
        rd_low = rd & 7
        code = 0x4600 | (d << 7) | (rm << 3) | rd_low
        return struct.pack("<H", code)

    @staticmethod
    def movs_imm8(rd: Reg, imm8: int) -> bytes:
        """MOVS Rd, #imm8 (16-bit) -> 2xxx"""
        _check_low_reg(rd)
        _check_imm(imm8, 8)
        code = 0x2000 | (rd << 8) | (imm8 & 0xFF)
        return struct.pack("<H", code)

    @staticmethod
    def adds_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """ADDS Rd, Rn, Rm (16-bit) -> 18xx"""
        _check_low_reg(rd)
        _check_low_reg(rn)
        _check_low_reg(rm)
        code = 0x1800 | (rm << 6) | (rn << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def subs_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """SUBS Rd, Rn, Rm (16-bit) -> 1Axx"""
        _check_low_reg(rd)
        _check_low_reg(rn)
        _check_low_reg(rm)
        code = 0x1A00 | (rm << 6) | (rn << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def ands_reg(rd: Reg, rm: Reg) -> bytes:
        """ANDS Rd, Rm (16-bit) -> 4000 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x4000 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def orrs_reg(rd: Reg, rm: Reg) -> bytes:
        """ORRS Rd, Rm (16-bit) -> 4300 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x4300 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def eors_reg(rd: Reg, rm: Reg) -> bytes:
        """EORS Rd, Rm (16-bit) -> 4040 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x4040 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def lsls_reg(rd: Reg, rm: Reg) -> bytes:
        """LSLS Rd, Rm (16-bit) -> 4080 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x4080 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def asrs_reg(rd: Reg, rm: Reg) -> bytes:
        """ASRS Rd, Rm (16-bit) -> 4100 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x4100 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def lsrs_reg(rd: Reg, rm: Reg) -> bytes:
        """LSRS Rd, Rm (16-bit) -> 40C0 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x40C0 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def rors_reg(rd: Reg, rm: Reg) -> bytes:
        """RORS Rd, Rm (16-bit) -> 41C0 | (rm << 3) | rd"""
        _check_low_reg(rd)
        _check_low_reg(rm)
        code = 0x41C0 | (rm << 3) | rd
        return struct.pack("<H", code)

    @staticmethod
    def cmp_reg(rn: Reg, rm: Reg) -> bytes:
        """CMP Rn, Rm (16-bit) -> 4280 | (rm << 3) | rn"""
        _check_low_reg(rn)
        _check_low_reg(rm)
        code = 0x4280 | (rm << 3) | rn
        return struct.pack("<H", code)

    @staticmethod
    def cmp_reg_t2(rn: Reg, rm: Reg) -> bytes:
        """CMP Rn, Rm (16-bit, T2 encoding) -> 4500 | (N << 7) | (Rm << 3) | rn_low3

        Unlike cmp_reg (T1), Rm may be any register R0-R14, at the cost of only
        3 bits of Rn (extended to the full register via the N bit) -- the ARM-
        documented form for comparing a low register against a high one. Per the
        architecture reference this encoding is UNPREDICTABLE if both Rn and Rm
        are low registers (use cmp_reg/T1 for that case) or if either is PC."""
        assert rn != Reg.PC and rm != Reg.PC, "CMP (T2) does not accept PC"
        assert not (rn < Reg.R8 and rm < Reg.R8), "use cmp_reg (T1) when both operands are low registers"
        n = 1 if rn >= Reg.R8 else 0
        code = 0x4500 | (n << 7) | (rm << 3) | (rn & 7)
        return struct.pack("<H", code)

    @staticmethod
    def cmp_imm8(rn: Reg, imm8: int) -> bytes:
        """CMP Rn, #imm8 (16-bit) -> 2800 | (rn << 8) | imm8"""
        _check_low_reg(rn)
        _check_imm(imm8, 8)
        code = 0x2800 | (rn << 8) | imm8
        return struct.pack("<H", code)

    @staticmethod
    def ldr_imm(rt: Reg, rn: Reg, imm_offset: int) -> bytes:
        """LDR Rt, [Rn, #imm] (16-bit, word aligned, imm multiple of 4, up to 124)"""
        _check_low_reg(rt)
        _check_low_reg(rn)
        if imm_offset % 4 != 0:
            raise AssemblerError(f"Immediate offset must be multiple of 4, got {imm_offset}")
        imm5 = imm_offset // 4
        _check_imm(imm5, 5)
        code = 0x6800 | (imm5 << 6) | (rn << 3) | rt
        return struct.pack("<H", code)

    @staticmethod
    def str_imm(rt: Reg, rn: Reg, imm_offset: int) -> bytes:
        """STR Rt, [Rn, #imm] (16-bit, word aligned, imm multiple of 4, up to 124)"""
        _check_low_reg(rt)
        _check_low_reg(rn)
        if imm_offset % 4 != 0:
            raise AssemblerError(f"Immediate offset must be multiple of 4, got {imm_offset}")
        imm5 = imm_offset // 4
        _check_imm(imm5, 5)
        code = 0x6000 | (imm5 << 6) | (rn << 3) | rt
        return struct.pack("<H", code)

    @staticmethod
    def it(firstcond: Cond, mask: int = 0b1000) -> bytes:
        """IT condition block (16-bit: BF00 | (cond << 4) | mask)"""
        code = 0xBF00 | (int(firstcond) << 4) | (mask & 0xF)
        return struct.pack("<H", code)

    @staticmethod
    def bx(rm: Reg) -> bytes:
        """BX Rm (16-bit) -> 4700 | (rm << 3)"""
        code = 0x4700 | (rm << 3)
        return struct.pack("<H", code)

    @staticmethod
    def bkpt(imm8: int = 0) -> bytes:
        """BKPT #imm8 (16-bit) -> BE00 | imm8"""
        _check_imm(imm8, 8)
        code = 0xBE00 | (imm8 & 0xFF)
        return struct.pack("<H", code)

    # --- 32-bit Thumb-2 Instructions ---

    @staticmethod
    def movw(rd: Reg, imm16: int) -> bytes:
        """MOVW Rd, #imm16 (32-bit Thumb-2)"""
        _check_imm(imm16, 16)
        imm4 = (imm16 >> 12) & 0xF
        i = (imm16 >> 11) & 0x1
        imm3 = (imm16 >> 8) & 0x7
        imm8 = imm16 & 0xFF

        hw1 = 0xF240 | (i << 10) | imm4
        hw2 = (imm3 << 12) | (rd << 8) | imm8
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def movt(rd: Reg, imm16: int) -> bytes:
        """MOVT Rd, #imm16 (32-bit Thumb-2)"""
        _check_imm(imm16, 16)
        imm4 = (imm16 >> 12) & 0xF
        i = (imm16 >> 11) & 0x1
        imm3 = (imm16 >> 8) & 0x7
        imm8 = imm16 & 0xFF

        hw1 = 0xF2C0 | (i << 10) | imm4
        hw2 = (imm3 << 12) | (rd << 8) | imm8
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def mul(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """MUL Rd, Rn, Rm (32-bit Thumb-2: FB00 F000)"""
        hw1 = 0xFB00 | rn
        hw2 = 0xF000 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def sdiv(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """SDIV Rd, Rn, Rm (32-bit Thumb-2: FB90 F0F0)"""
        hw1 = 0xFB90 | rn
        hw2 = 0xF0F0 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def udiv(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """UDIV Rd, Rn, Rm (32-bit Thumb-2: FBB0 F0F0)"""
        hw1 = 0xFBB0 | rn
        hw2 = 0xF0F0 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def mls(rd: Reg, rn: Reg, rm: Reg, ra: Reg) -> bytes:
        """MLS Rd, Rn, Rm, Ra (32-bit Thumb-2: FB00 0010 | (ra << 12))

        Shares the MUL/MLA family encoding (Ra:Rd:op:Rm in the second halfword);
        the op nibble (bits[7:4]) must be 0001 to select MLS instead of MLA's 0000.
        """
        hw1 = 0xFB00 | rn
        hw2 = (ra << 12) | (rd << 8) | 0x10 | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def _shift_reg_w(op: int, rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """Shared encoder for LSL/LSR/ASR/ROR (register), 32-bit Thumb-2 3-operand form.

        Rd = Rn <op> Rm, op in {0:LSL, 1:LSR, 2:ASR, 3:ROR}. Unlike the 16-bit
        Thumb-1 2-operand ALU forms (ANDS/EORS/LSLS/.../RORS), this is a genuine
        3-register instruction: it does not require the shift amount and the
        shifted operand to share a register, so it can express "Rn shifted by
        Rm" without clobbering either input via an extra MOV.
        """
        hw1 = 0xFA00 | (op << 5) | rn
        hw2 = 0xF000 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def lsl_w(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """LSL.W Rd, Rn, Rm (32-bit Thumb-2, register shift amount)"""
        return Thumb2Assembler._shift_reg_w(0, rd, rn, rm)

    @staticmethod
    def lsr_w(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """LSR.W Rd, Rn, Rm (32-bit Thumb-2, register shift amount)"""
        return Thumb2Assembler._shift_reg_w(1, rd, rn, rm)

    @staticmethod
    def asr_w(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """ASR.W Rd, Rn, Rm (32-bit Thumb-2, register shift amount)"""
        return Thumb2Assembler._shift_reg_w(2, rd, rn, rm)

    @staticmethod
    def ror_w(rd: Reg, rn: Reg, rm: Reg) -> bytes:
        """ROR.W Rd, Rn, Rm (32-bit Thumb-2, register shift amount)"""
        return Thumb2Assembler._shift_reg_w(3, rd, rn, rm)

    @staticmethod
    def rsb_imm(rd: Reg, rn: Reg, imm12: int) -> bytes:
        """RSB Rd, Rn, #imm12 (32-bit Thumb-2 reverse subtract, Rd = imm12 - Rn)"""
        _check_imm(imm12, 12)
        i = (imm12 >> 11) & 0x1
        imm3 = (imm12 >> 8) & 0x7
        imm8 = imm12 & 0xFF
        hw1 = 0xF1C0 | (i << 10) | rn
        hw2 = (imm3 << 12) | (rd << 8) | imm8
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def clz(rd: Reg, rm: Reg) -> bytes:
        """CLZ Rd, Rm (32-bit Thumb-2: FAB0 F080)"""
        hw1 = 0xFAB0 | rm
        hw2 = 0xF080 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def rbit(rd: Reg, rm: Reg) -> bytes:
        """RBIT Rd, Rm (32-bit Thumb-2: FA90 F0A0)"""
        hw1 = 0xFA90 | rm
        hw2 = 0xF0A0 | (rd << 8) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def ldr_w_reg(rt: Reg, rn: Reg, rm: Reg, lsl: int = 0) -> bytes:
        """LDR.W Rt, [Rn, Rm, LSL #lsl] (32-bit Thumb-2: F850 0000)"""
        _check_imm(lsl, 2)
        hw1 = 0xF850 | rn
        hw2 = (rt << 12) | (lsl << 4) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def str_w_reg(rt: Reg, rn: Reg, rm: Reg, lsl: int = 0) -> bytes:
        """STR.W Rt, [Rn, Rm, LSL #lsl] (32-bit Thumb-2: F840 0000)"""
        _check_imm(lsl, 2)
        hw1 = 0xF840 | rn
        hw2 = (rt << 12) | (lsl << 4) | rm
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def ldr_w_imm12(rt: Reg, rn: Reg, imm12: int) -> bytes:
        """LDR.W Rt, [Rn, #imm12] (32-bit Thumb-2: F8D0 0000)"""
        _check_imm(imm12, 12)
        hw1 = 0xF8D0 | rn
        hw2 = (rt << 12) | imm12
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def str_w_imm12(rt: Reg, rn: Reg, imm12: int) -> bytes:
        """STR.W Rt, [Rn, #imm12] (32-bit Thumb-2: F8C0 0000)"""
        _check_imm(imm12, 12)
        hw1 = 0xF8C0 | rn
        hw2 = (rt << 12) | imm12
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def push_w(reg_mask: int, push_lr: bool = False) -> bytes:
        """PUSH.W {registers, lr} (32-bit Thumb-2: STMDB SP!, {registers, lr})"""
        m = 1 if push_lr else 0
        hw1 = 0xE92D
        hw2 = (m << 14) | (reg_mask & 0x1FFF)
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def pop_w(reg_mask: int, pop_pc: bool = False, pop_lr: bool = False) -> bytes:
        """POP.W {registers, pc/lr} (32-bit Thumb-2: LDMIA SP!, {registers, pc/lr})"""
        p = 1 if pop_pc else 0
        m = 1 if pop_lr else 0
        hw1 = 0xE8BD
        hw2 = (p << 15) | (m << 14) | (reg_mask & 0x1FFF)
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def b_w(offset: int) -> bytes:
        """B.W offset (32-bit Thumb-2 unconditional relative branch, +/- 16MB)"""
        _check_imm(offset, 25, signed=True)
        # offset is relative to PC+4
        imm25 = offset & 0x1FFFFFF
        s = (imm25 >> 24) & 1
        i1 = (imm25 >> 23) & 1
        i2 = (imm25 >> 22) & 1
        imm10 = (imm25 >> 12) & 0x3FF
        imm11 = (imm25 >> 1) & 0x7FF

        j1 = (~i1 ^ s) & 1
        j2 = (~i2 ^ s) & 1

        hw1 = 0xF000 | (s << 10) | imm10
        hw2 = 0x9000 | (j1 << 13) | (j2 << 11) | imm11
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def b_cond_w(cond: Cond, offset: int) -> bytes:
        """B<cond>.W offset (32-bit Thumb-2 conditional relative branch)"""
        _check_imm(offset, 21, signed=True)
        imm21 = offset & 0x1FFFFF
        s = (imm21 >> 20) & 1
        j2 = (imm21 >> 19) & 1
        j1 = (imm21 >> 18) & 1
        imm6 = (imm21 >> 12) & 0x3F
        imm11 = (imm21 >> 1) & 0x7FF

        hw1 = 0xF000 | (s << 10) | (int(cond) << 6) | imm6
        hw2 = 0x8000 | (j1 << 13) | (j2 << 11) | imm11
        return struct.pack("<HH", hw1, hw2)

    @staticmethod
    def bl(offset: int) -> bytes:
        """BL offset (32-bit Thumb-2 branch with link)"""
        _check_imm(offset, 25, signed=True)
        imm25 = offset & 0x1FFFFFF
        s = (imm25 >> 24) & 1
        i1 = (imm25 >> 23) & 1
        i2 = (imm25 >> 22) & 1
        imm10 = (imm25 >> 12) & 0x3FF
        imm11 = (imm25 >> 1) & 0x7FF

        j1 = (~i1 ^ s) & 1
        j2 = (~i2 ^ s) & 1

        hw1 = 0xF000 | (s << 10) | imm10
        hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | imm11
        return struct.pack("<HH", hw1, hw2)


# ==============================================================================
# 4. Simulation & Verification Tests (Verification against Stencil Catalog)
# ==============================================================================

def _hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def test_compile_time_range_validation():
    """Verify that illegal registers and immediate overflows trigger compile-time errors."""
    asm = Thumb2Assembler()

    # 1. Low register validation on Thumb-1 instruction
    try:
        asm.adds_reg(Reg.R8, Reg.R1, Reg.R2)
        assert False, "Should raise AssemblerError for R8 on 16-bit adds_reg"
    except AssemblerError as e:
        assert "Low Register" in str(e)

    # 2. Immediate overflow on movs_imm8 (8-bit max 255)
    try:
        asm.movs_imm8(Reg.R0, 256)
        assert False, "Should raise AssemblerError for imm8 = 256"
    except AssemblerError as e:
        assert "out of range" in str(e)

    # 3. Word-alignment check on LDR/STR
    try:
        asm.ldr_imm(Reg.R0, Reg.R1, imm_offset=3)
        assert False, "Should raise AssemblerError for unaligned offset 3"
    except AssemblerError as e:
        assert "multiple of 4" in str(e)


def test_known_thumb2_encoding_reference_values():
    """Verify the encoder against known-correct ARMv8-M Thumb-2 bit patterns.

    This only checks the assembler against manually-transcribed literals; it does
    NOT read jit_copy_patch_concept.py's stencil catalog, so it cannot by itself
    prove the two stay in sync. See test_stencil_catalog_matches_assembler() in
    jit_copy_patch_concept.py for the real cross-file check.
    """
    asm = Thumb2Assembler()

    # STENCIL_PROLOGUE_FULL: push {r4-r6, r8-r11, lr} -> 2D E9 70 4F
    # r4-r6 (mask 0x0070) | r8-r11 (mask 0x0F00) = 0x0F70, lr = True (bit 14: 0x4000) -> 0x4F70
    prologue = asm.push_w(reg_mask=(1<<4)|(1<<5)|(1<<6)|(1<<8)|(1<<9)|(1<<10)|(1<<11), push_lr=True)
    assert _hex(prologue) == "2D E9 70 4F", f"Got {_hex(prologue)}"

    # STENCIL_EPILOGUE_RETURN: pop {r4-r6, r8-r11, pc} -> BD E8 70 8F
    # r4-r6 | r8-r11 = 0x0F70, pc = True (bit 15: 0x8000) -> 0x8F70
    epilogue = asm.pop_w(reg_mask=(1<<4)|(1<<5)|(1<<6)|(1<<8)|(1<<9)|(1<<10)|(1<<11), pop_pc=True)
    assert _hex(epilogue) == "BD E8 70 8F", f"Got {_hex(epilogue)}"

    # STENCIL_I32_ADD_D2: adds r4, r5, r4 -> 2C 19
    add_d2 = asm.adds_reg(Reg.R4, Reg.R5, Reg.R4)
    assert _hex(add_d2) == "2C 19", f"Got {_hex(add_d2)}"

    # STENCIL_I32_SUB_D2: subs r4, r5, r4 -> 2C 1B
    sub_d2 = asm.subs_reg(Reg.R4, Reg.R5, Reg.R4)
    assert _hex(sub_d2) == "2C 1B", f"Got {_hex(sub_d2)}"

    # STENCIL_I32_MUL_D2: mul r4, r5, r4 -> 05 FB 04 F4
    mul_d2 = asm.mul(Reg.R4, Reg.R5, Reg.R4)
    assert _hex(mul_d2) == "05 FB 04 F4", f"Got {_hex(mul_d2)}"

    # STENCIL_I32_DIV_S_D2: sdiv r4, r5, r4 -> 95 FB F4 F4
    sdiv_d2 = asm.sdiv(Reg.R4, Reg.R5, Reg.R4)
    assert _hex(sdiv_d2) == "95 FB F4 F4", f"Got {_hex(sdiv_d2)}"

    # STENCIL_I32_DIV_U_D2: udiv r4, r5, r4 -> B5 FB F4 F4
    udiv_d2 = asm.udiv(Reg.R4, Reg.R5, Reg.R4)
    assert _hex(udiv_d2) == "B5 FB F4 F4", f"Got {_hex(udiv_d2)}"

    # STENCIL_I32_CLZ_D1: clz r4, r4 -> B4 FA 84 F4
    clz_d1 = asm.clz(Reg.R4, Reg.R4)
    assert _hex(clz_d1) == "B4 FA 84 F4", f"Got {_hex(clz_d1)}"

    # STENCIL_I32_CTZ_D1: rbit r4, r4; clz r4, r4 -> 94 FA A4 F4 B4 FA 84 F4
    ctz_d1 = asm.rbit(Reg.R4, Reg.R4) + asm.clz(Reg.R4, Reg.R4)
    assert _hex(ctz_d1) == "94 FA A4 F4 B4 FA 84 F4", f"Got {_hex(ctz_d1)}"

    # STENCIL_UNREACHABLE: bkpt #0 -> 00 BE
    unreachable = asm.bkpt(0)
    assert _hex(unreachable) == "00 BE", f"Got {_hex(unreachable)}"

    # STENCIL_LOCAL_GET_D0: ldr r4, [r1, #0] -> 0C 68
    ldr_d0 = asm.ldr_imm(Reg.R4, Reg.R1, 0)
    assert _hex(ldr_d0) == "0C 68", f"Got {_hex(ldr_d0)}"

    # STENCIL_LOCAL_SET_D1: str r4, [r1, #0] -> 0C 60
    str_d1 = asm.str_imm(Reg.R4, Reg.R1, 0)
    assert _hex(str_d1) == "0C 60", f"Got {_hex(str_d1)}"

    # CMP R0, R8 (T2, Rm high) -> 40 45 -- classic textbook reference value for this encoding
    cmp_t2_ref = asm.cmp_reg_t2(Reg.R0, Reg.R8)
    assert _hex(cmp_t2_ref) == "40 45", f"Got {_hex(cmp_t2_ref)}"

    # FastAddressCheck bounds check against mem_size pinned in R9 (R8=mem_base, R9=mem_size):
    # cmp r4, r9 -> 4C 45 ; cmp r5, r9 -> 4D 45
    cmp_r4_r9 = asm.cmp_reg_t2(Reg.R4, Reg.R9)
    assert _hex(cmp_r4_r9) == "4C 45", f"Got {_hex(cmp_r4_r9)}"
    cmp_r5_r9 = asm.cmp_reg_t2(Reg.R5, Reg.R9)
    assert _hex(cmp_r5_r9) == "4D 45", f"Got {_hex(cmp_r5_r9)}"


if __name__ == "__main__":
    test_compile_time_range_validation()
    test_known_thumb2_encoding_reference_values()
    print("[PASS] All Full-Set constexpr Thumb-2 Assembler tests and reference-value checks passed successfully.")
