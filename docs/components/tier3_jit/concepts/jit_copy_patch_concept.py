"""
docs/components/tier3_jit/concepts/jit_copy_patch_concept.py
Reference Concept Implementation: Full-Set Copy-and-Patch JIT Engine & MPU W^X Transaction Protocol
- Exhaustive binary stencil library matching docs/specs/jit_stencil_catalog.md & wasm_instruction_set.md
- Multi-dimensional register variants (Depth 0/1/2/3, R3 mem_base/scratch, Callee-saved R4-R6, R8-R11)
- Direct relocation patching (RelocImm32, RelocBranch24, RelocOffset8, RelocApi)
- Hardware-enforced MPU W^X attribute switching protocol (RW_XN <-> RO_X)
- Comprehensive verification of all supported WASM opcodes
"""

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jit_assembler_constexpr_concept import Thumb2Assembler, Reg, Cond  # noqa: E402


class MPUAttribute:
    RO_X = "RO_X"      # Read-Only + Executable (Native code execution)
    RW_XN = "RW_XN"    # Read-Write + Non-Executable (Patching/Compilation)
    NO_ACCESS = "NO_ACCESS"


class MPUFault(Exception):
    pass


class Stencil:
    """Pre-compiled binary template with relocation hole descriptors and Thumb-2 disassembly."""
    def __init__(self, name: str, code: list[str], hex_bytes: str, reloc_offsets: dict[str, int]):
        self.name = name
        self.code = list(code)
        self.hex_bytes = hex_bytes
        self.reloc_offsets = dict(reloc_offsets)


_REG_NAME_TO_ENUM = {r.name.lower(): r for r in Reg}


class CopyPatchJITEngine:
    def __init__(self, cache_size: int = 4096):
        self.cache_size = cache_size
        self.code_cache: list[str] = ["NOP"] * cache_size
        # Real machine code, emitted in lockstep with code_cache. Everything else in
        # this file only ever manipulated the disassembly strings above -- compile_trace
        # never read a single stencil's hex_bytes, so nothing here could actually be
        # copied into memory and executed. byte_cache is the real output of the engine;
        # code_cache remains purely for human-readable inspection/logging.
        self.byte_cache: bytearray = bytearray(cache_size * 4)
        self.byte_write_pos: int = 0
        self.last_trace_byte_range: tuple[int, int] = (0, 0)
        self.mpu_attr: str = MPUAttribute.RO_X  # Default state: RO_X
        self.barrier_flushes: int = 0
        self.current_write_pos: int = 0

        # Exhaustive Stencil Library (Cortex-M33 AAPCS + JIT Register Map)
        # R0=ip, R1=stack_bot, R2=env, R3=spill/scratch, R4=TOS, R5=NOS, R6=NNOS, R7=FP, R8-R11=assignable pool
        self.stencils: dict[str, Stencil] = {
            # --- Prologue, Epilogue & Interop ---
            "prologue_full": Stencil(
                "prologue_full",
                ["PUSH.W {r4-r6, r8-r11, lr}"],
                "2D E9 70 4F",
                {}
            ),
            "epilogue_return": Stencil(
                "epilogue_return",
                ["POP.W {r4-r6, r8-r11, pc}"],
                "BD E8 70 8F",
                {}
            ),
            "fallback_interp": Stencil(
                "fallback_interp",
                ["POP.W {r4-r6, r8-r11, lr}", "BX r12"],
                "BD E8 70 4F 60 47",
                {}
            ),
            "external_call_stub": Stencil(
                "external_call_stub",
                ["PUSH {r0-r3, r12, lr}", "BL 0x00000000", "POP {r0-r3, r12, lr}"],
                "2D E9 0F 50 00 F0 00 F8 BD E8 0F 50",
                {"branch_off": 1}
            ),

            # --- Control Flow ---
            "unreachable": Stencil("unreachable", ["BKPT #0x00"], "00 BE", {}),
            "nop": Stencil("nop", [], "", {}),
            "br": Stencil("br", ["B.W 0x00000000"], "00 F0 00 B8", {"target": 0}),
            "br_if_d1": Stencil("br_if_d1", ["CMP r4, #0", "BNE.W 0x00000000"], "00 2C 00 F0 00 80", {"target": 1}),
            "select_d3": Stencil("select_d3", ["CMP r4, #0", "IT NE", "MOVNE r5, r6", "MOV r4, r5"], "00 2C 18 BF 35 46 2C 46", {}),

            # --- Constants ---
            "i32_const_d0": Stencil(
                "i32_const_d0",
                ["MOVW r4, #0x0000", "MOVT r4, #0x0000"],
                "40 F2 00 04 C0 F2 00 04",
                {"imm_lo": 0, "imm_hi": 1}
            ),
            "i32_const_d1": Stencil(
                "i32_const_d1",
                ["MOV r5, r4", "MOVW r4, #0x0000", "MOVT r4, #0x0000"],
                "25 46 40 F2 00 04 C0 F2 00 04",
                {"imm_lo": 1, "imm_hi": 2}
            ),
            "i64_const_d0": Stencil(
                "i64_const_d0",
                ["MOVW r4, #0x0000", "MOVT r4, #0x0000", "MOVW r5, #0x0000", "MOVT r5, #0x0000"],
                "40 F2 00 04 C0 F2 00 04 40 F2 00 05 C0 F2 00 05",
                {"imm32_lo": 0, "imm32_hi": 2}
            ),

            # --- Variables ---
            "local_get_d0": Stencil("local_get_d0", ["LDR r4, [r1, #0x00]"], "0C 68", {"offset": 0}),
            "local_set_d1": Stencil("local_set_d1", ["STR r4, [r1, #0x00]"], "0C 60", {"offset": 0}),
            "local_tee_d1": Stencil("local_tee_d1", ["STR r4, [r1, #0x00]"], "0C 60", {"offset": 0}),
            "global_get_d0": Stencil("global_get_d0", ["LDR.W r3, [r2, #0x0C]", "LDR.W r4, [r3, #0x00]"], "D2 F8 0C 30 D3 F8 00 40", {"offset": 1}),
            "global_set_d1": Stencil("global_set_d1", ["LDR.W r3, [r2, #0x0C]", "STR.W r4, [r3, #0x00]"], "D2 F8 0C 30 C3 F8 00 40", {"offset": 1}),

            # --- 32-bit Integer Arithmetic & Logic ---
            "i32_add_d2": Stencil("i32_add_d2", ["ADDS r4, r5, r4"], "2C 19", {}),
            "i32_sub_d2": Stencil("i32_sub_d2", ["SUBS r4, r5, r4"], "2C 1B", {}),
            "i32_mul_d2": Stencil("i32_mul_d2", ["MUL r4, r5, r4"], "05 FB 04 F4", {}),
            "i32_div_s_d2": Stencil("i32_div_s_d2", ["SDIV r4, r5, r4"], "95 FB F4 F4", {}),
            "i32_div_u_d2": Stencil("i32_div_u_d2", ["UDIV r4, r5, r4"], "B5 FB F4 F4", {}),
            "i32_rem_s_d2": Stencil("i32_rem_s_d2", ["SDIV r3, r5, r4", "MLS r4, r3, r4, r5"], "95 FB F4 F3 03 FB 14 54", {}),
            "i32_rem_u_d2": Stencil("i32_rem_u_d2", ["UDIV r3, r5, r4", "MLS r4, r3, r4, r5"], "B5 FB F4 F3 03 FB 14 54", {}),
            "i32_and_d2": Stencil("i32_and_d2", ["ANDS r4, r5, r4"], "2C 40", {}),
            "i32_or_d2": Stencil("i32_or_d2", ["ORRS r4, r5, r4"], "2C 43", {}),
            "i32_xor_d2": Stencil("i32_xor_d2", ["EORS r4, r5, r4"], "6C 40", {}),
            # NOTE: the 16-bit Thumb-1 2-operand ALU forms (LSLS/ASRS/LSRS/RORS Rdn,Rm)
            # compute Rdn = Rdn <op> Rm, i.e. dest and first operand MUST be the same
            # register. That makes "shift NOS by the amount in TOS while writing the
            # result to TOS" impossible to encode as a single 2-operand instruction
            # without either clobbering the wrong operand or adding an extra MOV. These
            # stencils use the 32-bit Thumb-2 3-operand shift-by-register form instead
            # (LSL.W/LSR.W/ASR.W/ROR.W Rd,Rn,Rm), which keeps Rn (value/NOS) and Rm
            # (amount/TOS) independent. See {ADR_TosCacheAsymmetry}.
            "i32_shl_d2": Stencil("i32_shl_d2", ["LSL.W r4, r5, r4"], "05 FA 04 F4", {}),
            "i32_shr_s_d2": Stencil("i32_shr_s_d2", ["ASR.W r4, r5, r4"], "45 FA 04 F4", {}),
            "i32_shr_u_d2": Stencil("i32_shr_u_d2", ["LSR.W r4, r5, r4"], "25 FA 04 F4", {}),
            "i32_rotl_d2": Stencil("i32_rotl_d2", ["RSB r3, r4, #32", "ROR.W r4, r5, r3"], "C4 F1 20 03 65 FA 03 F4", {}),
            "i32_rotr_d2": Stencil("i32_rotr_d2", ["ROR.W r4, r5, r4"], "65 FA 04 F4", {}),
            "i32_clz_d1": Stencil("i32_clz_d1", ["CLZ r4, r4"], "B4 FA 84 F4", {}),
            "i32_ctz_d1": Stencil("i32_ctz_d1", ["RBIT r4, r4", "CLZ r4, r4"], "94 FA A4 F4 B4 FA 84 F4", {}),

            # --- 32-bit Integer Comparisons ---
            "i32_eqz_d1": Stencil(
                "i32_eqz_d1",
                ["CMP r4, #0", "IT EQ", "MOVEQ r4, #1", "IT NE", "MOVNE r4, #0"],
                "00 2C 08 BF 01 24 18 BF 00 24",
                {},
            ),
            "i32_eq_d2": Stencil("i32_eq_d2", ["CMP r5, r4", "IT EQ", "MOVEQ r4, #1", "IT NE", "MOVNE r4, #0"], "A5 42 08 BF 01 24 18 BF 00 24", {}),
            "i32_ne_d2": Stencil("i32_ne_d2", ["CMP r5, r4", "IT NE", "MOVNE r4, #1", "IT EQ", "MOVEQ r4, #0"], "A5 42 18 BF 01 24 08 BF 00 24", {}),
            "i32_lt_s_d2": Stencil("i32_lt_s_d2", ["CMP r5, r4", "IT LT", "MOVLT r4, #1", "IT GE", "MOVGE r4, #0"], "A5 42 B8 BF 01 24 A8 BF 00 24", {}),
            "i32_lt_u_d2": Stencil("i32_lt_u_d2", ["CMP r5, r4", "IT LO", "MOVLO r4, #1", "IT HS", "MOVHS r4, #0"], "A5 42 38 BF 01 24 28 BF 00 24", {}),
            "i32_gt_s_d2": Stencil("i32_gt_s_d2", ["CMP r5, r4", "IT GT", "MOVGT r4, #1", "IT LE", "MOVLE r4, #0"], "A5 42 C8 BF 01 24 D8 BF 00 24", {}),
            "i32_gt_u_d2": Stencil("i32_gt_u_d2", ["CMP r5, r4", "IT HI", "MOVHI r4, #1", "IT LS", "MOVLS r4, #0"], "A5 42 88 BF 01 24 98 BF 00 24", {}),
            "i32_le_s_d2": Stencil("i32_le_s_d2", ["CMP r5, r4", "IT LE", "MOVLE r4, #1", "IT GT", "MOVGT r4, #0"], "A5 42 D8 BF 01 24 C8 BF 00 24", {}),
            "i32_le_u_d2": Stencil("i32_le_u_d2", ["CMP r5, r4", "IT LS", "MOVLS r4, #1", "IT HI", "MOVHI r4, #0"], "A5 42 98 BF 01 24 88 BF 00 24", {}),
            "i32_ge_s_d2": Stencil("i32_ge_s_d2", ["CMP r5, r4", "IT GE", "MOVGE r4, #1", "IT LT", "MOVLT r4, #0"], "A5 42 A8 BF 01 24 B8 BF 00 24", {}),
            "i32_ge_u_d2": Stencil("i32_ge_u_d2", ["CMP r5, r4", "IT HS", "MOVHS r4, #1", "IT LO", "MOVLO r4, #0"], "A5 42 28 BF 01 24 38 BF 00 24", {}),

            # --- Linear Memory Access with FastAddressCheck Boundary Mask (R3 = mem_base, R6 = mem_mask) ---
            "i32_load_r3": Stencil("i32_load_r3", ["ANDS r4, r6", "LDR.W r4, [r3, r4]"], "34 40 53 F8 04 40", {}),
            "i32_load8_s_r3": Stencil("i32_load8_s_r3", ["ANDS r4, r6", "LDRSB.W r4, [r3, r4]"], "34 40 13 F9 04 40", {}),
            "i32_load8_u_r3": Stencil("i32_load8_u_r3", ["ANDS r4, r6", "LDRB.W r4, [r3, r4]"], "34 40 13 F8 04 40", {}),
            "i32_load16_s_r3": Stencil("i32_load16_s_r3", ["ANDS r4, r6", "LDRSH.W r4, [r3, r4]"], "34 40 33 F9 04 40", {}),
            "i32_load16_u_r3": Stencil("i32_load16_u_r3", ["ANDS r4, r6", "LDRH.W r4, [r3, r4]"], "34 40 33 F8 04 40", {}),
            "i32_store_r3": Stencil("i32_store_r3", ["ANDS r5, r6", "STR.W r4, [r3, r5]"], "35 40 43 F8 05 40", {}),
            "i32_store8_r3": Stencil("i32_store8_r3", ["ANDS r5, r6", "STRB.W r4, [r3, r5]"], "35 40 03 F8 05 40", {}),
            "i32_store16_r3": Stencil("i32_store16_r3", ["ANDS r5, r6", "STRH.W r4, [r3, r5]"], "35 40 23 F8 05 40", {}),
            "memory_size_d0": Stencil("memory_size_d0", ["LDR.W r4, [r2, #0x04]"], "D2 F8 04 40", {}),
        }

    # --- MPU W^X Transaction Protocol ---

    def begin_jit_patch(self):
        """Switches JIT Code Cache MPU attribute to RW + XN."""
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_jit_patch(self):
        """Restores JIT Code Cache MPU attribute to RO + X with DSB & ISB barriers."""
        assert self.mpu_attr == MPUAttribute.RW_XN, "Must be in patching mode before commit"
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1

    def write_instruction(self, offset: int, instruction: str):
        """Hardware MPU write protection simulation."""
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: Attempted write to non-writable code memory")
        self.code_cache[offset] = instruction

    def execute_native(self, start_offset: int, num_instructions: int) -> list[str]:
        """Hardware MPU fetch/exec protection simulation."""
        if self.mpu_attr != MPUAttribute.RO_X:
            raise MPUFault("W^X VIOLATION: Attempted instruction execution on non-executable memory")
        return self.code_cache[start_offset:start_offset + num_instructions]

    def _emit_bytes(self, data: bytes):
        """Copies real Thumb-2 machine code into byte_cache, honoring the same W^X gate."""
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: Attempted write to non-writable code memory")
        end = self.byte_write_pos + len(data)
        if end > len(self.byte_cache):
            raise MemoryError("JIT byte cache exhausted")
        self.byte_cache[self.byte_write_pos:end] = data
        self.byte_write_pos = end

    def execute_native_bytes(self, start_byte_offset: int, num_bytes: int) -> bytes:
        """Fetches real machine code bytes for external execution (e.g. a CPU emulator)."""
        if self.mpu_attr != MPUAttribute.RO_X:
            raise MPUFault("W^X VIOLATION: Attempted instruction execution on non-executable memory")
        return bytes(self.byte_cache[start_byte_offset:start_byte_offset + num_bytes])

    # --- Full Copy-and-Patch Compilation ---

    def compile_trace(
        self,
        wasm_ops: list[tuple[str, Any]],
        exit_kind: str = "return",
        dirty_spills: list[tuple[str, int]] | None = None
    ) -> tuple[int, int]:
        """
        Batches stencil copy & relocation patching inside a single W^X transaction.
        Flushes all dirty spilled variables (TOS/NOS, registers) to unified stack before POP/BX.
        Returns (start_offset, total_instructions).
        """
        start_offset = self.current_write_pos
        start_byte_offset = self.byte_write_pos
        dirty_spills = dirty_spills or []
        asm = Thumb2Assembler()

        def emit(inst: str, data: bytes):
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1
            self._emit_bytes(data)

        def emit_stencil(st: "Stencil"):
            raw = bytes.fromhex(st.hex_bytes.replace(" ", "")) if st.hex_bytes else b""
            # A stencil's disassembly can list several logical instructions (e.g. the
            # 2-instruction fallback epilogue) that together correspond to one
            # contiguous hex_bytes run; only the first written string gets the whole
            # byte payload so the byte and instruction cursors don't double-count it.
            if not st.code:
                return
            self.write_instruction(self.current_write_pos, st.code[0])
            self.current_write_pos += 1
            self._emit_bytes(raw)
            for inst in st.code[1:]:
                self.write_instruction(self.current_write_pos, inst)
                self.current_write_pos += 1

        # 1. Begin W^X Transaction (RW + XN)
        self.begin_jit_patch()

        # 2. Emit Full Callee-saved Prologue
        emit_stencil(self.stencils["prologue_full"])

        # 3. Emit WASM Ops with Relocation Patching
        for op, arg in wasm_ops:
            if op == "i32.const":
                imm = int(arg) & 0xFFFFFFFF
                emit(f"MOVW r4, #{imm & 0xFFFF}", asm.movw(Reg.R4, imm & 0xFFFF))
                emit(f"MOVT r4, #{(imm >> 16) & 0xFFFF}", asm.movt(Reg.R4, (imm >> 16) & 0xFFFF))
            elif op == "local.get":
                off = int(arg)
                emit(f"LDR r4, [r1, #{off}]", asm.ldr_imm(Reg.R4, Reg.R1, off))
            elif op == "local.set":
                off = int(arg)
                emit(f"STR r4, [r1, #{off}]", asm.str_imm(Reg.R4, Reg.R1, off))
            elif op == "br_if":
                # Target address is not resolvable inside a single compile_trace call
                # (it may be a forward label in a not-yet-compiled trace), so the branch
                # displacement is left as an unresolved relocation hole (offset 0), same
                # as external_call's BL below -- both need a real patch pass after the
                # target address is known, which is outside this concept engine's scope.
                emit("CMP r4, #0", asm.cmp_imm8(Reg.R4, 0))
                target_pc = int(arg)
                emit(f"BNE.W 0x{target_pc:08X}", asm.b_cond_w(Cond.NE, 0))
            elif op == "external_call":
                func_name = str(arg)
                call_mask = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 12)
                emit("PUSH {r0-r3, r12, lr}", asm.push_w(reg_mask=call_mask, push_lr=True))
                emit(f"BL {func_name}", asm.bl(0))  # relocation hole: patched after linking
                emit("POP {r0-r3, r12, lr}", asm.pop_w(reg_mask=call_mask, pop_lr=True))
            else:
                # Direct stencil mapping
                stencil_key = op.replace(".", "_") + "_d2"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_") + "_d1"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_") + "_r3"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_")

                if stencil_key in self.stencils:
                    emit_stencil(self.stencils[stencil_key])
                else:
                    raise ValueError(f"Unsupported stencil opcode: {op}")

        # 4. Emit Epilogue: Flush Dirty Spill Variables before POP
        # The 16-bit STR (imm5) form only encodes low registers (R0-R7); the
        # assignable pool's high half (R8-R11) needs the 32-bit STR.W (imm12) form.
        for reg, stack_off in dirty_spills:
            reg_enum = _REG_NAME_TO_ENUM[reg.lower()]
            if reg_enum <= Reg.R7:
                emit(f"STR {reg}, [r1, #{stack_off}]", asm.str_imm(reg_enum, Reg.R1, stack_off))
            else:
                emit(f"STR {reg}, [r1, #{stack_off}]", asm.str_w_imm12(reg_enum, Reg.R1, stack_off))

        if exit_kind == "return":
            emit_stencil(self.stencils["epilogue_return"])
        elif exit_kind == "fallback":
            emit_stencil(self.stencils["fallback_interp"])

        # 5. Commit W^X Transaction (RO + X + Barriers)
        self.commit_jit_patch()

        total_emitted = self.current_write_pos - start_offset
        self.last_trace_byte_range = (start_byte_offset, self.byte_write_pos - start_byte_offset)
        return (start_offset, total_emitted)


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================

def test_full_stencil_library_coverage():
    """Verify all opcodes in the stencil catalog have valid Thumb-2 code and hex bytes."""
    engine = CopyPatchJITEngine()
    assert len(engine.stencils) >= 35, f"Expected full stencil library, got {len(engine.stencils)}"

    hex_digits = set("0123456789ABCDEFabcdef")
    for name, st in engine.stencils.items():
        if st.code:
            assert st.hex_bytes, f"Stencil {name} has disassembly but no hex byte definition"
        for tok in st.hex_bytes.split():
            assert len(tok) == 2 and set(tok) <= hex_digits, \
                f"Stencil {name} hex_bytes token {tok!r} is not a valid byte"


def test_stencil_catalog_matches_assembler():
    """Cross-file check: catalog hex_bytes must equal the real Thumb2Assembler's output.

    Without this, jit_assembler_constexpr_concept.py and this file each hand-transcribe
    the same bytes independently, and nothing catches them drifting apart if only one
    is edited. This imports the actual assembler used elsewhere and re-derives the
    bytes for every stencil whose operands are fixed (not runtime-patched), instead of
    comparing against a second copy-pasted literal.
    """
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()

    def h(b: bytes) -> str:
        return " ".join(f"{x:02X}" for x in b)

    full_mask = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11)
    call_mask = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 12)

    checks = {
        "prologue_full": asm.push_w(reg_mask=full_mask, push_lr=True),
        "epilogue_return": asm.pop_w(reg_mask=full_mask, pop_pc=True),
        "fallback_interp": asm.pop_w(reg_mask=full_mask, pop_lr=True) + asm.bx(Reg.R12),
        "unreachable": asm.bkpt(0),
        "i32_const_d0": asm.movw(Reg.R4, 0) + asm.movt(Reg.R4, 0),
        "i64_const_d0": (asm.movw(Reg.R4, 0) + asm.movt(Reg.R4, 0)
                          + asm.movw(Reg.R5, 0) + asm.movt(Reg.R5, 0)),
        "local_get_d0": asm.ldr_imm(Reg.R4, Reg.R1, 0),
        "local_set_d1": asm.str_imm(Reg.R4, Reg.R1, 0),
        "local_tee_d1": asm.str_imm(Reg.R4, Reg.R1, 0),
        "global_get_d0": asm.ldr_w_imm12(Reg.R3, Reg.R2, 0x0C) + asm.ldr_w_imm12(Reg.R4, Reg.R3, 0),
        "global_set_d1": asm.ldr_w_imm12(Reg.R3, Reg.R2, 0x0C) + asm.str_w_imm12(Reg.R4, Reg.R3, 0),
        "memory_size_d0": asm.ldr_w_imm12(Reg.R4, Reg.R2, 4),
        "i32_add_d2": asm.adds_reg(Reg.R4, Reg.R5, Reg.R4),
        "i32_sub_d2": asm.subs_reg(Reg.R4, Reg.R5, Reg.R4),
        "i32_mul_d2": asm.mul(Reg.R4, Reg.R5, Reg.R4),
        "i32_div_s_d2": asm.sdiv(Reg.R4, Reg.R5, Reg.R4),
        "i32_div_u_d2": asm.udiv(Reg.R4, Reg.R5, Reg.R4),
        "i32_rem_s_d2": asm.sdiv(Reg.R3, Reg.R5, Reg.R4) + asm.mls(Reg.R4, Reg.R3, Reg.R4, Reg.R5),
        "i32_rem_u_d2": asm.udiv(Reg.R3, Reg.R5, Reg.R4) + asm.mls(Reg.R4, Reg.R3, Reg.R4, Reg.R5),
        "i32_and_d2": asm.ands_reg(Reg.R4, Reg.R5),
        "i32_or_d2": asm.orrs_reg(Reg.R4, Reg.R5),
        "i32_xor_d2": asm.eors_reg(Reg.R4, Reg.R5),
        "i32_shl_d2": asm.lsl_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_shr_s_d2": asm.asr_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_shr_u_d2": asm.lsr_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_rotr_d2": asm.ror_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_clz_d1": asm.clz(Reg.R4, Reg.R4),
        "i32_ctz_d1": asm.rbit(Reg.R4, Reg.R4) + asm.clz(Reg.R4, Reg.R4),
        "i32_load_r3": asm.ands_reg(Reg.R4, Reg.R6) + asm.ldr_w_reg(Reg.R4, Reg.R3, Reg.R4),
        "i32_store_r3": asm.ands_reg(Reg.R5, Reg.R6) + asm.str_w_reg(Reg.R4, Reg.R3, Reg.R5),
    }

    for name, encoded in checks.items():
        catalog_hex = engine.stencils[name].hex_bytes
        assert h(encoded) == catalog_hex, (
            f"Stencil '{name}' drifted from the assembler: "
            f"catalog={catalog_hex!r} assembler={h(encoded)!r}"
        )

    # i32_const_d1 prepends a plain register MOV ahead of the shared MOVW/MOVT pair.
    const_d1_expected = h(asm.mov_reg(Reg.R5, Reg.R4) + asm.movw(Reg.R4, 0) + asm.movt(Reg.R4, 0))
    assert const_d1_expected == engine.stencils["i32_const_d1"].hex_bytes, (
        f"Stencil 'i32_const_d1' drifted from the assembler: "
        f"catalog={engine.stencils['i32_const_d1'].hex_bytes!r} assembler={const_d1_expected!r}"
    )

    # external_call_stub has a relocation hole (the BL target) between two otherwise
    # fixed PUSH/POP halves; check only the parts that don't depend on the patched call site.
    push_expected = h(asm.push_w(reg_mask=call_mask, push_lr=True))
    pop_expected = h(asm.pop_w(reg_mask=call_mask, pop_lr=True))
    call_hex = engine.stencils["external_call_stub"].hex_bytes
    assert call_hex.startswith(push_expected), (
        f"Stencil 'external_call_stub' PUSH half drifted: catalog={call_hex!r} expected prefix={push_expected!r}"
    )
    assert call_hex.endswith(pop_expected), (
        f"Stencil 'external_call_stub' POP half drifted: catalog={call_hex!r} expected suffix={pop_expected!r}"
    )

    # i32_rotl_d2: RSB r3,r4,#32 (amount = 32 - shift) then ROR.W r4,r5,r3.
    rotl_expected = h(asm.rsb_imm(Reg.R3, Reg.R4, 32) + asm.ror_w(Reg.R4, Reg.R5, Reg.R3))
    assert rotl_expected == engine.stencils["i32_rotl_d2"].hex_bytes, (
        f"Stencil 'i32_rotl_d2' drifted from the assembler: "
        f"catalog={engine.stencils['i32_rotl_d2'].hex_bytes!r} assembler={rotl_expected!r}"
    )

    # i32_eqz_d1: CMP r4,#0 ; IT EQ ; MOVEQ r4,#1 ; IT NE ; MOVNE r4,#0.
    eqz_expected = h(
        asm.cmp_imm8(Reg.R4, 0)
        + asm.it(Cond.EQ, 0b1000) + asm.movs_imm8(Reg.R4, 1)
        + asm.it(Cond.NE, 0b1000) + asm.movs_imm8(Reg.R4, 0)
    )
    assert eqz_expected == engine.stencils["i32_eqz_d1"].hex_bytes, (
        f"Stencil 'i32_eqz_d1' drifted from the assembler: "
        f"catalog={engine.stencils['i32_eqz_d1'].hex_bytes!r} assembler={eqz_expected!r}"
    )


def test_arithmetic_and_logic_traces():
    engine = CopyPatchJITEngine()
    ops = [
        ("i32.const", 100),
        ("local.get", 4),
        ("i32.add", None),
        ("i32.sub", None),
        ("i32.mul", None),
        ("i32.div_s", None),
        ("i32.rem_s", None),
        ("i32.and", None),
        ("i32.or", None),
        ("i32.xor", None),
        ("i32.shl", None),
        ("i32.clz", None),
        ("i32.ctz", None),
        ("i32.eqz", None),
        ("i32.eq", None),
        ("i32.lt_s", None),
        ("i32.load", None),
        ("i32.store", None),
        ("local.set", 4),
    ]

    start_pos, count = engine.compile_trace(ops)
    assert count > 20
    assert engine.mpu_attr == MPUAttribute.RO_X
    assert engine.barrier_flushes == 1

    code = engine.execute_native(start_pos, count)
    assert code[0] == "PUSH.W {r4-r6, r8-r11, lr}"
    assert "MOVW r4, #100" in code[1]
    assert code[-1] == "POP.W {r4-r6, r8-r11, pc}"


def test_external_aapcs_call_stub():
    """Verify external C/C++ function call preserves Callee-saved while saving Caller-saved."""
    engine = CopyPatchJITEngine()
    ops = [
        ("i32.const", 1),
        ("external_call", "wasi_fd_write"),
        ("local.set", 0),
    ]

    start_pos, count = engine.compile_trace(ops)
    code = engine.execute_native(start_pos, count)

    # Check caller-saved preservation around external call
    assert "PUSH {r0-r3, r12, lr}" in code
    assert "BL wasi_fd_write" in code
    assert "POP {r0-r3, r12, lr}" in code


def test_cps_shared_registers_never_clobbered():
    """ADR_TosCacheAsymmetry: Shared R0/R1/R2 are never clobbered by trace ALU/loads."""
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 42), ("i32.add", None), ("i32.load", None)]
    start_pos, count = engine.compile_trace(ops)
    code = engine.execute_native(start_pos, count)

    for inst in code:
        mnemonic, _, operands = inst.partition(" ")
        if mnemonic in ("STR", "STR.W", "STRB.W", "STRH.W", "BX", "PUSH", "PUSH.W", "POP", "POP.W", "CMP", "BNE.W", "BL"):
            continue
        dest = operands.split(",")[0].strip()
        assert dest not in ("r0", "r1", "r2", "R0", "R1", "R2"), \
            f"Instruction '{inst}' illegal write to shared CPS register"


def test_epilogue_spill_variable_flush():
    """Verify that dirty spill variables (TOS/NOS, registers) are flushed to stack before POP/BX."""
    engine = CopyPatchJITEngine()
    ops = [
        ("i32.const", 10),
        ("local.get", 4),
        ("i32.add", None),
    ]

    # Compile with dirty spills: R4 (TOS) to stack offset 0, R8 (local[0]) to stack offset 8
    start_pos, count = engine.compile_trace(
        ops,
        exit_kind="fallback",
        dirty_spills=[("r4", 0), ("r8", 8)]
    )
    code = engine.execute_native(start_pos, count)

    # Check spill flush STR instructions before POP
    assert "STR r4, [r1, #0]" in code
    assert "STR r8, [r1, #8]" in code
    assert "POP.W {r4-r6, r8-r11, lr}" in code
    assert "BX r12" in code


def test_mpu_wx_protection():
    engine = CopyPatchJITEngine()
    try:
        engine.write_instruction(0, "ILLEGAL")
        assert False, "Should raise MPUFault"
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)


if __name__ == "__main__":
    test_full_stencil_library_coverage()
    test_stencil_catalog_matches_assembler()
    test_arithmetic_and_logic_traces()
    test_external_aapcs_call_stub()
    test_epilogue_spill_variable_flush()
    test_cps_shared_registers_never_clobbered()
    test_mpu_wx_protection()
    print("[PASS] All JIT Copy-and-Patch Full-Set concept tests passed successfully.")
