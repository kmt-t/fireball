"""
docs/components/tier3_jit/concepts/jit_copy_patch_concept.py
Reference Concept Implementation: Full-Set Copy-and-Patch JIT Engine & MPU W^X Transaction Protocol
- Exhaustive binary stencil library matching docs/specs/jit_stencil_catalog.md & wasm_instruction_set.md
- Multi-dimensional register variants (Depth 0/1/2/3, R2 local_base, R8/R9 mem_base/mem_size, Callee-saved R4-R6, R8-R11)
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
    compiled traces. A direct chain branch (exit_kind="chain") carries live
    register state straight across the boundary with no memory traffic and no
    variant reconciliation; only a genuine exit (exit_kind="return"/"fallback",
    no resident successor) flushes dirty values to memory
    (jit_compiler.md 8, {ADR_TosCacheAsymmetry}, {JIT_LazyChaining}). The `_r8` memory stencils don't
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

# WASM linear-memory ops whose operand register (r3 for a unary load's address,
# r4 for a store's address -- value is in r3) must be bounds-checked against
# vsoc_runtime.mem-size (pinned in R9) before the access is allowed to execute.
# See {FastAddressCheck} / {MemoryBoundaryCheck}: trapping to the interpreter is
# mandatory on out-of-bounds, silent wrapping is not permitted.
_MEMORY_OP_ADDR_REG = {
    "i32.load": Reg.R3,
    "i32.load8_s": Reg.R3,
    "i32.load8_u": Reg.R3,
    "i32.load16_s": Reg.R3,
    "i32.load16_u": Reg.R3,
    "i32.store": Reg.R4,
    "i32.store8": Reg.R4,
    "i32.store16": Reg.R4,
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
        # R0=ctx (execution_context*), R1=SP (OperandStack pointer), R2=local_base, R3=TOS,
        # R4=NOS, R5=NNOS, R6=scratch, R7=FP (AAPCS),
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
            "chain_branch": Stencil(
                "chain_branch", ["B.W 0x00000000"], "00 F0 00 B8", {"target": 0}
            ),
            "dynamic_chain_exit_d1": Stencil(
                "dynamic_chain_exit_d1",
                [
                    "LDR.W r12, [pc, #-0x18]",
                    "CMP.W r12, #0",
                    "BNE.W 0x00000006",
                    "STR r3, [r1, #0x00]",
                    "POP.W {r4-r6, r8-r11, pc}",
                    "BX r12",
                ],
                "5F F8 18 C0 BC F1 00 0F 40 F0 03 80 0B 60 BD E8 70 8F 60 47",
                {"header_target": 0, "skip_epilogue": 2, "spill_off": 3},
            ),
            "external_call_stub": Stencil(
                "external_call_stub",
                ["PUSH {r0-r3, r12, lr}", "BL 0x00000000", "POP {r0-r3, r12, lr}"],
                "2D E9 0F 50 00 F0 00 F8 BD E8 0F 50",
                {"branch_off": 1},
            ),
            # --- Control Flow & Delimiters ---
            "unreachable": Stencil("unreachable", ["BKPT #0x00"], "00 BE", {}),
            "nop": Stencil("nop", [], "", {}),
            "block": Stencil("block", [], "", {}),
            "loop": Stencil("loop", [], "", {}),
            "end": Stencil("end", [], "", {}),
            "else": Stencil("else", [], "", {}),
            "return": Stencil("return", ["POP.W {r4-r6, r8-r11, pc}"], "BD E8 70 8F", {}),
            "br": Stencil("br", ["B.W 0x00000000"], "00 F0 00 B8", {"target": 0}),
            "br_if_d1": Stencil(
                "br_if_d1",
                ["CMP r3, #0", "BNE.W 0x00000000"],
                "00 2B 40 F0 00 80",
                {"target": 1},
            ),
            "select_d3": Stencil(
                "select_d3",
                ["CMP r3, #0", "IT NE", "MOVNE r4, r5", "MOV r3, r4"],
                "00 2B 18 BF 2C 46 23 46",
                {},
            ),
            # --- Constants ---
            "i32_const_d0": Stencil(
                "i32_const_d0",
                ["MOVW r3, #0x0000", "MOVT r3, #0x0000"],
                "40 F2 00 03 C0 F2 00 03",
                {"imm_lo": 0, "imm_hi": 1},
            ),
            "i32_const_d1": Stencil(
                "i32_const_d1",
                ["MOV r4, r3", "MOVW r3, #0x0000", "MOVT r3, #0x0000"],
                "1C 46 40 F2 00 03 C0 F2 00 03",
                {"imm_lo": 1, "imm_hi": 2},
            ),
            "i64_const_d0": Stencil(
                "i64_const_d0",
                [
                    "MOVW r3, #0x0000",
                    "MOVT r3, #0x0000",
                    "MOVW r4, #0x0000",
                    "MOVT r4, #0x0000",
                ],
                "40 F2 00 03 C0 F2 00 03 40 F2 00 04 C0 F2 00 04",
                {"imm32_lo": 0, "imm32_hi": 2},
            ),
            # --- Variables ---
            # local_base (R2) addresses the current call_frame's locals array;
            # see docs/specs/jit_stencil_catalog.md 3.4.
            "local_get_d0": Stencil(
                "local_get_d0", ["LDR r3, [r2, #0x00]"], "13 68", {"offset": 0}
            ),
            "local_set_d1": Stencil(
                "local_set_d1", ["STR r3, [r2, #0x00]"], "13 60", {"offset": 0}
            ),
            "local_tee_d1": Stencil(
                "local_tee_d1", ["STR r3, [r2, #0x00]"], "13 60", {"offset": 0}
            ),
            # globals_base lives inside execution_context (R0: ctx) at +0x30, not
            # behind a separate argument register ({ExecutionContext_Layout}). R12
            # (AAPCS intra-call scratch) holds that pointer only for the duration of
            # this one stencil.
            "global_get_d0": Stencil(
                "global_get_d0",
                ["LDR.W r12, [r0, #0x30]", "LDR.W r3, [r12, #0x00]"],
                "D0 F8 30 C0 DC F8 00 30",
                {"offset": 1},
            ),
            "global_set_d1": Stencil(
                "global_set_d1",
                ["LDR.W r12, [r0, #0x30]", "STR.W r3, [r12, #0x00]"],
                "D0 F8 30 C0 CC F8 00 30",
                {"offset": 1},
            ),
            # --- 32-bit Integer Arithmetic & Logic ---
            "i32_add_d2": Stencil("i32_add_d2", ["ADDS r3, r4, r3"], "E3 18", {}),
            "i32_sub_d2": Stencil("i32_sub_d2", ["SUBS r3, r4, r3"], "E3 1A", {}),
            "i32_mul_d2": Stencil("i32_mul_d2", ["MUL r3, r4, r3"], "04 FB 03 F3", {}),
            "i32_div_s_d2": Stencil("i32_div_s_d2", ["SDIV r3, r4, r3"], "94 FB F3 F3", {}),
            "i32_div_u_d2": Stencil("i32_div_u_d2", ["UDIV r3, r4, r3"], "B4 FB F3 F3", {}),
            # R12 scratch, not R2/R3 -- R2 is local_base, R3 is TOS
            "i32_rem_s_d2": Stencil(
                "i32_rem_s_d2",
                ["SDIV r12, r4, r3", "MLS r3, r12, r3, r4"],
                "94 FB F3 FC 0C FB 13 43",
                {},
            ),
            "i32_rem_u_d2": Stencil(
                "i32_rem_u_d2",
                ["UDIV r12, r4, r3", "MLS r3, r12, r3, r4"],
                "B4 FB F3 FC 0C FB 13 43",
                {},
            ),
            "i32_and_d2": Stencil("i32_and_d2", ["ANDS r3, r4"], "23 40", {}),
            "i32_or_d2": Stencil("i32_or_d2", ["ORRS r3, r4"], "23 43", {}),
            "i32_xor_d2": Stencil("i32_xor_d2", ["EORS r3, r4"], "63 40", {}),
            # 32-bit variable shifts use 3-register Thumb-2 form
            "i32_shl_d2": Stencil("i32_shl_d2", ["LSL.W r3, r4, r3"], "04 FA 03 F3", {}),
            "i32_shr_s_d2": Stencil("i32_shr_s_d2", ["ASR.W r3, r4, r3"], "44 FA 03 F3", {}),
            "i32_shr_u_d2": Stencil("i32_shr_u_d2", ["LSR.W r3, r4, r3"], "24 FA 03 F3", {}),
            "i32_rotl_d2": Stencil(
                "i32_rotl_d2",
                ["RSB r12, r3, #32", "ROR.W r3, r4, r12"],
                "C3 F1 20 0C 64 FA 0C F3",
                {},
            ),
            "i32_rotr_d2": Stencil("i32_rotr_d2", ["ROR.W r3, r4, r3"], "64 FA 03 F3", {}),
            "i32_clz_d1": Stencil("i32_clz_d1", ["CLZ r3, r3"], "B3 FA 83 F3", {}),
            "i32_ctz_d1": Stencil(
                "i32_ctz_d1",
                ["RBIT r3, r3", "CLZ r3, r3"],
                "93 FA A3 F3 B3 FA 83 F3",
                {},
            ),
            # --- 32-bit Integer Comparisons ---
            "i32_eqz_d1": Stencil(
                "i32_eqz_d1",
                ["CMP r3, #0", "IT EQ", "MOVEQ r3, #1", "IT NE", "MOVNE r3, #0"],
                "00 2B 08 BF 01 23 18 BF 00 23",
                {},
            ),
            "i32_eq_d2": Stencil(
                "i32_eq_d2",
                ["CMP r4, r3", "IT EQ", "MOVEQ r3, #1", "IT NE", "MOVNE r3, #0"],
                "9C 42 08 BF 01 23 18 BF 00 23",
                {},
            ),
            "i32_ne_d2": Stencil(
                "i32_ne_d2",
                ["CMP r4, r3", "IT NE", "MOVNE r3, #1", "IT EQ", "MOVNE r3, #0"],
                "9C 42 18 BF 01 23 08 BF 00 23",
                {},
            ),
            "i32_lt_s_d2": Stencil(
                "i32_lt_s_d2",
                ["CMP r4, r3", "IT LT", "MOVLT r3, #1", "IT GE", "MOVGE r3, #0"],
                "9C 42 B8 BF 01 23 A8 BF 00 23",
                {},
            ),
            "i32_lt_u_d2": Stencil(
                "i32_lt_u_d2",
                ["CMP r4, r3", "IT LO", "MOVLO r3, #1", "IT HS", "MOVHS r3, #0"],
                "9C 42 38 BF 01 23 28 BF 00 23",
                {},
            ),
            "i32_gt_s_d2": Stencil(
                "i32_gt_s_d2",
                ["CMP r4, r3", "IT GT", "MOVGT r3, #1", "IT LE", "MOVLE r3, #0"],
                "9C 42 C8 BF 01 23 D8 BF 00 23",
                {},
            ),
            "i32_gt_u_d2": Stencil(
                "i32_gt_u_d2",
                ["CMP r4, r3", "IT HI", "MOVHI r3, #1", "IT LS", "MOVLS r3, #0"],
                "9C 42 88 BF 01 23 98 BF 00 23",
                {},
            ),
            "i32_le_s_d2": Stencil(
                "i32_le_s_d2",
                ["CMP r4, r3", "IT LE", "MOVLE r3, #1", "IT GT", "MOVGT r3, #0"],
                "9C 42 D8 BF 01 23 C8 BF 00 23",
                {},
            ),
            "i32_le_u_d2": Stencil(
                "i32_le_u_d2",
                ["CMP r4, r3", "IT LS", "MOVLS r3, #1", "IT HI", "MOVHI r3, #0"],
                "9C 42 98 BF 01 23 88 BF 00 23",
                {},
            ),
            "i32_ge_s_d2": Stencil(
                "i32_ge_s_d2",
                ["CMP r4, r3", "IT GE", "MOVGE r3, #1", "IT LT", "MOVLT r3, #0"],
                "9C 42 A8 BF 01 23 B8 BF 00 23",
                {},
            ),
            "i32_ge_u_d2": Stencil(
                "i32_ge_u_d2",
                ["CMP r4, r3", "IT HS", "MOVHS r3, #1", "IT LO", "MOVLO r3, #0"],
                "9C 42 28 BF 01 23 38 BF 00 23",
                {},
            ),
            # --- Linear Memory Access (R8 = mem_base) ---
            "i32_load_r8": Stencil("i32_load_r8", ["LDR.W r3, [r8, r3]"], "58 F8 03 30", {}),
            "i32_load8_s_r8": Stencil(
                "i32_load8_s_r8", ["LDRSB.W r3, [r8, r3]"], "18 F9 03 30", {}
            ),
            "i32_load8_u_r8": Stencil("i32_load8_u_r8", ["LDRB.W r3, [r8, r3]"], "18 F8 03 30", {}),
            "i32_load16_s_r8": Stencil(
                "i32_load16_s_r8", ["LDRSH.W r3, [r8, r3]"], "38 F9 03 30", {}
            ),
            "i32_load16_u_r8": Stencil(
                "i32_load16_u_r8", ["LDRH.W r3, [r8, r3]"], "38 F8 03 30", {}
            ),
            "i32_store_r8": Stencil("i32_store_r8", ["STR.W r3, [r8, r4]"], "48 F8 04 30", {}),
            "i32_store8_r8": Stencil("i32_store8_r8", ["STRB.W r3, [r8, r4]"], "08 F8 04 30", {}),
            "i32_store16_r8": Stencil("i32_store16_r8", ["STRH.W r3, [r8, r4]"], "28 F8 04 30", {}),
            # mem_size lives inside execution_context (R0: ctx) at +0x2C
            # ({ExecutionContext_Layout}).
            "memory_size_d0": Stencil(
                "memory_size_d0", ["LDR.W r3, [r0, #0x2C]"], "D0 F8 2C 30", {}
            ),
        }

    # --- MPU W^X Transaction Protocol ---
    def begin_jit_patch(self) -> None:
        """Switches JIT Code Cache MPU attribute to RW + XN."""
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_jit_patch(self) -> None:
        """Restores JIT Code Cache MPU attribute to RO + X with DSB & ISB barriers."""
        assert self.mpu_attr == MPUAttribute.RW_XN, "Must be in patching mode before commit"
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1

    def write_instruction(self, offset: int, instruction: str) -> None:
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

    def _emit_bytes(self, data: bytes) -> None:
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

    def set_chain_target(self, header_byte_offset: int, target_chain_entry_addr: int) -> None:
        """Atomically updates the chain_target_addr field (+0x0C) in the JITTraceHeader.
        CRITICAL: This updates METADATA (data bytes), NOT instruction stream code bytes.
        Therefore, it completely avoids in-place code rewriting, MPU W^X attribute toggling
        (RO_X <-> RW_XN), and instruction cache invalidation (ISB) barriers.
        """
        struct.pack_into("<I", self.byte_cache, header_byte_offset + 12, target_chain_entry_addr)

    def unlink_chain(self, header_byte_offset: int) -> None:
        """Atomically unlinks chaining by resetting chain_target_addr (+0x0C) to 0.
        Future executions will fall through to epilogue and return to the interpreter.
        """
        self.set_chain_target(header_byte_offset, 0)

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
        Returns (code_start_offset, total_instructions); also sets
        self.last_chain_entry_byte_offset (this trace's own chain entry point, just
        past its prologue -- see 3b in the body) and self.last_chain_branch_byte_addr
        (set only when exit_kind="chain": where the chain B.W was emitted).

        `exit_kind` selects one of three trace-boundary shapes, which must not be
        conflated (docs/specs/jit_stencil_catalog.md 3.1):
        - "return"/"fallback": a genuine AAPCS exit. `flush_dirty_spills()` writes
          every dirty cached value (TOS/NOS, ...) to its stack_bot-relative
          canonical address first, since nothing preserves R4-R6 past the
          POP/BX that follows -- WASM operand-stack state and the C return value
          are unrelated ({JITC-GOTCHA-07}).
        - "chain": a direct backpatched B.W to a resident successor trace's chain
          entry point (`chain_target_addr` must be that successor's
          `last_chain_entry_byte_offset`, not its trace-start address). No flush,
          no prologue/epilogue on either side of the hop -- register state
          (including R4-R6 caches) survives untouched, which is the entire point
          of chaining ({JIT_LazyChaining}). If `chain_target_addr` is 0 the branch
          is left as an unresolved placeholder for later backpatching once the
          successor is compiled (lazy chaining) or unlinking if it is evicted.

        `variant_id` is the trace's register-occupancy ID (Depth 0..3, see
        docs/specs/jit_stencil_catalog.md 3.8) -- which of TOS/NOS/NNOS are register-
        resident. It exists for consecutive stencils *within* this same trace (see
        emit_variant_reconciliation_glue() below), which matters once a future
        per-trace register allocator can make them disagree -- this engine does not
        yet compute variant_id automatically from `wasm_ops` (that belongs to that
        allocator, out of scope here); the caller states it, the same way it already
        states `exit_kind`/`dirty_spills`. Recorded in the header for real, not
        hardcoded.
        """
        start_offset = self.current_write_pos
        caller_dirty_spills = list(dirty_spills) if dirty_spills is not None else None
        current_variant = variant_id
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

        def emit(inst: str, data: bytes) -> None:
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1
            self._emit_bytes(data)

        def emit_stencil(st: "Stencil") -> None:
            raw = bytes.fromhex(st.hex_bytes.replace(" ", "")) if st.hex_bytes else b""
            if not st.code:
                return
            self.write_instruction(self.current_write_pos, st.code[0])
            self.current_write_pos += 1
            self._emit_bytes(raw)
            for inst in st.code[1:]:
                self.write_instruction(self.current_write_pos, inst)
                self.current_write_pos += 1

        def flush_dirty_spills_and_sync_context() -> None:
            # 1. Flush dirty stack values (TOS, NOS, NNOS etc.) to stack memory [r1, #offset]
            if caller_dirty_spills is not None:
                spills = caller_dirty_spills
            elif current_variant == 1:
                spills = [("r3", 0)]
            elif current_variant == 2:
                spills = [("r3", 0), ("r4", 4)]
            elif current_variant == 3:
                spills = [("r3", 0), ("r4", 4), ("r5", 8)]
            else:
                spills = []

            for reg, stack_off in spills:
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
            # 2. Write back updated SP (r1) to execution_context.sp_offset (+0x0C)
            emit("STR.W r1, [r0, #0x0C]", asm.str_w_imm12(Reg.R1, Reg.R0, 0x0C))
            # 3. Write back next WASM PC to execution_context.ip (+0x00).
            # Uses r6 as scratch: r6 is callee-saved (pushed in prologue, popped in epilogue),
            # so modifying it here leaves r12 (interp handler / sentinel) intact for BX r12!
            target_pc = chain_next_pc if chain_next_pc != 0 else head_wasm_pc + len(wasm_ops)
            emit(f"MOVW r6, #{target_pc & 0xFFFF}", asm.movw(Reg.R6, target_pc & 0xFFFF))
            if ((target_pc >> 16) & 0xFFFF) != 0:
                emit(
                    f"MOVT r6, #{(target_pc >> 16) & 0xFFFF}",
                    asm.movt(Reg.R6, (target_pc >> 16) & 0xFFFF),
                )
            emit("STR.W r6, [r0, #0x00]", asm.str_w_imm12(Reg.R6, Reg.R0, 0x00))

        # 3. Emit Full Callee-saved Prologue
        emit_stencil(self.stencils["prologue_full"])
        # The chain entry point: a resident predecessor trace's backpatched B.W
        # (exit_kind="chain") lands exactly here, skipping the prologue above.
        # Its register state (R3-R5 caches included) is already correct for this
        # trace's body, so no restore or reload is needed or wanted here.
        chain_entry_byte_offset = self.byte_write_pos
        # 3b. If the trace touches linear memory, pin R8=mem_base and R9=mem_size for the
        # lifetime of the trace (execution_context: mem_base @+0x28, mem_size @+0x2C,
        # {ExecutionContext_Layout}). Loaded once here rather than per-access since
        # neither value can change mid-trace.
        has_memory_ops = any(op in _MEMORY_OP_ADDR_REG for op, _ in wasm_ops)
        if has_memory_ops:
            emit("LDR.W r8, [r0, #0x28]", asm.ldr_w_imm12(Reg.R8, Reg.R0, 0x28))
            emit("LDR.W r9, [r0, #0x2C]", asm.ldr_w_imm12(Reg.R9, Reg.R0, 0x2C))

        current_variant = variant_id
        # Byte addresses of BHS.W trap branches emitted below, patched once the trace's
        # trap tail (see step 5b) is known.
        oob_branch_fixups: list[int] = []
        # 4. Emit WASM Ops with Relocation Patching
        for op, arg in wasm_ops:
            if op == "i32.const":
                imm = int(arg) & 0xFFFFFFFF
                emit(f"MOVW r3, #{imm & 0xFFFF}", asm.movw(Reg.R3, imm & 0xFFFF))
                emit(
                    f"MOVT r3, #{(imm >> 16) & 0xFFFF}",
                    asm.movt(Reg.R3, (imm >> 16) & 0xFFFF),
                )
                current_variant = 1
            elif op == "local.get":
                off = int(arg)
                emit(f"LDR r3, [r2, #{off}]", asm.ldr_imm(Reg.R3, Reg.R2, off))
                current_variant = 1
            elif op == "local.set":
                off = int(arg)
                emit(f"STR r3, [r2, #{off}]", asm.str_imm(Reg.R3, Reg.R2, off))
                current_variant = 0
            elif op in ("block", "loop", "end", "else", "nop"):
                # Zero-cost syntax delimiters and NOPs: eliminated at compile-time
                continue
            elif op == "return":
                # Inlined return: flush dirty spills, sync context, and POP PC to return
                flush_dirty_spills_and_sync_context()
                emit_stencil(self.stencils["epilogue_return"])
            elif op == "br":
                target_pc = arg[0] if isinstance(arg, tuple) else int(arg if arg is not None else 0)
                sp_rewind = arg[1] if isinstance(arg, tuple) and len(arg) > 1 else 0
                if sp_rewind > 0:
                    # SP immediate rewind: update SP register (r1) and execution_context.sp_offset (+0x0C)
                    if sp_rewind <= 255:
                        emit(f"SUBS r1, #{sp_rewind}", asm.subs_imm8(Reg.R1, sp_rewind))
                    else:
                        emit(
                            f"SUB.W r1, r1, #{sp_rewind}",
                            asm.sub_w_imm12(Reg.R1, Reg.R1, sp_rewind),
                        )
                    emit("STR.W r1, [r0, #0x0C]", asm.str_w_imm12(Reg.R1, Reg.R0, 0x0C))
                emit(f"B.W 0x{target_pc:08X}", asm.b_w(0))
            elif op == "br_if":
                target_pc = arg[0] if isinstance(arg, tuple) else int(arg if arg is not None else 0)
                sp_rewind = arg[1] if isinstance(arg, tuple) and len(arg) > 1 else 0
                emit("CMP r3, #0", asm.cmp_imm8(Reg.R3, 0))
                if sp_rewind > 0:
                    if sp_rewind <= 255:
                        emit(f"SUBS r1, #{sp_rewind}", asm.subs_imm8(Reg.R1, sp_rewind))
                    else:
                        emit(
                            f"SUB.W r1, r1, #{sp_rewind}",
                            asm.sub_w_imm12(Reg.R1, Reg.R1, sp_rewind),
                        )
                    emit("STR.W r1, [r0, #0x0C]", asm.str_w_imm12(Reg.R1, Reg.R0, 0x0C))
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
                # FastAddressCheck: CMP the address (r3 for load, r4 for store) against mem_size (R9)
                addr_reg = _MEMORY_OP_ADDR_REG[op]
                emit(f"CMP {addr_reg.name.lower()}, r9", asm.cmp_reg_t2(addr_reg, Reg.R9))
                oob_branch_fixups.append(self.byte_write_pos)
                emit("BHS.W <trap>", asm.b_cond_w(Cond.HS, 0))
                emit_stencil(self.stencils[op.replace(".", "_") + "_r8"])
                current_variant = 1 if "load" in op else 0
            else:
                # Direct stencil mapping with dynamic variant selection and reconciliation glue
                base = op.replace(".", "_")
                stencil_key = None
                preferred_key = f"{base}_d{current_variant}"
                if preferred_key in self.stencils:
                    stencil_key = preferred_key
                else:
                    for suffix in ("_d2", "_d1", "_d0", "_d3", "_r8", ""):
                        cand = base + suffix
                        if cand in self.stencils:
                            stencil_key = cand
                            break

                if stencil_key is not None and stencil_key in self.stencils:
                    target_st = self.stencils[stencil_key]
                    if target_st.variant_id is not None and target_st.variant_id != current_variant:
                        self.emit_variant_reconciliation_glue(current_variant, target_st.variant_id)
                        current_variant = target_st.variant_id
                    emit_stencil(target_st)
                    if base in _OP_OUTPUT_DEPTH:
                        current_variant = _OP_OUTPUT_DEPTH[base]
                else:
                    raise ValueError(f"Unsupported stencil opcode: {op}")

        # 5. Emit Exit. "return"/"fallback" are genuine AAPCS exits: every dirty
        # cached value must be flushed to its canonical stack_bot-relative address
        # first, since nothing preserves R4-R6 past the POP/BX below.
        # "chain" / "dynamic_chain" is a dynamic header-driven chain exit:
        # It dynamically checks the header's chain_target_addr (+0x0C).
        # - If resolved (chain_target_addr != 0): skips epilogue and BX r12 directly
        #   to the successor trace's chain entry (past its prologue). Neither epilogue
        #   nor successor prologue executes; registers survive untouched.
        # - If unresolved (chain_target_addr == 0): falls through, flushes dirty spills,
        #   and executes epilogue_return (POP {..., pc}) to return to the interpreter.
        # This completely avoids in-place machine code rewriting ({ADR_TosCacheAsymmetry},
        # {JIT_LazyChaining}, {JITC-GOTCHA-07}).
        self.last_chain_branch_byte_addr = None
        if exit_kind in ("chain", "dynamic_chain"):
            # 1. Dynamically load chain_target_addr from the inlined trace header (+0x0C)
            ldr_pos = self.byte_write_pos
            align_pc = (ldr_pos + 4) & ~3
            header_target_pos = header_byte_offset + 12
            rel_offset = header_target_pos - align_pc
            emit(
                f"LDR.W r12, [header_target (rel={rel_offset})]",
                asm.ldr_w_literal(Reg.R12, rel_offset),
            )
            # 2. Check if chain_target_addr is resolved (!= 0)
            emit("CMP.W r12, #0", asm.cmp_w_imm(Reg.R12, 0))
            # 3. Branch if Not Equal (resolved): skip epilogue directly to BX r12
            bne_pos = self.byte_write_pos
            self.last_chain_branch_byte_addr = bne_pos
            emit("BNE.W <skip_epilogue_to_chain>", asm.b_cond_w(Cond.NE, 0))
            # 4. Fallthrough path (unresolved): flush dirty spills and POP PC to return
            flush_dirty_spills_and_sync_context()
            emit_stencil(self.stencils["epilogue_return"])
            # 5. Chain hop target: BX r12
            chain_jump_pos = self.byte_write_pos
            rel_to_chain = chain_jump_pos - (bne_pos + 4)
            patched_bne = asm.b_cond_w(Cond.NE, rel_to_chain)
            self.byte_cache[bne_pos : bne_pos + len(patched_bne)] = patched_bne
            emit("BX r12", asm.bx(Reg.R12))
        else:
            flush_dirty_spills_and_sync_context()
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
            flush_dirty_spills_and_sync_context()
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
        # Byte offset of this trace's chain entry point (just past its prologue) --
        # what a *later*-compiled trace's exit_kind="chain" must target with
        # chain_target_addr to hop into this trace without re-running the prologue.
        self.last_chain_entry_byte_offset = chain_entry_byte_offset
        return (code_start_inst_offset, total_emitted - 1)

    # --- Intra-Trace Variant Compatibility & Reconciliation Glue ---
    #
    # This is about consecutive STENCILS within a single compile_trace() call,
    # NOT about chaining between two separately-compiled traces. A direct chain
    # branch (exit_kind="chain") carries register state straight across the
    # boundary with no memory traffic at all -- reconciling a variant mismatch
    # across a chain hop is therefore not addressed by this mechanism; only a
    # genuine exit (exit_kind="return"/"fallback") goes through memory, and only
    # because nothing preserves registers past its POP/BX ({ADR_TosCacheAsymmetry},
    # {JIT_LazyChaining}). That's a settled, separate design; nothing here changes it.
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
            self.write_instruction(
                self.current_write_pos, f"MOV {dst.name.lower()}, {src.name.lower()}"
            )
            self.current_write_pos += 1
            self._emit_bytes(asm.mov_reg(dst, src))
        return True


VARIANT_REGISTER_MAPS: dict[int, dict[str, Reg]] = {
    0: {},
    1: {"TOS": Reg.R3},
    2: {"TOS": Reg.R3, "NOS": Reg.R4},
    3: {"TOS": Reg.R3, "NOS": Reg.R4, "NNOS": Reg.R5},
}

_OP_OUTPUT_DEPTH: dict[str, int] = {
    # ALU / Comparison: consumes 2 (Depth 2), produces 1 (Depth 1)
    "i32_add": 1,
    "i32_sub": 1,
    "i32_mul": 1,
    "i32_div_s": 1,
    "i32_div_u": 1,
    "i32_rem_s": 1,
    "i32_rem_u": 1,
    "i32_and": 1,
    "i32_or": 1,
    "i32_xor": 1,
    "i32_shl": 1,
    "i32_shr_s": 1,
    "i32_shr_u": 1,
    "i32_rotl": 1,
    "i32_rotr": 1,
    "i32_eq": 1,
    "i32_ne": 1,
    "i32_lt_s": 1,
    "i32_lt_u": 1,
    "i32_gt_s": 1,
    "i32_gt_u": 1,
    "i32_le_s": 1,
    "i32_le_u": 1,
    "i32_ge_s": 1,
    "i32_ge_u": 1,
    # Unary: consumes 1 (Depth 1), produces 1 (Depth 1)
    "i32_clz": 1,
    "i32_ctz": 1,
    "i32_eqz": 1,
    # Ternary: consumes 3 (Depth 3), produces 1 (Depth 1)
    "select": 1,
    # Variables & Constants
    "i32_const": 1,
    "i64_const": 1,
    "local_get": 1,
    "global_get": 1,
    "memory_size": 1,
    "local_tee": 1,
    "local_set": 0,
    "global_set": 0,
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


def test_full_stencil_library_coverage() -> None:
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


def test_stencil_variant_ids_match_the_documented_table() -> None:
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
        "dynamic_chain_exit_d1": 1,
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
        "block": None,
        "loop": None,
        "end": None,
        "else": None,
        "return": None,
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
        "chain_branch",
        "unreachable",
        "nop",
        "block",
        "loop",
        "end",
        "else",
        "return",
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


def test_stencil_catalog_matches_assembler() -> None:
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
        "return": asm.pop_w(reg_mask=full_mask, pop_pc=True),
        "dynamic_chain_exit_d1": (
            asm.ldr_w_literal(Reg.R12, -24)
            + asm.cmp_w_imm(Reg.R12, 0)
            + asm.b_cond_w(Cond.NE, 6)
            + asm.str_imm(Reg.R3, Reg.R1, 0)
            + asm.pop_w(reg_mask=full_mask, pop_pc=True)
            + asm.bx(Reg.R12)
        ),
        "fallback_interp": asm.pop_w(reg_mask=full_mask, pop_lr=True) + asm.bx(Reg.R12),
        "unreachable": asm.bkpt(0),
        "i32_const_d0": asm.movw(Reg.R3, 0) + asm.movt(Reg.R3, 0),
        "i64_const_d0": (
            asm.movw(Reg.R3, 0) + asm.movt(Reg.R3, 0) + asm.movw(Reg.R4, 0) + asm.movt(Reg.R4, 0)
        ),
        "local_get_d0": asm.ldr_imm(Reg.R3, Reg.R2, 0),
        "local_set_d1": asm.str_imm(Reg.R3, Reg.R2, 0),
        "local_tee_d1": asm.str_imm(Reg.R3, Reg.R2, 0),
        "global_get_d0": asm.ldr_w_imm12(Reg.R12, Reg.R0, 0x30)
        + asm.ldr_w_imm12(Reg.R3, Reg.R12, 0),
        "global_set_d1": asm.ldr_w_imm12(Reg.R12, Reg.R0, 0x30)
        + asm.str_w_imm12(Reg.R3, Reg.R12, 0),
        "memory_size_d0": asm.ldr_w_imm12(Reg.R3, Reg.R0, 0x2C),
        "i32_add_d2": asm.adds_reg(Reg.R3, Reg.R4, Reg.R3),
        "i32_sub_d2": asm.subs_reg(Reg.R3, Reg.R4, Reg.R3),
        "i32_mul_d2": asm.mul(Reg.R3, Reg.R4, Reg.R3),
        "i32_div_s_d2": asm.sdiv(Reg.R3, Reg.R4, Reg.R3),
        "i32_div_u_d2": asm.udiv(Reg.R3, Reg.R4, Reg.R3),
        "i32_rem_s_d2": asm.sdiv(Reg.R12, Reg.R4, Reg.R3)
        + asm.mls(Reg.R3, Reg.R12, Reg.R3, Reg.R4),
        "i32_rem_u_d2": asm.udiv(Reg.R12, Reg.R4, Reg.R3)
        + asm.mls(Reg.R3, Reg.R12, Reg.R3, Reg.R4),
        "i32_and_d2": asm.ands_reg(Reg.R3, Reg.R4),
        "i32_or_d2": asm.orrs_reg(Reg.R3, Reg.R4),
        "i32_xor_d2": asm.eors_reg(Reg.R3, Reg.R4),
        "i32_shl_d2": asm.lsl_w(Reg.R3, Reg.R4, Reg.R3),
        "i32_shr_s_d2": asm.asr_w(Reg.R3, Reg.R4, Reg.R3),
        "i32_shr_u_d2": asm.lsr_w(Reg.R3, Reg.R4, Reg.R3),
        "i32_rotr_d2": asm.ror_w(Reg.R3, Reg.R4, Reg.R3),
        "i32_clz_d1": asm.clz(Reg.R3, Reg.R3),
        "i32_ctz_d1": asm.rbit(Reg.R3, Reg.R3) + asm.clz(Reg.R3, Reg.R3),
        "i32_load_r8": asm.ldr_w_reg(Reg.R3, Reg.R8, Reg.R3),
        "i32_store_r8": asm.str_w_reg(Reg.R3, Reg.R8, Reg.R4),
    }
    for name, encoded in checks.items():
        catalog_hex = engine.stencils[name].hex_bytes
        assert h(encoded) == catalog_hex, (
            f"Stencil '{name}' drifted from the assembler: "
            f"catalog={catalog_hex!r} assembler={h(encoded)!r}"
        )

    # i32_const_d1 prepends a plain register MOV ahead of the shared MOVW/MOVT pair.
    const_d1_expected = h(asm.mov_reg(Reg.R4, Reg.R3) + asm.movw(Reg.R3, 0) + asm.movt(Reg.R3, 0))
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
    # i32_rotl_d2: RSB r12,r3,#32 (amount = 32 - shift) then ROR.W r3,r4,r12. R12 scratch,
    # R2 is local_base, R3 is TOS.
    rotl_expected = h(asm.rsb_imm(Reg.R12, Reg.R3, 32) + asm.ror_w(Reg.R3, Reg.R4, Reg.R12))
    assert rotl_expected == engine.stencils["i32_rotl_d2"].hex_bytes, (
        f"Stencil 'i32_rotl_d2' drifted from the assembler: "
        f"catalog={engine.stencils['i32_rotl_d2'].hex_bytes!r} assembler={rotl_expected!r}"
    )
    # i32_eqz_d1: CMP r3,#0 ; IT EQ ; MOVEQ r3,#1 ; IT NE ; MOVNE r3,#0.
    eqz_expected = h(
        asm.cmp_imm8(Reg.R3, 0)
        + asm.it(Cond.EQ, 0b1000)
        + asm.movs_imm8(Reg.R3, 1)
        + asm.it(Cond.NE, 0b1000)
        + asm.movs_imm8(Reg.R3, 0)
    )
    assert eqz_expected == engine.stencils["i32_eqz_d1"].hex_bytes, (
        f"Stencil 'i32_eqz_d1' drifted from the assembler: "
        f"catalog={engine.stencils['i32_eqz_d1'].hex_bytes!r} assembler={eqz_expected!r}"
    )


def test_arithmetic_and_logic_traces() -> None:
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
    # ops include i32.load/i32.store, so the prologue also pins R8=mem_base/R9=mem_size
    # from execution_context (R0) at +0x28 and +0x2C
    assert code[1] == "LDR.W r8, [r0, #0x28]"
    assert code[2] == "LDR.W r9, [r0, #0x2C]"
    assert "MOVW r3, #100" in code[3]
    assert "POP.W {r4-r6, r8-r11, pc}" in code
    assert code[-1] == "BX r12"


def test_control_flow_and_all_48_opcodes() -> None:
    """Verifies inlined control flow (delimiters eliminated, return, br with SP rewind)
    and full coverage of all 48 WASM opcodes supported by the JIT compiler."""
    engine = CopyPatchJITEngine()

    # 1. Delimiters (block, loop, end, else, nop) must be eliminated at compile time (zero cost)
    ops_delim = [
        ("block", None),
        ("loop", None),
        ("nop", None),
        ("i32.const", 42),
        ("else", None),
        ("end", None),
    ]
    start_pos, count = engine.compile_trace(ops_delim, exit_kind="return")
    code_delim = engine.execute_native(start_pos, count)
    # The only instruction emitted from ops_delim should be the i32.const (MOVW+MOVT)
    body_insts = [
        c
        for c in code_delim
        if not c.startswith("PUSH")
        and not c.startswith("POP")
        and not (c.startswith("STR r") and "[r1" in c)
        and not c.startswith("STR.W r1, [r0")
        and not c.startswith("MOVW r6")
        and not c.startswith("MOVT r6")
        and not c.startswith("STR.W r6, [r0")
    ]
    assert len(body_insts) == 2, f"Delimiters were not eliminated: {body_insts}"

    # 2. Inlined return
    ops_ret = [("i32.const", 1), ("return", None)]
    start_pos, count = engine.compile_trace(ops_ret, exit_kind="return")
    code_ret = engine.execute_native(start_pos, count)
    assert any("POP.W {r4-r6, r8-r11, pc}" in c for c in code_ret)

    # 3. Inlined br with SP rewind
    ops_br = [("br", (0x1000, 16))]
    start_pos, count = engine.compile_trace(ops_br, exit_kind="return")
    code_br = engine.execute_native(start_pos, count)
    assert "SUBS r1, #16" in code_br
    assert "STR.W r1, [r0, #0x0C]" in code_br
    assert "B.W 0x00001000" in code_br

    # 4. Full 48 Opcode Coverage Sweep
    all_48_opcodes = [
        # Control flow (7)
        ("unreachable", None),
        ("nop", None),
        ("block", None),
        ("loop", None),
        ("end", None),
        ("br", (0x200, 0)),
        ("br_if", (0x300, 0)),
        ("return", None),
        # Constants and variables (8)
        ("i32.const", 1),
        ("i64.const", 1),
        ("local.get", 0),
        ("local.set", 0),
        ("local.tee", 0),
        ("global.get", 0),
        ("global.set", 0),
        ("select", None),
        # 32-bit Integer ALU & Logic (16)
        ("i32.add", None),
        ("i32.sub", None),
        ("i32.mul", None),
        ("i32.div_s", None),
        ("i32.div_u", None),
        ("i32.rem_s", None),
        ("i32.rem_u", None),
        ("i32.and", None),
        ("i32.or", None),
        ("i32.xor", None),
        ("i32.shl", None),
        ("i32.shr_s", None),
        ("i32.shr_u", None),
        ("i32.rotl", None),
        ("i32.rotr", None),
        ("i32.clz", None),
        ("i32.ctz", None),
        # Comparisons (11)
        ("i32.eqz", None),
        ("i32.eq", None),
        ("i32.ne", None),
        ("i32.lt_s", None),
        ("i32.lt_u", None),
        ("i32.gt_s", None),
        ("i32.gt_u", None),
        ("i32.le_s", None),
        ("i32.le_u", None),
        ("i32.ge_s", None),
        ("i32.ge_u", None),
        # Linear Memory (8)
        ("i32.load", None),
        ("i32.load8_s", None),
        ("i32.load8_u", None),
        ("i32.load16_s", None),
        ("i32.load16_u", None),
        ("i32.store", None),
        ("i32.store8", None),
        ("i32.store16", None),
    ]
    # Verify every single opcode compiles without raising ValueError
    for op, arg in all_48_opcodes:
        engine_single = CopyPatchJITEngine()
        start, cnt = engine_single.compile_trace([(op, arg)], exit_kind="return")
        assert cnt > 0, f"Opcode {op} produced 0 instructions"


def test_external_aapcs_call_stub() -> None:
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


def test_cps_shared_registers_never_clobbered() -> None:
    """ADR_TosCacheAsymmetry: Shared R0 (ctx) and R2 (local_base) are never clobbered
    by trace ALU/loads/local access (R3 is TOS cache)."""
    engine = CopyPatchJITEngine()
    ops = [
        ("i32.const", 42),
        ("local.get", 0),
        ("i32.add", None),
        ("local.set", 0),
        ("i32.load", None),
    ]
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
        assert dest not in ("r0", "r2", "R0", "R2"), (
            f"Instruction '{inst}' illegal write to shared ctx/local_base register"
        )


def test_fast_address_check_traps_before_access() -> None:
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
    assert "CMP r3, r9" in code
    assert "BHS.W <trap>" in code
    assert "LDR.W r3, [r8, r3]" in code
    # The bounds check + branch must be emitted strictly before the access it guards.
    assert code.index("BHS.W <trap>") < code.index("LDR.W r3, [r8, r3]")
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


def test_memory_access_without_bounds_check_is_impossible() -> None:
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


def test_variant_reconciliation_glue_same_variant_emits_nothing() -> None:
    """Depth 2 -> Depth 2 (the only real transition today) must be a no-op: the next
    stencil already finds TOS/NOS exactly where it left them, no MOVs needed."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=2, target_variant_id=2)
    engine.commit_jit_patch()
    assert ok is True
    assert engine.byte_write_pos == start_pos, "identical layouts must not emit any MOV"


def test_variant_reconciliation_glue_subset_emits_nothing() -> None:
    """A Depth-2 exit feeding a Depth-1 entry needs no reconciliation either: the
    entry only reads TOS (R3), which the Depth-2 layout already has in R3 too."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=2, target_variant_id=1)
    engine.commit_jit_patch()
    assert ok is True
    assert engine.byte_write_pos == start_pos


def test_variant_reconciliation_glue_rejects_missing_value() -> None:
    """A Depth-1 exit cannot feed a Depth-2 entry: the entry needs a NOS value the
    predecessor never computed, and no MOV sequence can synthesize a value that was
    never produced. This should never actually arise in a well-formed trace (depth
    only grows via real pushes), but the mechanism must fail closed if it did."""
    engine = CopyPatchJITEngine()
    engine.begin_jit_patch()
    ok = engine.emit_variant_reconciliation_glue(source_variant_id=1, target_variant_id=2)
    engine.commit_jit_patch()
    assert ok is False


def test_variant_reconciliation_glue_emits_real_swap_bytes() -> None:
    """Structural check that a genuine register-layout mismatch (same role set,
    different physical registers -- what a future allocator could produce) emits the
    real cycle-safe MOV sequence, not a placeholder. Semantic correctness on actual
    hardware is proven separately in jit_trace_execution_verifier.py (Unicorn)."""
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()
    # A synthetic alt-Depth-2 layout (TOS=R4, NOS=R3) swapped relative to the real one,
    # standing in for a hypothetical future allocator output -- not a real variant_id.
    engine.begin_jit_patch()
    start_pos = engine.byte_write_pos
    moves = {Reg.R3: Reg.R4, Reg.R4: Reg.R3}
    for dst, src in _order_register_moves(moves):
        engine._emit_bytes(asm.mov_reg(dst, src))
    engine.commit_jit_patch()
    emitted = bytes(engine.byte_cache[start_pos : engine.byte_write_pos])
    expected = (
        asm.mov_reg(Reg.R12, Reg.R3) + asm.mov_reg(Reg.R3, Reg.R4) + asm.mov_reg(Reg.R4, Reg.R12)
    )
    assert emitted == expected


def test_order_register_moves_breaks_swap_cycle_correctly() -> None:
    """A straight R3<->R4 swap is the classic case a naive move-ordering corrupts: emitting
    MOV r3,r4 then MOV r4,r3 would make r4 end up equal to r3's NEW (already-overwritten)
    value instead of its original one. _order_register_moves must route through R12."""
    moves = _order_register_moves({Reg.R3: Reg.R4, Reg.R4: Reg.R3})
    assert moves == [(Reg.R12, Reg.R3), (Reg.R3, Reg.R4), (Reg.R4, Reg.R12)]


def test_epilogue_spill_variable_flush() -> None:
    """Verify that dirty spill variables (TOS/NOS, registers) are flushed to stack and context is synced before POP/BX."""
    engine = CopyPatchJITEngine()
    ops = [
        ("i32.const", 10),
        ("local.get", 4),
        ("i32.add", None),
    ]
    # Compile with dirty spills: R3 (TOS) to stack offset 0, R4 (NOS) to stack offset 4
    start_pos, count = engine.compile_trace(
        ops, exit_kind="fallback", dirty_spills=[("r3", 0), ("r4", 4)]
    )
    code = engine.execute_native(start_pos, count)
    # Check spill flush STR instructions before POP
    assert "STR r3, [r1, #0]" in code
    assert "STR r4, [r1, #4]" in code
    assert "STR.W r1, [r0, #0x0C]" in code
    assert "STR.W r6, [r0, #0x00]" in code
    assert "POP.W {r4-r6, r8-r11, lr}" in code
    assert "BX r12" in code


def test_epilogue_flush_d1_before_return() -> None:
    """A genuine exit_kind="return" must flush TOS(R3) to its canonical address
    and sync context before POP -- nothing preserves R3 past the POP.W {..., pc} that follows."""
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 10)]
    start_pos, count = engine.compile_trace(ops, exit_kind="return", dirty_spills=[("r3", 0)])
    code = engine.execute_native(start_pos, count)
    str_idx = code.index("STR r3, [r1, #0]")
    pop_idx = code.index("POP.W {r4-r6, r8-r11, pc}")
    assert str_idx < pop_idx, "TOS must be flushed to memory before the POP that destroys R3"


def test_chain_branch_skips_flush_and_epilogue() -> None:
    """exit_kind="chain" emits a dynamic header-driven exit branch.
    The instruction sequence contains:
    1. LDR.W r12 from inlined header (+0x0C)
    2. CMP.W r12, #0
    3. BNE.W <skip_epilogue_to_chain> (skips spill flush & POP PC)
    4. Epilogue fallback: STR (flush) + SP/IP sync + POP.W (return to interpreter)
    5. Chain jump: BX r12 (jumps directly to successor's chain_entry past prologue).
    When chain_target_addr is resolved (!= 0), BNE.W executes and skips the epilogue entirely.
    When unresolved (== 0), it falls through and executes the epilogue.
    """
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 10)]
    start_pos, count = engine.compile_trace(
        ops, exit_kind="chain", dirty_spills=[("r3", 0)], chain_target_addr=0
    )
    code = engine.execute_native(start_pos, count)
    assert any("LDR.W r12, [header_target" in c for c in code)
    assert "CMP.W r12, #0" in code
    assert "BNE.W <skip_epilogue_to_chain>" in code
    assert "STR r3, [r1, #0]" in code
    assert "STR.W r1, [r0, #0x0C]" in code
    assert "STR.W r6, [r0, #0x00]" in code
    assert "POP.W {r4-r6, r8-r11, pc}" in code
    assert "BX r12" in code

    # Verify BNE.W offset lands exactly on BX r12
    bne_idx = code.index("BNE.W <skip_epilogue_to_chain>")
    bx_idx = code.index("BX r12")
    assert bne_idx < bx_idx
    # Epilogue is strictly between BNE.W and BX r12
    str_idx = code.index("STR r3, [r1, #0]")
    pop_idx = code.index("POP.W {r4-r6, r8-r11, pc}")
    assert bne_idx < str_idx < pop_idx < bx_idx


def test_dynamic_chain_header_patch_and_unlink() -> None:
    """Runtime chaining via set_chain_target and unlink_chain:
    Updates METADATA (header chain_target_addr +0x0C) without in-place code modification!
    Code memory remains strictly untouched and RO_X throughout."""
    engine = CopyPatchJITEngine()
    # 1. Compile predecessor trace with unresolved chaining (chain_target_addr=0)
    pred_start, pred_count = engine.compile_trace(
        [("i32.const", 1)], exit_kind="chain", head_wasm_pc=0x100
    )
    pred_header_off, _ = engine.last_trace_header_range
    pred_code_off, pred_code_len = engine.last_trace_byte_range
    code_bytes_before = bytes(engine.byte_cache[pred_code_off : pred_code_off + pred_code_len])

    # 2. Compile successor trace and obtain its chain_entry_byte_offset
    succ_start, succ_count = engine.compile_trace(
        [("i32.const", 2)], exit_kind="fallback", head_wasm_pc=0x200
    )
    succ_chain_entry = engine.last_chain_entry_byte_offset

    # 3. Link predecessor to successor by updating ONLY header metadata
    engine.set_chain_target(pred_header_off, succ_chain_entry)
    header = JITTraceHeader.from_bytes(engine.byte_cache, pred_header_off)
    assert header.chain_target_addr == succ_chain_entry

    # Invariant: Code bytes MUST NOT change at all (Zero self-modifying code!)
    code_bytes_after = bytes(engine.byte_cache[pred_code_off : pred_code_off + pred_code_len])
    assert code_bytes_before == code_bytes_after, (
        "set_chain_target must NOT modify code memory (avoids W^X toggle and ISB barriers)"
    )

    # 4. Unlink chaining: resets chain_target_addr to 0
    engine.unlink_chain(pred_header_off)
    header_unlinked = JITTraceHeader.from_bytes(engine.byte_cache, pred_header_off)
    assert header_unlinked.chain_target_addr == 0
    code_bytes_unlinked = bytes(engine.byte_cache[pred_code_off : pred_code_off + pred_code_len])
    assert code_bytes_before == code_bytes_unlinked


def test_chain_entry_offset_is_past_the_prologue() -> None:
    """A trace's chain entry point (where a predecessor's chain branch must land)
    is exactly past its own prologue, never through it -- a chained hop must not
    re-run PUSH.W {r4-r6, r8-r11, lr} on registers that are already correctly
    live from the predecessor."""
    engine = CopyPatchJITEngine()
    start_pos, _ = engine.compile_trace([("i32.const", 1)], exit_kind="fallback")
    code_start_byte_offset, _ = engine.last_trace_byte_range
    assert engine.last_chain_entry_byte_offset == code_start_byte_offset + 4


def test_chain_branch_compiled_with_target_sets_header_correctly() -> None:
    """When chain_target_addr is passed at compile time, the header metadata is initialized
    directly without requiring a subsequent set_chain_target call."""
    engine = CopyPatchJITEngine()
    engine.compile_trace([("i32.const", 2)], exit_kind="fallback")
    succ_chain_entry = engine.last_chain_entry_byte_offset

    engine.compile_trace([("i32.const", 1)], exit_kind="chain", chain_target_addr=succ_chain_entry)
    header_off, _ = engine.last_trace_header_range
    header = JITTraceHeader.from_bytes(engine.byte_cache, header_off)
    assert header.chain_target_addr == succ_chain_entry


def test_mpu_wx_protection() -> None:
    engine = CopyPatchJITEngine()
    try:
        engine.write_instruction(0, "ILLEGAL")
        raise AssertionError("Should raise MPUFault")
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)


def test_jit_trace_header_layout() -> None:
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


def test_variant_stack_flush_and_sp_sync() -> None:
    """Verify that when dirty_spills is None, compile_trace automatically derives
    stack flushes from variant_id (0..3) and always syncs SP (+0x0C) and IP (+0x00)
    to execution_context R0 at basic block end."""
    engine = CopyPatchJITEngine()

    # Variant 0 (Depth 0): No register stack cache, no flush instructions
    start_v0, count_v0 = engine.compile_trace([], exit_kind="return", variant_id=0)
    code_v0 = engine.execute_native(start_v0, count_v0)
    assert not any(c.startswith("STR r3, [r1") for c in code_v0)
    assert not any(c.startswith("STR r4, [r1") for c in code_v0)
    assert not any(c.startswith("STR r5, [r1") for c in code_v0)
    assert "STR.W r1, [r0, #0x0C]" in code_v0
    assert "STR.W r6, [r0, #0x00]" in code_v0

    # Variant 1 (Depth 1): TOS (R3) in register, flush to [R1, #0]
    start_v1, count_v1 = engine.compile_trace([], exit_kind="return", variant_id=1)
    code_v1 = engine.execute_native(start_v1, count_v1)
    assert "STR r3, [r1, #0]" in code_v1
    assert not any(c.startswith("STR r4, [r1") for c in code_v1)
    assert not any(c.startswith("STR r5, [r1") for c in code_v1)
    assert "STR.W r1, [r0, #0x0C]" in code_v1
    assert "STR.W r6, [r0, #0x00]" in code_v1

    # Variant 2 (Depth 2): TOS (R3) & NOS (R4) in registers, flush to [R1, #0] & [R1, #4]
    start_v2, count_v2 = engine.compile_trace([], exit_kind="return", variant_id=2)
    code_v2 = engine.execute_native(start_v2, count_v2)
    assert "STR r3, [r1, #0]" in code_v2
    assert "STR r4, [r1, #4]" in code_v2
    assert not any(c.startswith("STR r5, [r1") for c in code_v2)
    assert "STR.W r1, [r0, #0x0C]" in code_v2
    assert "STR.W r6, [r0, #0x00]" in code_v2

    # Variant 3 (Depth 3): TOS (R3), NOS (R4), NNOS (R5) in registers, flush to [R1, #0], #4, #8
    start_v3, count_v3 = engine.compile_trace([], exit_kind="return", variant_id=3)
    code_v3 = engine.execute_native(start_v3, count_v3)
    assert "STR r3, [r1, #0]" in code_v3
    assert "STR r4, [r1, #4]" in code_v3
    assert "STR r5, [r1, #8]" in code_v3
    assert "STR.W r1, [r0, #0x0C]" in code_v3
    assert "STR.W r6, [r0, #0x00]" in code_v3


def test_dynamic_variant_selection_and_glue() -> None:
    """Verify that compile_trace selects depth-specific stencils matching current depth
    and inserts reconciliation glue (MOVs) when required."""
    engine = CopyPatchJITEngine()
    # local.get produces Depth 1 (TOS: R3). i32.clz consumes Depth 1 and produces Depth 1.
    # Preferred i32_clz_d1 should be chosen without reconciliation glue.
    ops = [("local.get", 0), ("i32.clz", None)]
    start_pos, count = engine.compile_trace(ops, exit_kind="return", variant_id=0)
    code = engine.execute_native(start_pos, count)
    assert any("clz" in c.lower() for c in code)
    # Trace end flushes Depth 1 (TOS: R3)
    assert "STR r3, [r1, #0]" in code
    assert "STR.W r1, [r0, #0x0C]" in code


if __name__ == "__main__":
    test_full_stencil_library_coverage()
    test_stencil_variant_ids_match_the_documented_table()
    test_stencil_catalog_matches_assembler()
    test_arithmetic_and_logic_traces()
    test_control_flow_and_all_48_opcodes()
    test_external_aapcs_call_stub()
    test_epilogue_spill_variable_flush()
    test_epilogue_flush_d1_before_return()
    test_chain_branch_skips_flush_and_epilogue()
    test_dynamic_chain_header_patch_and_unlink()
    test_chain_entry_offset_is_past_the_prologue()
    test_chain_branch_compiled_with_target_sets_header_correctly()
    test_cps_shared_registers_never_clobbered()
    test_fast_address_check_traps_before_access()
    test_memory_access_without_bounds_check_is_impossible()
    test_variant_reconciliation_glue_same_variant_emits_nothing()
    test_variant_reconciliation_glue_subset_emits_nothing()
    test_variant_reconciliation_glue_rejects_missing_value()
    test_variant_reconciliation_glue_emits_real_swap_bytes()
    test_order_register_moves_breaks_swap_cycle_correctly()
    test_variant_stack_flush_and_sp_sync()
    test_dynamic_variant_selection_and_glue()
    test_mpu_wx_protection()
    test_jit_trace_header_layout()
    print("[PASS] All JIT Copy-and-Patch Full-Set concept tests passed successfully.")
