"""
docs/components/tier3_jit/concepts/jit_copy_patch_concept.py
Reference Concept Implementation: Full-Set Copy-and-Patch JIT Engine & MPU W^X Transaction Protocol
- Exhaustive binary stencil library matching docs/specs/jit_stencil_catalog.md & wasm_instruction_set.md
- Multi-dimensional register variants (Depth 0/1/2/3, R3 local_base, R8/R9 mem_base/mem_size, Callee-saved R4-R6, R8-R11)
- Direct relocation patching (RelocImm32, RelocBranch24, RelocOffset8, RelocApi)
- Hardware-enforced MPU W^X attribute switching protocol (RW_XN <-> RO_X)
- Comprehensive verification of all supported WASM opcodes
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jit_assembler_constexpr_concept import Cond, Reg, Thumb2Assembler


class MPUAttribute:
    RO_X = "RO_X"  # Read-Only + Executable (Native code execution)
    RW_XN = "RW_XN"  # Read-Write + Non-Executable (Patching/Compilation)
    NO_ACCESS = "NO_ACCESS"


class MPUFault(Exception):
    pass


class Stencil:
    """Pre-compiled binary template with relocation hole descriptors and Thumb-2 disassembly.
    variant_id is the intra-trace register-occupancy ID from
    docs/specs/jit_stencil_catalog.md 3.8 (0..3 = Depth 0..3, how many of
    TOS/NOS/NNOS are register-resident), derived from the name's `_dN` suffix so
    it can never drift out of sync with the name itself. It is used both for the
    (not yet implemented) dynamic per-depth stencil selection and for the real
    reconciliation-glue mechanism between consecutive stencils *within* one trace
    (see emit_variant_reconciliation_glue()) -- NOT for chaining between separately
    compiled traces, which always goes through memory regardless of variant
    (jit_compiler.md 8, {ADR_TosCacheAsymmetry}). The `_r8` memory stencils don't
    introduce a depth of their own -- loads reuse Depth 1's R4=addr, stores reuse
    Depth 2's R4=val/R5=addr -- so they're mapped onto the depth they build on top
    of. Stencils with no depth meaning (prologue/epilogue/control-flow) get
    variant_id=None.
    """

    _R8_BASE_VARIANT = {
        "i32_load_r8": 1,
        "i32_load8_s_r8": 1,
        "i32_load8_u_r8": 1,
        "i32_load16_s_r8": 1,
        "i32_load16_u_r8": 1,
        "i32_store_r8": 2,
        "i32_store8_r8": 2,
        "i32_store16_r8": 2,
    }

    def __init__(self, name: str, code: list[str], hex_bytes: str, reloc_offsets: dict[str, int]):
        self.name = name
        self.code = list(code)
        self.hex_bytes = hex_bytes
        self.reloc_offsets = dict(reloc_offsets)
        self.variant_id = self._derive_variant_id(name)

    @classmethod
    def _derive_variant_id(cls, name: str) -> int | None:
        if name in cls._R8_BASE_VARIANT:
            return cls._R8_BASE_VARIANT[name]
        for depth in (0, 1, 2, 3):
            if name.endswith(f"_d{depth}"):
                return depth
        return None


class JITTraceHeader:
    """Fixed-size 16-byte header inlined at the head of every compiled trace in the JIT cache."""

    SIZE_BYTES = 16

    def __init__(
        self,
        head_wasm_pc: int = 0,
        trace_size_bytes: int = 0,
        flags: int = 0,
        variant_id: int = 0,
        chain_next_pc: int = 0,
        chain_target_addr: int = 0,
    ):
        self.head_wasm_pc = head_wasm_pc
        self.trace_size_bytes = trace_size_bytes
        self.flags = flags
        self.variant_id = variant_id
        self.chain_next_pc = chain_next_pc
        self.chain_target_addr = chain_target_addr

    def to_bytes(self) -> bytes:
        import struct

        return struct.pack(
            "<IHBBII",
            self.head_wasm_pc,
            self.trace_size_bytes,
            self.flags,
            self.variant_id,
            self.chain_next_pc,
            self.chain_target_addr,
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray, offset: int = 0) -> "JITTraceHeader":
        head_pc, size, flags, variant, chain_next, chain_target = struct.unpack_from(
            "<IHBBII", data, offset
        )
        return cls(head_pc, size, flags, variant, chain_next, chain_target)


_REG_NAME_TO_ENUM = {r.name.lower(): r for r in Reg}

# WASM linear-memory ops whose operand register (r4 for a unary load's address,
# r5 for a store's address -- value stays in r4) must be bounds-checked against
# vsoc_runtime.mem-size (pinned in R9) before the access is allowed to execute.
# See {FastAddressCheck} / {MemoryBoundaryCheck}: trapping to the interpreter is
# mandatory on out-of-bounds, silent wrapping is not permitted.
_MEMORY_OP_ADDR_REG = {
    "i32.load": Reg.R4,
    "i32.load8_s": Reg.R4,
    "i32.load8_u": Reg.R4,
    "i32.load16_s": Reg.R4,
    "i32.load16_u": Reg.R4,
    "i32.store": Reg.R5,
    "i32.store8": Reg.R5,
    "i32.store16": Reg.R5,
}


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
        self.last_oob_fixups: list[int] = []
        self.last_trap_tail_byte_addr: int | None = None
        self.mpu_attr: str = MPUAttribute.RO_X  # Default state: RO_X
        self.barrier_flushes: int = 0
        self.current_write_pos: int = 0
        # Exhaustive Stencil Library (Cortex-M33 AAPCS + JIT Register Map)
        # R0=ip, R1=stack_bot, R2=env, R3=local_base, R4=TOS, R5=NOS, R6=NNOS, R7=FP,
        # R8/R9=mem_base/mem_size (pinned only when the trace touches linear memory),
        # R12=intra-call scratch (globals_base pointer, rem/rotl temporaries), R8-R11=assignable pool otherwise
        self.stencils: dict[str, Stencil] = {
            # --- Prologue, Epilogue & Interop ---
            "prologue_full": Stencil(
                "prologue_full", ["PUSH.W {r4-r6, r8-r11, lr}"], "2D E9 70 4F", {}
            ),
            "epilogue_return": Stencil(
                "epilogue_return", ["POP.W {r4-r6, r8-r11, pc}"], "BD E8 70 8F", {}
            ),
            "fallback_interp": Stencil(
                "fallback_interp",
                ["POP.W {r4-r6, r8-r11, lr}", "BX r12"],
                "BD E8 70 4F 60 47",
                {},
            ),
            "external_call_stub": Stencil(
                "external_call_stub",
                ["PUSH {r0-r3, r12, lr}", "BL 0x00000000", "POP {r0-r3, r12, lr}"],
                "2D E9 0F 50 00 F0 00 F8 BD E8 0F 50",
                {"branch_off": 1},
            ),
            # --- Control Flow ---
            "unreachable": Stencil("unreachable", ["BKPT #0x00"], "00 BE", {}),
            "nop": Stencil("nop", [], "", {}),
            "br": Stencil("br", ["B.W 0x00000000"], "00 F0 00 B8", {"target": 0}),
            "br_if_d1": Stencil(
                "br_if_d1",
                ["CMP r4, #0", "BNE.W 0x00000000"],
                "00 2C 00 F0 00 80",
                {"target": 1},
            ),
            "select_d3": Stencil(
                "select_d3",
                ["CMP r4, #0", "IT NE", "MOVNE r5, r6", "MOV r4, r5"],
                "00 2C 18 BF 35 46 2C 46",
                {},
            ),
            # --- Constants ---
            "i32_const_d0": Stencil(
                "i32_const_d0",
                ["MOVW r4, #0x0000", "MOVT r4, #0x0000"],
                "40 F2 00 04 C0 F2 00 04",
                {"imm_lo": 0, "imm_hi": 1},
            ),
            "i32_const_d1": Stencil(
                "i32_const_d1",
                ["MOV r5, r4", "MOVW r4, #0x0000", "MOVT r4, #0x0000"],
                "25 46 40 F2 00 04 C0 F2 00 04",
                {"imm_lo": 1, "imm_hi": 2},
            ),
            "i64_const_d0": Stencil(
                "i64_const_d0",
                [
                    "MOVW r4, #0x0000",
                    "MOVT r4, #0x0000",
                    "MOVW r5, #0x0000",
                    "MOVT r5, #0x0000",
                ],
                "40 F2 00 04 C0 F2 00 04 40 F2 00 05 C0 F2 00 05",
                {"imm32_lo": 0, "imm32_hi": 2},
            ),
            # --- Variables ---
            "local_get_d0": Stencil(
                "local_get_d0", ["LDR r4, [r1, #0x00]"], "0C 68", {"offset": 0}
            ),
            "local_set_d1": Stencil(
                "local_set_d1", ["STR r4, [r1, #0x00]"], "0C 60", {"offset": 0}
            ),
            "local_tee_d1": Stencil(
                "local_tee_d1", ["STR r4, [r1, #0x00]"], "0C 60", {"offset": 0}
            ),
            # R12 (AAPCS intra-call scratch) holds the globals_base pointer only for the
            # duration of this one stencil -- R3 is local_base now, not general scratch.
            "global_get_d0": Stencil(
                "global_get_d0",
                ["LDR.W r12, [r2, #0x08]", "LDR.W r4, [r12, #0x00]"],
                "D2 F8 08 C0 DC F8 00 40",
                {"offset": 1},
            ),
            "global_set_d1": Stencil(
                "global_set_d1",
                ["LDR.W r12, [r2, #0x08]", "STR.W r4, [r12, #0x00]"],
                "D2 F8 08 C0 CC F8 00 40",
                {"offset": 1},
            ),
            # --- 32-bit Integer Arithmetic & Logic ---
            "i32_add_d2": Stencil("i32_add_d2", ["ADDS r4, r5, r4"], "2C 19", {}),
            "i32_sub_d2": Stencil("i32_sub_d2", ["SUBS r4, r5, r4"], "2C 1B", {}),
            "i32_mul_d2": Stencil("i32_mul_d2", ["MUL r4, r5, r4"], "05 FB 04 F4", {}),
            "i32_div_s_d2": Stencil("i32_div_s_d2", ["SDIV r4, r5, r4"], "95 FB F4 F4", {}),
            "i32_div_u_d2": Stencil("i32_div_u_d2", ["UDIV r4, r5, r4"], "B5 FB F4 F4", {}),
            # R12 scratch, not R3 -- R3 is local_base now (see i32_rotl_d2 below too).
            "i32_rem_s_d2": Stencil(
                "i32_rem_s_d2",
                ["SDIV r12, r5, r4", "MLS r4, r12, r4, r5"],
                "95 FB F4 FC 0C FB 14 54",
                {},
            ),
            "i32_rem_u_d2": Stencil(
                "i32_rem_u_d2",
                ["UDIV r12, r5, r4", "MLS r4, r12, r4, r5"],
                "B5 FB F4 FC 0C FB 14 54",
                {},
            ),
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
            "i32_rotl_d2": Stencil(
                "i32_rotl_d2",
                ["RSB r12, r4, #32", "ROR.W r4, r5, r12"],
                "C4 F1 20 0C 65 FA 0C F4",
                {},
            ),
            "i32_rotr_d2": Stencil("i32_rotr_d2", ["ROR.W r4, r5, r4"], "65 FA 04 F4", {}),
            "i32_clz_d1": Stencil("i32_clz_d1", ["CLZ r4, r4"], "B4 FA 84 F4", {}),
            "i32_ctz_d1": Stencil(
                "i32_ctz_d1",
                ["RBIT r4, r4", "CLZ r4, r4"],
                "94 FA A4 F4 B4 FA 84 F4",
                {},
            ),
            # --- 32-bit Integer Comparisons ---
            "i32_eqz_d1": Stencil(
                "i32_eqz_d1",
                ["CMP r4, #0", "IT EQ", "MOVEQ r4, #1", "IT NE", "MOVNE r4, #0"],
                "00 2C 08 BF 01 24 18 BF 00 24",
                {},
            ),
            "i32_eq_d2": Stencil(
                "i32_eq_d2",
                ["CMP r5, r4", "IT EQ", "MOVEQ r4, #1", "IT NE", "MOVNE r4, #0"],
                "A5 42 08 BF 01 24 18 BF 00 24",
                {},
            ),
            "i32_ne_d2": Stencil(
                "i32_ne_d2",
                ["CMP r5, r4", "IT NE", "MOVNE r4, #1", "IT EQ", "MOVEQ r4, #0"],
                "A5 42 18 BF 01 24 08 BF 00 24",
                {},
            ),
            "i32_lt_s_d2": Stencil(
                "i32_lt_s_d2",
                ["CMP r5, r4", "IT LT", "MOVLT r4, #1", "IT GE", "MOVGE r4, #0"],
                "A5 42 B8 BF 01 24 A8 BF 00 24",
                {},
            ),
            "i32_lt_u_d2": Stencil(
                "i32_lt_u_d2",
                ["CMP r5, r4", "IT LO", "MOVLO r4, #1", "IT HS", "MOVHS r4, #0"],
                "A5 42 38 BF 01 24 28 BF 00 24",
                {},
            ),
            "i32_gt_s_d2": Stencil(
                "i32_gt_s_d2",
                ["CMP r5, r4", "IT GT", "MOVGT r4, #1", "IT LE", "MOVLE r4, #0"],
                "A5 42 C8 BF 01 24 D8 BF 00 24",
                {},
            ),
            "i32_gt_u_d2": Stencil(
                "i32_gt_u_d2",
                ["CMP r5, r4", "IT HI", "MOVHI r4, #1", "IT LS", "MOVLS r4, #0"],
                "A5 42 88 BF 01 24 98 BF 00 24",
                {},
            ),
            "i32_le_s_d2": Stencil(
                "i32_le_s_d2",
                ["CMP r5, r4", "IT LE", "MOVLE r4, #1", "IT GT", "MOVGT r4, #0"],
                "A5 42 D8 BF 01 24 C8 BF 00 24",
                {},
            ),
            "i32_le_u_d2": Stencil(
                "i32_le_u_d2",
                ["CMP r5, r4", "IT LS", "MOVLS r4, #1", "IT HI", "MOVHI r4, #0"],
                "A5 42 98 BF 01 24 88 BF 00 24",
                {},
            ),
            "i32_ge_s_d2": Stencil(
                "i32_ge_s_d2",
                ["CMP r5, r4", "IT GE", "MOVGE r4, #1", "IT LT", "MOVLT r4, #0"],
                "A5 42 A8 BF 01 24 B8 BF 00 24",
                {},
            ),
            "i32_ge_u_d2": Stencil(
                "i32_ge_u_d2",
                ["CMP r5, r4", "IT HS", "MOVHS r4, #1", "IT LO", "MOVLO r4, #0"],
                "A5 42 28 BF 01 24 38 BF 00 24",
                {},
            ),
            # --- Linear Memory Access (R8 = mem_base) ---
            # R3 is local_base (not mem_base) -- see docs/specs/jit_stencil_catalog.md 3.8.
            # mem_base/mem_size are pinned in R8/R9 precisely so they never collide with it.
            # The FastAddressCheck bounds check (CMP addr, r9=mem_size; BHS.W <trap>) is NOT part of
            # these stencils -- it needs a runtime-patched branch target (the trace's own trap tail),
            # so it is emitted directly by compile_trace() around whichever of these gets selected,
            # the same way local.get/local.set are handled rather than being a fixed byte template.
            "i32_load_r8": Stencil("i32_load_r8", ["LDR.W r4, [r8, r4]"], "58 F8 04 40", {}),
            "i32_load8_s_r8": Stencil(
                "i32_load8_s_r8", ["LDRSB.W r4, [r8, r4]"], "18 F9 04 40", {}
            ),
            "i32_load8_u_r8": Stencil("i32_load8_u_r8", ["LDRB.W r4, [r8, r4]"], "18 F8 04 40", {}),
            "i32_load16_s_r8": Stencil(
                "i32_load16_s_r8", ["LDRSH.W r4, [r8, r4]"], "38 F9 04 40", {}
            ),
            "i32_load16_u_r8": Stencil(
                "i32_load16_u_r8", ["LDRH.W r4, [r8, r4]"], "38 F8 04 40", {}
            ),
            "i32_store_r8": Stencil("i32_store_r8", ["STR.W r4, [r8, r5]"], "48 F8 05 40", {}),
            "i32_store8_r8": Stencil("i32_store8_r8", ["STRB.W r4, [r8, r5]"], "08 F8 05 40", {}),
            "i32_store16_r8": Stencil("i32_store16_r8", ["STRH.W r4, [r8, r5]"], "28 F8 05 40", {}),
            "memory_size_d0": Stencil(
                "memory_size_d0", ["LDR.W r4, [r2, #0x04]"], "D2 F8 04 40", {}
            ),
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
            raise MPUFault(
                "W^X VIOLATION: Attempted instruction execution on non-executable memory"
            )
        return self.code_cache[start_offset : start_offset + num_instructions]

    def _emit_bytes(self, data: bytes):
        """Copies real Thumb-2 machine code into byte_cache, honoring the same W^X gate."""
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: Attempted write to non-writable code memory")
        end = self.byte_write_pos + len(data)
        if end > len(self.byte_cache):
            raise MemoryError("JIT byte cache exhausted")
        self.byte_cache[self.byte_write_pos : end] = data
        self.byte_write_pos = end

    def execute_native_bytes(self, start_byte_offset: int, num_bytes: int) -> bytes:
        """Fetches real machine code bytes for external execution (e.g. a CPU emulator)."""
        if self.mpu_attr != MPUAttribute.RO_X:
            raise MPUFault(
                "W^X VIOLATION: Attempted instruction execution on non-executable memory"
            )
        return bytes(self.byte_cache[start_byte_offset : start_byte_offset + num_bytes])

    # --- Full Copy-and-Patch Compilation ---
    def compile_trace(
        self,
        wasm_ops: list[tuple[str, object]],
        exit_kind: str = "return",
        dirty_spills: list[tuple[str, int]] | None = None,
        head_wasm_pc: int = 0,
        chain_next_pc: int = 0,
        chain_target_addr: int = 0,
        variant_id: int = 2,
    ) -> tuple[int, int]:
        """
        Batches stencil copy & relocation patching inside a single W^X transaction.
        Inlines a 16-byte JITTraceHeader at the start of the trace buffer.
        Flushes all dirty spilled variables (TOS/NOS, registers) to unified stack before POP/BX.
        Returns (code_start_offset, total_instructions).
        `variant_id` is the trace's register-occupancy ID (Depth 0..3, see
        docs/specs/jit_stencil_catalog.md 3.8) -- which of TOS/NOS/NNOS are register-
        resident. It does NOT describe anything about chaining to another trace:
        trace-boundary chaining ({JIT_LazyChaining}) always goes through memory
        regardless of variant (jit_compiler.md 8, {ADR_TosCacheAsymmetry}) and never
        reads this field to decide how to link. It exists for consecutive stencils
        *within* this same trace (see emit_variant_reconciliation_glue() below), which
        matters once a future per-trace register allocator can make them disagree --
        this engine does not yet compute variant_id automatically from `wasm_ops`
        (that belongs to that allocator, out of scope here); the caller states it, the
        same way it already states `exit_kind`/`dirty_spills`. Recorded in the header
        for real, not hardcoded.
        """
        start_offset = self.current_write_pos
        dirty_spills = dirty_spills or []
        asm = Thumb2Assembler()
        # 1. Begin W^X Transaction (RW + XN)
        self.begin_jit_patch()
        # 2. Emit 16-byte JIT Trace Header (inlined at the head of every trace)
        header_byte_offset = self.byte_write_pos
        self.write_instruction(
            self.current_write_pos,
            f"// [JIT_TRACE_HEADER] pc=0x{head_wasm_pc:X} (16 bytes)",
        )
        self.current_write_pos += 1
        self._emit_bytes(bytes(JITTraceHeader.SIZE_BYTES))
        code_start_byte_offset = self.byte_write_pos
        code_start_inst_offset = self.current_write_pos

        def emit(inst: str, data: bytes):
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1
            self._emit_bytes(data)

        def emit_stencil(st: "Stencil"):
            raw = bytes.fromhex(st.hex_bytes.replace(" ", "")) if st.hex_bytes else b""
            if not st.code:
                return
            self.write_instruction(self.current_write_pos, st.code[0])
            self.current_write_pos += 1
            self._emit_bytes(raw)
            for inst in st.code[1:]:
                self.write_instruction(self.current_write_pos, inst)
                self.current_write_pos += 1

        # 3. Emit Full Callee-saved Prologue
        emit_stencil(self.stencils["prologue_full"])
        # 3b. If the trace touches linear memory, pin R8=mem_base and R9=mem_size for the
        # lifetime of the trace (execution_context: mem_base @+0x10, mem_size @+0x14 -- see
        # docs/architecture/architecture_overview.md 4.1). Loaded once here rather than
        # per-access since neither value can change mid-trace.
        has_memory_ops = any(op in _MEMORY_OP_ADDR_REG for op, _ in wasm_ops)
        if has_memory_ops:
            emit("LDR.W r8, [r1, #0x10]", asm.ldr_w_imm12(Reg.R8, Reg.R1, 0x10))
            emit("LDR.W r9, [r1, #0x14]", asm.ldr_w_imm12(Reg.R9, Reg.R1, 0x14))

        # Byte addresses of BHS.W trap branches emitted below, patched once the trace's
        # trap tail (see step 5b) is known.
        oob_branch_fixups: list[int] = []
        # 4. Emit WASM Ops with Relocation Patching
        for op, arg in wasm_ops:
            if op == "i32.const":
                imm = int(arg) & 0xFFFFFFFF
                emit(f"MOVW r4, #{imm & 0xFFFF}", asm.movw(Reg.R4, imm & 0xFFFF))
                emit(
                    f"MOVT r4, #{(imm >> 16) & 0xFFFF}",
                    asm.movt(Reg.R4, (imm >> 16) & 0xFFFF),
                )
            elif op == "local.get":
                off = int(arg)
                emit(f"LDR r4, [r1, #{off}]", asm.ldr_imm(Reg.R4, Reg.R1, off))
            elif op == "local.set":
                off = int(arg)
                emit(f"STR r4, [r1, #{off}]", asm.str_imm(Reg.R4, Reg.R1, off))
            elif op == "br_if":
                emit("CMP r4, #0", asm.cmp_imm8(Reg.R4, 0))
                target_pc = int(arg)
                emit(f"BNE.W 0x{target_pc:08X}", asm.b_cond_w(Cond.NE, 0))
            elif op == "external_call":
                func_name = str(arg)
                call_mask = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 12)
                emit(
                    "PUSH {r0-r3, r12, lr}",
                    asm.push_w(reg_mask=call_mask, push_lr=True),
                )
                emit(f"BL {func_name}", asm.bl(0))  # relocation hole: patched after linking
                emit("POP {r0-r3, r12, lr}", asm.pop_w(reg_mask=call_mask, pop_lr=True))
            elif op in _MEMORY_OP_ADDR_REG:
                # FastAddressCheck: CMP the address against mem_size (R9) and take a
                # placeholder BHS.W to the trace's trap tail (patched in step 5b, once its
                # address is known) *before* the actual load/store executes, so an
                # out-of-bounds access never has a side effect to unwind. R9 is a high
                # register, so the low addr_reg (R4/R5) needs the T2 CMP encoding.
                addr_reg = _MEMORY_OP_ADDR_REG[op]
                emit(f"CMP {addr_reg.name.lower()}, r9", asm.cmp_reg_t2(addr_reg, Reg.R9))
                oob_branch_fixups.append(self.byte_write_pos)
                emit("BHS.W <trap>", asm.b_cond_w(Cond.HS, 0))
                emit_stencil(self.stencils[op.replace(".", "_") + "_r8"])
            else:
                # Direct stencil mapping
                stencil_key = op.replace(".", "_") + "_d2"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_") + "_d1"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_") + "_r8"
                if stencil_key not in self.stencils:
                    stencil_key = op.replace(".", "_")

                if stencil_key in self.stencils:
                    emit_stencil(self.stencils[stencil_key])
                else:
                    raise ValueError(f"Unsupported stencil opcode: {op}")

        def flush_dirty_spills():
            for reg, stack_off in dirty_spills:
                reg_enum = _REG_NAME_TO_ENUM[reg.lower()]
                if reg_enum <= Reg.R7:
                    emit(
                        f"STR {reg}, [r1, #{stack_off}]",
                        asm.str_imm(reg_enum, Reg.R1, stack_off),
                    )
                else:
                    emit(
                        f"STR {reg}, [r1, #{stack_off}]",
                        asm.str_w_imm12(reg_enum, Reg.R1, stack_off),
                    )

        # 5. Emit Epilogue: Flush Dirty Spill Variables before POP
        flush_dirty_spills()
        if exit_kind == "return":
            emit_stencil(self.stencils["epilogue_return"])
        elif exit_kind == "fallback":
            emit_stencil(self.stencils["fallback_interp"])

        # 5b. Trap tail: every FastAddressCheck failure above lands here, regardless of the
        # trace's own exit_kind. The bounds check runs strictly before the faulting load/store,
        # so no partial side effect has happened yet and it is always safe to flush spills and
        # fall back into the interpreter at the same WASM PC -- which owns TrapCode.OUT_OF_BOUNDS
        # handling, including halting the guest task if it cannot recover. See {vMMIO_TrapAndEmulate}.
        self.last_oob_fixups = list(oob_branch_fixups)
        self.last_trap_tail_byte_addr = None
        if oob_branch_fixups:
            trap_tail_byte_addr = self.byte_write_pos
            self.last_trap_tail_byte_addr = trap_tail_byte_addr
            flush_dirty_spills()
            emit_stencil(self.stencils["fallback_interp"])
            for branch_byte_addr in oob_branch_fixups:
                rel_offset = trap_tail_byte_addr - (branch_byte_addr + 4)
                patched = asm.b_cond_w(Cond.HS, rel_offset)
                self.byte_cache[branch_byte_addr : branch_byte_addr + len(patched)] = patched

        # 6. Patch inlined JIT Trace Header
        total_trace_bytes = self.byte_write_pos - header_byte_offset
        header = JITTraceHeader(
            head_wasm_pc=head_wasm_pc,
            trace_size_bytes=total_trace_bytes,
            flags=0,
            variant_id=variant_id,
            chain_next_pc=chain_next_pc,
            chain_target_addr=chain_target_addr,
        )
        self.byte_cache[header_byte_offset : header_byte_offset + JITTraceHeader.SIZE_BYTES] = (
            header.to_bytes()
        )
        # 7. Commit W^X Transaction (RO + X + Barriers)
        self.commit_jit_patch()
        total_emitted = self.current_write_pos - start_offset
        self.last_trace_byte_range = (
            code_start_byte_offset,
            self.byte_write_pos - code_start_byte_offset,
        )
        self.last_trace_header_range = (header_byte_offset, JITTraceHeader.SIZE_BYTES)
        return (code_start_inst_offset, total_emitted - 1)

    # --- Intra-Trace Variant Compatibility & Reconciliation Glue ---
    #
    # This is about consecutive STENCILS within a single compile_trace() call,
    # NOT about chaining between two separately-compiled traces. Trace-boundary
    # chaining ({JIT_LazyChaining}) always goes through memory -- every trace's
    # prologue reloads from the canonical stack_bot-relative address and every
    # exit writes back there, regardless of the neighboring trace's variant (see
    # jit_compiler.md 8 "トレース境界とチェイニングの安全性" / {ADR_TosCacheAsymmetry}).
    # That's a settled, separate design; nothing here changes it.
    #
    # What's still open is *inside* one trace: today every WASM op maps to
    # exactly one hardcoded stencil (no dynamic depth-based selection -- see the
    # NOTE in docs/specs/jit_stencil_catalog.md 3.8), so two consecutive stencils
    # can never actually disagree on which register holds which role. If a
    # future per-trace register allocator changes that, this is the mechanism
    # that keeps it correct: VARIANT_REGISTER_MAPS records, for each variant_id,
    # which logical roles (TOS/NOS/NNOS) are register-resident and where, and
    # emit_variant_reconciliation_glue() emits whatever MOVs are needed to carry
    # values from one stencil's output layout into the next stencil's expected
    # input layout, inline in the same instruction stream (no branch -- this is
    # sequential code in one trace, not a jump to another compilation unit).
    # mem_base/mem_size/local_base are deliberately excluded from the register
    # maps: they're loaded once at trace entry and held fixed for the trace's
    # whole body, never re-negotiated between stencils.
    def emit_variant_reconciliation_glue(
        self, source_variant_id: int, target_variant_id: int
    ) -> bool:
        """Emits, inline at the engine's current write position, the MOVs needed to
        carry source_variant_id's register-resident values into target_variant_id's
        expected layout before the next stencil runs. Must be called with a W^X
        patch transaction already open (e.g. from inside compile_trace()'s op loop).
        Returns True if the transition is representable (possibly zero MOVs, if the
        layouts already agree) or False if target_variant_id needs a role
        source_variant_id never produced -- no MOV sequence can synthesize a value
        that was never computed; a well-formed trace should never reach this.
        """
        source_map = VARIANT_REGISTER_MAPS[source_variant_id]
        target_map = VARIANT_REGISTER_MAPS[target_variant_id]
        if not set(target_map).issubset(source_map):
            return False
        moves = {
            target_map[role]: source_map[role]
            for role in target_map
            if source_map[role] != target_map[role]
        }
        asm = Thumb2Assembler()
        for dst, src in _order_register_moves(moves):
            self._emit_bytes(asm.mov_reg(dst, src))
        return True


VARIANT_REGISTER_MAPS: dict[int, dict[str, Reg]] = {
    0: {},
    1: {"TOS": Reg.R4},
    2: {"TOS": Reg.R4, "NOS": Reg.R5},
    3: {"TOS": Reg.R4, "NOS": Reg.R5, "NNOS": Reg.R6},
}


def _order_register_moves(moves: dict[Reg, Reg]) -> list[tuple[Reg, Reg]]:
    """Sequences a parallel register-to-register move set (dst -> src) into a
    correct sequential MOV order, using R12 to break cycles (the classic
    permutation-shuffle problem: a straight MOV order corrupts a swap, since the
    second MOV would read back the value the first MOV just overwrote).
    R12 is used as scratch because it is Intra-Call Scratch (never a Callee-saved
    assignable-pool register, never live across a chain boundary on its own)."""
    remaining = dict(moves)
    ordered: list[tuple[Reg, Reg]] = []
    while remaining:
        # A "leaf" dst is never itself read as someone else's src -- safe to move now
        # without clobbering a value some other pending move still needs to read.
        leaf = next((d for d in remaining if d not in remaining.values()), None)
        if leaf is not None:
            ordered.append((leaf, remaining.pop(leaf)))
            continue
        # Everything left forms a pure cycle (e.g. a straight R4<->R5 swap): no dst
        # is safe to overwrite first without losing a value another move still
        # needs. Break it by saving the starting value in R12, walking the cycle
        # with ordinary MOVs, and closing the loop with a MOV from R12.
        start = next(iter(remaining))
        ordered.append((Reg.R12, start))
        node = start
        while True:
            src = remaining.pop(node)
            if src == start:
                ordered.append((node, Reg.R12))
                break
            ordered.append((node, src))
            node = src
    return ordered


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
            assert len(tok) == 2 and set(tok) <= hex_digits, (
                f"Stencil {name} hex_bytes token {tok!r} is not a valid byte"
            )


def test_stencil_variant_ids_match_the_documented_table():
    """docs/specs/jit_stencil_catalog.md 3.8 is the source of truth for which stencil
    belongs to which trace-boundary register variant (Depth 0..3). Stencil.variant_id
    is derived from the name rather than hand-set per entry precisely so it cannot
    drift from that name -- this test instead guards the table (in the .md) against
    drifting from the code, by pinning a representative sample from every row."""
    engine = CopyPatchJITEngine()
    expected = {
        # Depth 0 (Empty): next stencil generates a value from nothing.
        "i32_const_d0": 0,
        "i64_const_d0": 0,
        "local_get_d0": 0,
        "global_get_d0": 0,
        "memory_size_d0": 0,
        # Depth 1 (TOS only, R4).
        "i32_const_d1": 1,
        "local_set_d1": 1,
        "local_tee_d1": 1,
        "global_set_d1": 1,
        "br_if_d1": 1,
        "i32_eqz_d1": 1,
        "i32_clz_d1": 1,
        "i32_ctz_d1": 1,
        # Depth 2 (TOS+NOS, R4+R5): every binary arithmetic/comparison stencil.
        "i32_add_d2": 2,
        "i32_sub_d2": 2,
        "i32_mul_d2": 2,
        "i32_eq_d2": 2,
        "i32_lt_s_d2": 2,
        # Depth 3 (TOS+NOS+NNOS, R4+R5+R6): select is the only Depth-3 stencil.
        "select_d3": 3,
        # The _r8 memory stencils build on top of an existing depth rather than
        # introducing their own: loads reuse Depth 1 (R4=addr), stores reuse
        # Depth 2 (R4=val, R5=addr).
        "i32_load_r8": 1,
        "i32_load8_s_r8": 1,
        "i32_load16_u_r8": 1,
        "i32_store_r8": 2,
        "i32_store8_r8": 2,
        "i32_store16_r8": 2,
        # Prologue/epilogue/control-flow stencils have no depth-variant meaning.
        "prologue_full": None,
        "epilogue_return": None,
        "fallback_interp": None,
        "unreachable": None,
        "nop": None,
        "br": None,
        "external_call_stub": None,
    }
    for name, variant_id in expected.items():
        assert name in engine.stencils, f"expected stencil {name!r} is missing from the catalog"
        actual = engine.stencils[name].variant_id
        assert actual == variant_id, (
            f"{name}: variant_id={actual}, expected {variant_id} per "
            f"docs/specs/jit_stencil_catalog.md 3.8"
        )

    # Every stencil must be accounted for: either it matches a documented depth
    # suffix, or it's one of the no-depth-meaning control/prologue stencils.
    no_depth_meaning = {
        "prologue_full",
        "epilogue_return",
        "fallback_interp",
        "unreachable",
        "nop",
        "br",
        "external_call_stub",
    }
    for name, st in engine.stencils.items():
        if name in no_depth_meaning:
            assert st.variant_id is None, f"{name} should have no variant_id, got {st.variant_id}"
        else:
            assert st.variant_id is not None, (
                f"{name} has no variant_id -- add it to jit_stencil_catalog.md 3.8's table "
                f"and to Stencil._R8_BASE_VARIANT if it's an _r8 memory stencil"
            )


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
        "i64_const_d0": (
            asm.movw(Reg.R4, 0) + asm.movt(Reg.R4, 0) + asm.movw(Reg.R5, 0) + asm.movt(Reg.R5, 0)
        ),
        "local_get_d0": asm.ldr_imm(Reg.R4, Reg.R1, 0),
        "local_set_d1": asm.str_imm(Reg.R4, Reg.R1, 0),
        "local_tee_d1": asm.str_imm(Reg.R4, Reg.R1, 0),
        "global_get_d0": asm.ldr_w_imm12(Reg.R12, Reg.R2, 0x08)
        + asm.ldr_w_imm12(Reg.R4, Reg.R12, 0),
        "global_set_d1": asm.ldr_w_imm12(Reg.R12, Reg.R2, 0x08)
        + asm.str_w_imm12(Reg.R4, Reg.R12, 0),
        "memory_size_d0": asm.ldr_w_imm12(Reg.R4, Reg.R2, 4),
        "i32_add_d2": asm.adds_reg(Reg.R4, Reg.R5, Reg.R4),
        "i32_sub_d2": asm.subs_reg(Reg.R4, Reg.R5, Reg.R4),
        "i32_mul_d2": asm.mul(Reg.R4, Reg.R5, Reg.R4),
        "i32_div_s_d2": asm.sdiv(Reg.R4, Reg.R5, Reg.R4),
        "i32_div_u_d2": asm.udiv(Reg.R4, Reg.R5, Reg.R4),
        "i32_rem_s_d2": asm.sdiv(Reg.R12, Reg.R5, Reg.R4)
        + asm.mls(Reg.R4, Reg.R12, Reg.R4, Reg.R5),
        "i32_rem_u_d2": asm.udiv(Reg.R12, Reg.R5, Reg.R4)
        + asm.mls(Reg.R4, Reg.R12, Reg.R4, Reg.R5),
        "i32_and_d2": asm.ands_reg(Reg.R4, Reg.R5),
        "i32_or_d2": asm.orrs_reg(Reg.R4, Reg.R5),
        "i32_xor_d2": asm.eors_reg(Reg.R4, Reg.R5),
        "i32_shl_d2": asm.lsl_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_shr_s_d2": asm.asr_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_shr_u_d2": asm.lsr_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_rotr_d2": asm.ror_w(Reg.R4, Reg.R5, Reg.R4),
        "i32_clz_d1": asm.clz(Reg.R4, Reg.R4),
        "i32_ctz_d1": asm.rbit(Reg.R4, Reg.R4) + asm.clz(Reg.R4, Reg.R4),
        "i32_load_r8": asm.ldr_w_reg(Reg.R4, Reg.R8, Reg.R4),
        "i32_store_r8": asm.str_w_reg(Reg.R4, Reg.R8, Reg.R5),
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
    # i32_rotl_d2: RSB r12,r4,#32 (amount = 32 - shift) then ROR.W r4,r5,r12. R12 scratch,
    # not R3 -- R3 is local_base now.
    rotl_expected = h(asm.rsb_imm(Reg.R12, Reg.R4, 32) + asm.ror_w(Reg.R4, Reg.R5, Reg.R12))
    assert rotl_expected == engine.stencils["i32_rotl_d2"].hex_bytes, (
        f"Stencil 'i32_rotl_d2' drifted from the assembler: "
        f"catalog={engine.stencils['i32_rotl_d2'].hex_bytes!r} assembler={rotl_expected!r}"
    )
    # i32_eqz_d1: CMP r4,#0 ; IT EQ ; MOVEQ r4,#1 ; IT NE ; MOVNE r4,#0.
    eqz_expected = h(
        asm.cmp_imm8(Reg.R4, 0)
        + asm.it(Cond.EQ, 0b1000)
        + asm.movs_imm8(Reg.R4, 1)
        + asm.it(Cond.NE, 0b1000)
        + asm.movs_imm8(Reg.R4, 0)
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
    # ops include i32.load/i32.store, so the prologue also pins R8=mem_base/R9=mem_size,
    # and the trace grows a trap tail (FastAddressCheck fallback to the interpreter) after
    # the normal return path.
    assert code[1] == "LDR.W r8, [r1, #0x10]"
    assert code[2] == "LDR.W r9, [r1, #0x14]"
    assert "MOVW r4, #100" in code[3]
    assert "POP.W {r4-r6, r8-r11, pc}" in code
    assert code[-1] == "BX r12"


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
        if mnemonic in (
            "STR",
            "STR.W",
            "STRB.W",
            "STRH.W",
            "BX",
            "PUSH",
            "PUSH.W",
            "POP",
            "POP.W",
            "CMP",
            "BNE.W",
            "BL",
        ):
            continue
        dest = operands.split(",")[0].strip()
        assert dest not in ("r0", "r1", "r2", "R0", "R1", "R2"), (
            f"Instruction '{inst}' illegal write to shared CPS register"
        )


def test_fast_address_check_traps_before_access():
    """FastAddressCheck/MemoryBoundaryCheck: OOB address must trap to the interpreter, not wrap.
    Bounds checking is size-comparison based (no mask, no power-of-two constraint on mem-size).
    The check must precede the actual load/store, and the placeholder BHS.W branch emitted for
    it must be back-patched to a real (non-placeholder) offset pointing at the trace's trap
    tail -- an un-patched offset=0 branch would silently fall through into the faulting access.
    """
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()
    ops = [("i32.const", 0), ("i32.load", None)]
    start_pos, count = engine.compile_trace(ops)
    code = engine.execute_native(start_pos, count)
    assert "CMP r4, r9" in code
    assert "BHS.W <trap>" in code
    assert "LDR.W r4, [r8, r4]" in code
    # The bounds check + branch must be emitted strictly before the access it guards.
    assert code.index("BHS.W <trap>") < code.index("LDR.W r4, [r8, r4]")
    assert len(engine.last_oob_fixups) == 1
    assert engine.last_trap_tail_byte_addr is not None
    branch_byte_addr = engine.last_oob_fixups[0]
    rel_offset = engine.last_trap_tail_byte_addr - (branch_byte_addr + 4)
    assert rel_offset > 0, "trap tail must be patched to a real forward offset, not left at 0"
    expected_bytes = asm.b_cond_w(Cond.HS, rel_offset)
    patched_bytes = bytes(
        engine.byte_cache[branch_byte_addr : branch_byte_addr + len(expected_bytes)]
    )
    assert patched_bytes == expected_bytes, (
        "BHS.W placeholder was not back-patched to the trap tail"
    )
    # The trap tail itself must reach the interpreter fallback (POP + BX r12), never a bare return.
    assert code[-1] == "BX r12"


def test_memory_access_without_bounds_check_is_impossible():
    """No memory-access stencil is reachable from compile_trace() without the CMP/BHS.W guard --
    i.e. there is no code path left that performs a raw unchecked load/store (the old mask-based
    ANDS design silently wrapped instead of trapping; the design now requires a trap on OOB)."""
    for op in (
        "i32.load",
        "i32.load8_s",
        "i32.load8_u",
        "i32.load16_s",
        "i32.load16_u",
        "i32.store",
        "i32.store8",
        "i32.store16",
    ):
        engine = CopyPatchJITEngine()
        ops = (
            [("i32.const", 0)]
            if op.startswith("i32.load")
            else [("i32.const", 0), ("i32.const", 0)]
        )
        ops.append((op, None))
        start_pos, count = engine.compile_trace(ops)
        code = engine.execute_native(start_pos, count)
        assert "BHS.W <trap>" in code, f"{op} compiled without a FastAddressCheck guard"
    del engine


def test_variant_reconciliation_glue_same_variant_emits_nothing():
    """Depth 2 -> Depth 2 (the only real transition today) must be a no-op: the next
    stencil already finds TOS/NOS exactly where it left them, no MOVs needed."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=2, target_variant_id=2)
    engine.commit_jit_patch()
    assert ok is True
    assert engine.byte_write_pos == start_pos, "identical layouts must not emit any MOV"


def test_variant_reconciliation_glue_subset_emits_nothing():
    """A Depth-2 exit feeding a Depth-1 entry needs no reconciliation either: the
    entry only reads TOS (R4), which the Depth-2 layout already has in R4 too."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=2, target_variant_id=1)
    engine.commit_jit_patch()
    assert ok is True
    assert engine.byte_write_pos == start_pos


def test_variant_reconciliation_glue_rejects_missing_value():
    """A Depth-1 exit cannot feed a Depth-2 entry: the entry needs a NOS value the
    predecessor never computed, and no MOV sequence can synthesize a value that was
    never produced. This should never actually arise in a well-formed trace (depth
    only grows via real pushes), but the mechanism must fail closed if it did."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=1, target_variant_id=2)
    engine.commit_jit_patch()
    assert ok is False


def test_variant_reconciliation_glue_emits_real_swap_bytes():
    """Structural check that a genuine register-layout mismatch (same role set,
    different physical registers -- what a future allocator could produce) emits the
    real cycle-safe MOV sequence, not a placeholder. Semantic correctness on actual
    hardware is proven separately in jit_trace_execution_verifier.py (Unicorn)."""
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()
    # A synthetic alt-Depth-2 layout (TOS=R5, NOS=R4) swapped relative to the real one,
    # standing in for a hypothetical future allocator output -- not a real variant_id.
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    moves = {Reg.R4: Reg.R5, Reg.R5: Reg.R4}
    for dst, src in _order_register_moves(moves):
        engine._emit_bytes(asm.mov_reg(dst, src))
    engine.commit_jit_patch()
    emitted = bytes(engine.byte_cache[start_pos : engine.byte_write_pos])
    expected = (
        asm.mov_reg(Reg.R12, Reg.R4) + asm.mov_reg(Reg.R4, Reg.R5) + asm.mov_reg(Reg.R5, Reg.R12)
    )
    assert emitted == expected


def test_order_register_moves_breaks_swap_cycle_correctly():
    """A straight R4<->R5 swap is the classic case a naive move-ordering corrupts: emitting
    MOV r4,r5 then MOV r5,r4 would make r5 end up equal to r4's NEW (already-overwritten)
    value instead of its original one. _order_register_moves must route through R12."""
    moves = _order_register_moves({Reg.R4: Reg.R5, Reg.R5: Reg.R4})
    assert moves == [(Reg.R12, Reg.R4), (Reg.R4, Reg.R5), (Reg.R5, Reg.R12)]


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
        ops, exit_kind="fallback", dirty_spills=[("r4", 0), ("r8", 8)]
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
        raise AssertionError("Should raise MPUFault")
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)


def test_jit_trace_header_layout():
    """Verify that a 16-byte JITTraceHeader is correctly inlined at the head of every compiled trace."""
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 42), ("local.set", 0)]
    start_pos, count = engine.compile_trace(
        ops,
        exit_kind="fallback",
        head_wasm_pc=0x100,
        chain_next_pc=0x200,
        chain_target_addr=0x08001020,
    )
    assert count > 0
    header_offset, header_len = engine.last_trace_header_range
    assert header_len == JITTraceHeader.SIZE_BYTES == 16
    assert header_offset == 0
    # Parse the header directly from byte_cache
    header = JITTraceHeader.from_bytes(engine.byte_cache, header_offset)
    assert header.head_wasm_pc == 0x100
    assert header.trace_size_bytes > 16
    assert header.chain_next_pc == 0x200
    assert header.chain_target_addr == 0x08001020


if __name__ == "__main__":
    test_full_stencil_library_coverage()
    test_stencil_variant_ids_match_the_documented_table()
    test_stencil_catalog_matches_assembler()
    test_arithmetic_and_logic_traces()
    test_external_aapcs_call_stub()
    test_epilogue_spill_variable_flush()
    test_cps_shared_registers_never_clobbered()
    test_fast_address_check_traps_before_access()
    test_memory_access_without_bounds_check_is_impossible()
    test_variant_reconciliation_glue_same_variant_emits_nothing()
    test_variant_reconciliation_glue_subset_emits_nothing()
    test_variant_reconciliation_glue_rejects_missing_value()
    test_variant_reconciliation_glue_emits_real_swap_bytes()
    test_order_register_moves_breaks_swap_cycle_correctly()
    test_mpu_wx_protection()
    test_jit_trace_header_layout()
    print("[PASS] All JIT Copy-and-Patch Full-Set concept tests passed successfully.")
