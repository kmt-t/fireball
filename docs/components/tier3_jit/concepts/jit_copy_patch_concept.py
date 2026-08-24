"""
docs/components/tier3_jit/concepts/jit_copy_patch_concept.py
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

        # Stencil Library.
        #
        # Register convention (ADR_TosCacheAsymmetry / ContextPointerRegister):
        #   R0 = ip         (WASM PC)          -- shared with the interpreter, never clobbered
        #   R1 = stack_bot  (unified stack)    -- shared with the interpreter, never clobbered
        #   R2 = env        (vsoc_runtime*)    -- shared with the interpreter, never clobbered
        #   R3 = scratch                       -- free for the trace to use
        #   R4 = TOS, R5 = NOS                 -- JIT-trace-local operand cache. The interpreter
        #                                         does NOT share this, so the prologue fills it
        #                                         from the unified stack and the epilogue flushes
        #                                         it back before tail-jumping into a handler.
        # No C stack frame is created: the trace never touches SP (InterpreterContextStackless).
        self.stencils: dict[str, Stencil] = {
            "prologue": Stencil(
                "prologue",
                ["LDR R4, [R1, #__TOS_OFF__]", "LDR R5, [R1, #__NOS_OFF__]"],
                {"tos_off": 0, "nos_off": 1}
            ),
            "i32_const": Stencil(
                "i32_const",
                ["STR R5, [R1, #__SPILL_OFF__]", "MOV R5, R4",
                 "MOVW R4, #__IMM16_LO__", "MOVT R4, #__IMM16_HI__"],
                {"spill_off": 0, "imm": 2}
            ),
            "i32_add": Stencil(
                "i32_add",
                ["ADD R4, R5, R4", "LDR R5, [R1, #__FILL_OFF__]"],
                {"fill_off": 1}
            ),
            "epilogue": Stencil(
                "epilogue",
                ["STR R4, [R1, #__TOS_OFF__]", "STR R5, [R1, #__NOS_OFF__]", "BX R3"],
                {"tos_off": 0, "nos_off": 1}
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
                # Relocation patch: spill displaced NOS, shift TOS -> NOS, load immediate into TOS
                patched = [
                    "STR R5, [R1, #0]",
                    st.code[1],
                    f"MOVW R4, #{imm & 0xFFFF}",
                    f"MOVT R4, #{(imm >> 16) & 0xFFFF}",
                ]
                for inst in patched:
                    self.write_instruction(self.current_write_pos, inst)
                    self.current_write_pos += 1
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
    assert count == 11  # Prologue(2) + Const(4) + Add(2) + Epilogue(3)
    assert engine.mpu_attr == MPUAttribute.RO_X
    assert engine.barrier_flushes == 1

    # Execute generated native instructions
    emitted_code = engine.execute_native(start_pos, count)
    # Trace entry fills the JIT-local TOS/NOS cache from the unified stack.
    assert emitted_code[0] == "LDR R4, [R1, #__TOS_OFF__]"
    assert "MOVW R4, #42" in emitted_code[4]
    # Trace exit flushes the cache back and tail-jumps via the scratch register.
    assert emitted_code[-3] == "STR R4, [R1, #__TOS_OFF__]"
    assert emitted_code[-1] == "BX R3"


def test_cps_registers_are_never_clobbered_by_a_trace():
    """ADR_TosCacheAsymmetry: R0/R1/R2 carry (ip, stack_bot, env) across the whole
    JIT <-> interpreter boundary, so no emitted instruction may write to them. A
    trace that clobbered one would corrupt the continuation it tail-jumps into."""
    engine = CopyPatchJITEngine()
    start_pos, count = engine.compile_basic_block([("i32.const", 42), ("i32.add", None)])
    emitted_code = engine.execute_native(start_pos, count)

    for inst in emitted_code:
        mnemonic, _, operands = inst.partition(" ")
        if mnemonic in ("STR", "BX"):
            continue  # these read their first operand, they do not write it
        dest = operands.split(",")[0].strip()
        assert dest not in ("R0", "R1", "R2"), \
            f"trace instruction '{inst}' writes to a shared CPS register"


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
    test_cps_registers_are_never_clobbered_by_a_trace()
    test_mpu_wx_protection_violation()
    print("[PASS] All JIT Copy-and-Patch concept tests passed successfully.")
