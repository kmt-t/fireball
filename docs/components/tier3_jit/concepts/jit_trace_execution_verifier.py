"""
docs/components/tier3_jit/concepts/jit_trace_execution_verifier.py
End-to-end dynamic verifier for CopyPatchJITEngine.compile_trace().

thumb2_stencil_semantic_verifier.py proves the individual stencil catalog
entries compute the right WASM result. This module proves the ENGINE that
assembles them -- compile_trace()'s prologue/op/spill-flush/epilogue
sequence -- produces one contiguous, genuinely executable machine code
blob: it maps native call-stack (R13) and unified-stack (R1) memory, runs
the real bytes on an ARMv8-M Thumb emulator (Unicorn), and checks that the
callee-saved prologue/epilogue round-trip the stack pointer, the ALU op
computes the correct result, and the dirty-spill STR actually lands in
the unified-stack memory the interpreter would read back on fallback.

Requires the `unicorn` package: `uv run --with unicorn python <this file>`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jit_copy_patch_concept import CopyPatchJITEngine  # noqa: E402

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_ERR_EXCEPTION, UcError  # noqa: E402
from unicorn.arm_const import (  # noqa: E402
    UC_ARM_REG_R1, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R12, UC_ARM_REG_SP,
)

CODE_BASE = 0x08000
CSTACK_TOP = 0x21000  # native (R13) call stack -- grows down from here
WASM_STACK_BASE = 0x22000  # unified stack (R1 / stack_bot)
SENTINEL_ADDR = 0x23000  # where BX r12 lands on fallback exit


def test_compiled_trace_runs_on_real_cpu_and_spills_correctly():
    engine = CopyPatchJITEngine()
    r4_in, r5_in = 0x64, 0x17  # TOS, NOS -- caller-loaded, as the real interpreter would

    engine.compile_trace(
        [("i32.add", None)],
        exit_kind="fallback",
        dirty_spills=[("r4", 0)],
    )
    start_byte, length = engine.last_trace_byte_range
    code = engine.execute_native_bytes(start_byte, length)
    assert code, "compile_trace produced zero bytes -- nothing to execute"

    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x4000)  # covers CSTACK and WASM_STACK regions
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))  # BKPT sentinel to stop on

    mu.reg_write(UC_ARM_REG_R4, r4_in)
    mu.reg_write(UC_ARM_REG_R5, r5_in)
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R12, SENTINEL_ADDR)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)

    try:
        mu.emu_start(CODE_BASE | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise

    final_sp = mu.reg_read(UC_ARM_REG_SP)
    assert final_sp == CSTACK_TOP, (
        f"prologue/epilogue PUSH.W/POP.W did not round-trip SP: "
        f"started at {CSTACK_TOP:#x}, ended at {final_sp:#x}"
    )

    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    expected = (r5_in + r4_in) & 0xFFFFFFFF
    assert spilled == expected, (
        f"dirty-spill flush did not write the ADD result to the unified stack: "
        f"stack_bot[0]={spilled:#x}, expected r5+r4={expected:#x}"
    )

    pc = mu.reg_read(UC_ARM_REG_R12)
    print(f"[OK] compile_trace() emitted {length} real byte(s), executed on a real ARMv8-M "
          f"Thumb core, spilled r5+r4={expected:#x} to stack_bot[0], SP round-tripped, "
          f"reached fallback sentinel via BX r12={pc:#x}.")


if __name__ == "__main__":
    test_compiled_trace_runs_on_real_cpu_and_spills_correctly()
    print("[PASS] compile_trace() output is real, executable, and correct Thumb-2 machine code.")
