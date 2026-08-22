"""
docs/components/tier2_jit/concepts/jit_copy_patch_concept.py
Reference Concept Implementation: Copy-and-Patch JIT Engine & MPU W^X Transaction Protocol
- Fast binary code generation via pre-compiled Stencil templates
- Direct relocation patching (immediate constants, branch offsets)
- Hardware-enforced MPU W^X attribute switching protocol (RW_XN <-> RO_X)
- Transaction batching to minimize DSB/ISB pipeline flush overhead
"""

from typing import Any


class MPUAttribute:
    RO_X = "RO_X"      # Read-Only + Executable (Native code execution)
    RW_XN = "RW_XN"    # Read-Write + Non-Executable (Patching/Compilation)
    NO_ACCESS = "NO_ACCESS"


class MPUFault(Exception):
    pass


class Stencil:
    """Pre-compiled binary template with relocation hole descriptors."""
    def __init__(self, name: str, code: list[str], reloc_offsets: dict[str, int]):
        self.name = name
        self.code = list(code)
        self.reloc_offsets = dict(reloc_offsets)


class CopyPatchJITEngine:
    def __init__(self, cache_size: int = 1024):
        self.cache_size = cache_size
        self.code_cache: list[str] = ["NOP"] * cache_size
        self.mpu_attr: str = MPUAttribute.RO_X  # Default state: RO_X
        self.barrier_flushes: int = 0
        self.current_write_pos: int = 0

        # Stencil Library
        self.stencils: dict[str, Stencil] = {
            "prologue": Stencil(
                "prologue",
                ["PUSH {R4, LR}", "SUB SP, SP, #16"],
                {}
            ),
            "i32_const": Stencil(
                "i32_const",
                ["MOVW R0, #__IMM16_LO__", "MOVT R0, #__IMM16_HI__", "STR R0, [SP, #__STACK_OFF__]"],
                {"imm": 0, "stack_off": 2}
            ),
            "i32_add": Stencil(
                "i32_add",
                ["LDR R0, [SP, #0]", "LDR R1, [SP, #4]", "ADD R0, R0, R1", "STR R0, [SP, #0]"],
                {}
            ),
            "epilogue": Stencil(
                "epilogue",
                ["ADD SP, SP, #16", "POP {R4, PC}"],
                {}
            ),
        }

    # --- MPU W^X Transaction Protocol ---

    def begin_jit_patch(self):
        """
        Switches JIT Code Cache MPU attribute to RW + XN.
        Executable permissions are strictly revoked before writing.
        """
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_jit_patch(self):
        """
        Restores JIT Code Cache MPU attribute to RO + X.
        Issues DSB (Data Synchronization Barrier) & ISB (Instruction Synchronization Barrier).
        """
        assert self.mpu_attr == MPUAttribute.RW_XN, "Must be in patching mode before commit"
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1  # Hardware: __DSB(); __ISB();

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

    # --- Copy-and-Patch Compilation ---

    def compile_basic_block(self, wasm_ops: list[tuple[str, Any]]) -> tuple[int, int]:
        """
        Batches stencil copy & relocation patching inside a single W^X transaction.
        Returns (start_offset, total_instructions).
        """
        start_offset = self.current_write_pos

        # 1. Begin W^X Transaction (RW + XN)
        self.begin_jit_patch()

        # 2. Emit Prologue
        prologue = self.stencils["prologue"]
        for inst in prologue.code:
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1

        # 3. Emit WASM Ops
        for op, arg in wasm_ops:
            if op == "i32.const":
                st = self.stencils["i32_const"]
                imm = arg
                # Relocation patch: IMM and stack offset
                i0 = f"MOVW R0, #{imm & 0xFFFF}"
                i1 = f"MOVT R0, #{(imm >> 16) & 0xFFFF}"
                i2 = "STR R0, [SP, #0]"
                self.write_instruction(self.current_write_pos, i0)
                self.write_instruction(self.current_write_pos + 1, i1)
                self.write_instruction(self.current_write_pos + 2, i2)
                self.current_write_pos += 3
            elif op == "i32.add":
                st = self.stencils["i32_add"]
                for inst in st.code:
                    self.write_instruction(self.current_write_pos, inst)
                    self.current_write_pos += 1

        # 4. Emit Epilogue
        epilogue = self.stencils["epilogue"]
        for inst in epilogue.code:
            self.write_instruction(self.current_write_pos, inst)
            self.current_write_pos += 1

        # 5. Commit W^X Transaction (RO + X + Barriers)
        self.commit_jit_patch()

        total_emitted = self.current_write_pos - start_offset
        return (start_offset, total_emitted)


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================

def test_copy_patch_compilation_and_execution():
    engine = CopyPatchJITEngine()

    wasm_ops = [
        ("i32.const", 42),
        ("i32.add", None),
    ]

    # Compile basic block
    start_pos, count = engine.compile_basic_block(wasm_ops)
    assert count == 11  # Prologue(2) + Const(3) + Add(4) + Epilogue(2)
    assert engine.mpu_attr == MPUAttribute.RO_X
    assert engine.barrier_flushes == 1

    # Execute generated native instructions
    emitted_code = engine.execute_native(start_pos, count)
    assert emitted_code[0] == "PUSH {R4, LR}"
    assert "MOVW R0, #42" in emitted_code[2]
    assert emitted_code[-1] == "POP {R4, PC}"


def test_mpu_wx_protection_violation():
    engine = CopyPatchJITEngine()

    # 1. Attempt to write to JIT cache without begin_jit_patch (RO_X mode) -> MPU Fault
    try:
        engine.write_instruction(0, "ILLEGAL_WRITE")
        assert False, "Should have thrown MPUFault"
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)

    # 2. Attempt to execute while in patching mode (RW_XN mode) -> MPU Fault
    engine.begin_jit_patch()
    try:
        engine.execute_native(0, 4)
        assert False, "Should have thrown MPUFault"
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)


if __name__ == "__main__":
    test_copy_patch_compilation_and_execution()
    test_mpu_wx_protection_violation()
    print("[PASS] All JIT Copy-and-Patch concept tests passed successfully.")
