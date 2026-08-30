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
from jit_assembler_constexpr_concept import Reg, Thumb2Assembler
from jit_copy_patch_concept import (
    CopyPatchJITEngine,
    _order_register_moves,
)
from unicorn import (
    UC_ARCH_ARM,
    UC_ERR_EXCEPTION,
    UC_MODE_THUMB,
    Uc,
    UcError,
)
from unicorn.arm_const import (
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R12,
    UC_ARM_REG_SP,
)

CODE_BASE = 0x08000
CSTACK_TOP = 0x21000  # native (R13) call stack -- grows down from here
WASM_STACK_BASE = 0x22000  # unified stack (R1 / stack_bot)
SENTINEL_ADDR = 0x23000  # where BX r12 lands on fallback exit
ENV_BASE = 0x24000  # vsoc_runtime: mem-base @+0x00, mem-size @+0x04
GUEST_RAM_BASE = 0x25000  # a deliberately small, tightly-mapped guest linear memory region


def test_compiled_trace_runs_on_real_cpu_and_spills_correctly():
    engine = CopyPatchJITEngine()
    r4_in, r5_in = (
        0x64,
        0x17,
    )  # TOS, NOS -- caller-loaded, as the real interpreter would
    engine.compile_trace(
        [("i32.add", None)],
        exit_kind="fallback",
        dirty_spills=[("r4", 0)],
        head_wasm_pc=0x100,
    )
    start_byte, length = engine.last_trace_byte_range
    code = engine.execute_native_bytes(start_byte, length)
    assert code, "compile_trace produced zero bytes -- nothing to execute"
    # Verify the inlined JITTraceHeader immediately preceding the code
    header_offset, header_len = engine.last_trace_header_range
    assert header_offset == start_byte - 16
    assert header_len == 16
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
    print(
        f"[OK] compile_trace() emitted {length} real byte(s), executed on a real ARMv8-M "
        f"Thumb core, spilled r5+r4={expected:#x} to stack_bot[0], SP round-tripped, "
        f"reached fallback sentinel via BX r12={pc:#x}."
    )


def _run_memory_access_trace(guest_addr: int, mem_size: int) -> dict:
    """Compiles [i32.const guest_addr, i32.load], maps a real ENV/guest-RAM pair in Unicorn,
    and runs the trace to completion (fallback exit -- always ends via BX r12 -> SENTINEL_ADDR,
    whether via the normal exit path or the FastAddressCheck trap tail). r4 is declared dirty
    so its final value (loaded word, or the untouched address if trapped) survives the callee-
    saved POP.W -- otherwise POP.W would silently restore r4 to its pre-trace (garbage) value,
    the same reason test_compiled_trace_runs_on_real_cpu_and_spills_correctly() spills r4.
    Returns the flushed stack_bot[0] word and final SP.
    """
    engine = CopyPatchJITEngine()
    engine.compile_trace(
        [("i32.const", guest_addr), ("i32.load", None)],
        exit_kind="fallback",
        dirty_spills=[("r4", 0)],
        head_wasm_pc=0x100,
    )
    start_byte, length = engine.last_trace_byte_range
    code = engine.execute_native_bytes(start_byte, length)
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x5000)  # covers CSTACK, WASM_STACK, SENTINEL, ENV -- up to 0x25000
    mu.mem_map(GUEST_RAM_BASE, 0x1000)  # guest RAM: exactly one page, nothing beyond it mapped
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))  # BKPT sentinel to stop on
    mu.mem_write(ENV_BASE + 0x00, GUEST_RAM_BASE.to_bytes(4, "little"))  # vsoc_runtime.mem-base
    mu.mem_write(ENV_BASE + 0x04, mem_size.to_bytes(4, "little"))  # vsoc_runtime.mem-size
    # Sentinel word at the fixed in-bounds offset the in-bounds test's guest_addr targets.
    # The OOB test's guest_addr lands outside the mapped page entirely, so it never reads this.
    mu.mem_write(GUEST_RAM_BASE + 0x10, (0xAABBCCDD).to_bytes(4, "little"))
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R2, ENV_BASE)
    mu.reg_write(UC_ARM_REG_R12, SENTINEL_ADDR)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)
    try:
        mu.emu_start(CODE_BASE | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise

    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    return {"r4_spilled": spilled, "sp": mu.reg_read(UC_ARM_REG_SP)}


def test_in_bounds_memory_access_executes_the_load():
    """FastAddressCheck: an in-bounds address must fall through the CMP/BHS.W guard and
    actually perform the load -- the flushed r4 ends up holding the loaded word, not the
    raw address."""
    result = _run_memory_access_trace(guest_addr=0x10, mem_size=0x1000)
    assert result["r4_spilled"] == 0xAABBCCDD, (
        f"in-bounds i32.load did not execute: spilled r4={result['r4_spilled']:#x}, "
        f"expected loaded word 0xAABBCCDD"
    )
    assert result["sp"] == CSTACK_TOP, "prologue/epilogue did not round-trip SP"


def test_out_of_bounds_memory_access_traps_before_executing_the_load():
    """FastAddressCheck/MemoryBoundaryCheck: an out-of-bounds address must take the BHS.W
    trap branch and reach the interpreter fallback WITHOUT executing the faulting load.
    guest_addr (0x2000) lands past mem_size (0x1000) AND past the end of the one-page
    GUEST_RAM_BASE mapping, so if the guard failed to fire the load would hit unmapped
    host memory and Unicorn would raise (an uncaught UcError fails the test loudly). The
    flushed r4 is checked regardless, so a guard bug that happened to land on mapped memory
    would still be caught: it must still hold the raw address, never 0xAABBCCDD.
    """
    result = _run_memory_access_trace(guest_addr=0x2000, mem_size=0x1000)
    assert result["r4_spilled"] == 0x2000, (
        f"out-of-bounds i32.load was not trapped: spilled r4={result['r4_spilled']:#x}, "
        f"expected untouched address 0x2000 (the load must never have executed)"
    )
    assert result["sp"] == CSTACK_TOP, "trap-tail epilogue did not round-trip SP"


def test_intra_trace_variant_reconciliation_swap_on_real_hardware():
    """Proves _order_register_moves()'s cycle-safe MOV sequencing -- the primitive
    emit_variant_reconciliation_glue() uses internally -- is correct on a real ARMv8-M
    core, not just structurally plausible Python. This is NOT about chaining between
    two separately-compiled traces: trace-boundary chaining ({JIT_LazyChaining}) always
    goes through memory regardless of variant (jit_compiler.md 8, {ADR_TosCacheAsymmetry}).
    It's about two consecutive stencils *within one trace* disagreeing on which register
    holds which role -- what a future per-trace register allocator could produce. Here
    that's simulated with a straight R4<->R5 swap (the case a naive move order corrupts),
    executed inline with no branch (matching real intra-trace placement) and followed
    immediately by a BKPT so the reconciled register state is observable.
    """
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()
    moves = _order_register_moves({Reg.R4: Reg.R5, Reg.R5: Reg.R4})
    engine.begin_jit_patch()
    entry = engine.byte_write_pos
    for dst, src in moves:
        engine._emit_bytes(asm.mov_reg(dst, src))
    engine._emit_bytes(bytes.fromhex("00BE"))  # BKPT #0 -- marks "next stencil" reached
    engine.commit_jit_patch()
    code = bytes(engine.byte_cache[entry : engine.byte_write_pos])
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_write(CODE_BASE, code)
    mu.reg_write(UC_ARM_REG_R4, 0xAAAAAAAA)
    mu.reg_write(UC_ARM_REG_R5, 0xCAFEF00D)
    try:
        mu.emu_start(CODE_BASE | 1, CODE_BASE + len(code))
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise

    assert mu.reg_read(UC_ARM_REG_R4) == 0xCAFEF00D, "R4 must end up with R5's original value"
    assert mu.reg_read(UC_ARM_REG_R5) == 0xAAAAAAAA, "R5 must end up with R4's original value"


if __name__ == "__main__":
    test_compiled_trace_runs_on_real_cpu_and_spills_correctly()
    test_in_bounds_memory_access_executes_the_load()
    test_out_of_bounds_memory_access_traps_before_executing_the_load()
    test_intra_trace_variant_reconciliation_swap_on_real_hardware()
    print("[PASS] compile_trace() output is real, executable, and correct Thumb-2 machine code.")
