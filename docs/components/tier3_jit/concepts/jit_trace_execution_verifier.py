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

try:
    from unicorn import (
        UC_ARCH_ARM,
        UC_ERR_EXCEPTION,
        UC_MODE_THUMB,
        Uc,
        UcError,
    )
    from unicorn.arm_const import (
        UC_ARM_REG_LR,
        UC_ARM_REG_R0,
        UC_ARM_REG_R1,
        UC_ARM_REG_R2,
        UC_ARM_REG_R3,
        UC_ARM_REG_R4,
        UC_ARM_REG_R12,
        UC_ARM_REG_SP,
    )

    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

CODE_BASE = 0x08000
CSTACK_TOP = 0x21000  # native (R13) call stack -- grows down from here
CTX_BASE = 0x22000  # execution_context (R0) -- 60 bytes (15 fields)
WASM_STACK_BASE = 0x22400  # operand stack buffer (R1 / SP)
SENTINEL_ADDR = 0x23000  # where BX r12 or POP {..,pc} lands
GUEST_RAM_BASE = 0x25000  # guest linear memory region


def test_compiled_trace_runs_on_real_cpu_and_spills_correctly() -> None:
    engine = CopyPatchJITEngine()
    r3_in, r4_in = (
        0x64,
        0x17,
    )  # TOS, NOS -- caller-loaded, as the real interpreter would
    engine.compile_trace(
        [("i32.add", None)],
        exit_kind="fallback",
        dirty_spills=[("r3", 0)],
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
    mu.mem_map(0x20000, 0x4000)  # covers CSTACK, CTX, WASM_STACK, SENTINEL
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))  # BKPT sentinel to stop on
    mu.reg_write(UC_ARM_REG_R0, CTX_BASE)
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R2, 0)
    mu.reg_write(UC_ARM_REG_R3, r3_in)
    mu.reg_write(UC_ARM_REG_R4, r4_in)
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
    # Check stack flush: TOS (R3) flushed to WASM_STACK_BASE
    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    expected = (r4_in + r3_in) & 0xFFFFFFFF
    assert spilled == expected, (
        f"dirty-spill flush did not write the ADD result to the stack: "
        f"stack[0]={spilled:#x}, expected r4+r3={expected:#x}"
    )
    # Check context sync: execution_context.sp_offset (+0x0C) and ip (+0x00)
    ctx_sp = int.from_bytes(mu.mem_read(CTX_BASE + 0x0C, 4), "little")
    assert ctx_sp == WASM_STACK_BASE, f"execution_context.sp_offset not updated, got {ctx_sp:#x}"
    ctx_ip = int.from_bytes(mu.mem_read(CTX_BASE + 0x00, 4), "little")
    assert ctx_ip == 0x101, f"execution_context.ip not updated, got {ctx_ip:#x}"

    pc = mu.reg_read(UC_ARM_REG_R12)
    print(
        f"[OK] compile_trace() emitted {length} real byte(s), executed on a real ARMv8-M "
        f"Thumb core, spilled r4+r3={expected:#x} to stack[0], synced context IP/SP, "
        f"reached fallback sentinel via BX r12={pc:#x}."
    )


def _run_memory_access_trace(guest_addr: int, mem_size: int) -> dict:
    """Compiles [i32.const guest_addr, i32.load], maps a real ENV/guest-RAM pair in Unicorn,
    and runs the trace to completion (fallback exit -- always ends via BX r12 -> SENTINEL_ADDR).
    r3 (TOS) is declared dirty so its final value survives the callee-saved POP.W.
    Returns the flushed stack[0] word and final SP.
    """
    engine = CopyPatchJITEngine()
    engine.compile_trace(
        [("i32.const", guest_addr), ("i32.load", None)],
        exit_kind="fallback",
        dirty_spills=[("r3", 0)],
        head_wasm_pc=0x100,
    )
    start_byte, length = engine.last_trace_byte_range
    code = engine.execute_native_bytes(start_byte, length)
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x5000)  # covers CSTACK, CTX, WASM_STACK, SENTINEL
    mu.mem_map(GUEST_RAM_BASE, 0x1000)  # guest RAM: exactly one page
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))  # BKPT sentinel to stop on
    # Write mem_base (+0x28) and mem_size (+0x2C) into execution_context (CTX_BASE)
    mu.mem_write(CTX_BASE + 0x28, GUEST_RAM_BASE.to_bytes(4, "little"))
    mu.mem_write(CTX_BASE + 0x2C, mem_size.to_bytes(4, "little"))
    # Sentinel word at the fixed in-bounds offset the in-bounds test's guest_addr targets.
    mu.mem_write(GUEST_RAM_BASE + 0x10, (0xAABBCCDD).to_bytes(4, "little"))
    mu.reg_write(UC_ARM_REG_R0, CTX_BASE)
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R2, 0)
    mu.reg_write(UC_ARM_REG_R12, SENTINEL_ADDR)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)
    try:
        mu.emu_start(CODE_BASE | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise
    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    return {"r3_spilled": spilled, "sp": mu.reg_read(UC_ARM_REG_SP)}


def test_in_bounds_memory_access_executes_the_load() -> None:
    result = _run_memory_access_trace(guest_addr=0x10, mem_size=0x1000)
    assert result["r3_spilled"] == 0xAABBCCDD, (
        f"in-bounds i32.load did not execute: spilled r3={result['r3_spilled']:#x}, "
        f"expected loaded word 0xAABBCCDD"
    )
    assert result["sp"] == CSTACK_TOP, "prologue/epilogue did not round-trip SP"


def test_out_of_bounds_memory_access_traps_before_executing_the_load() -> None:
    result = _run_memory_access_trace(guest_addr=0x2000, mem_size=0x1000)
    assert result["r3_spilled"] == 0x2000, (
        f"out-of-bounds i32.load was not trapped: spilled r3={result['r3_spilled']:#x}, "
        f"expected untouched address 0x2000"
    )
    assert result["sp"] == CSTACK_TOP, "trap-tail epilogue did not round-trip SP"


def test_intra_trace_variant_reconciliation_swap_on_real_hardware() -> None:
    engine = CopyPatchJITEngine()
    asm = Thumb2Assembler()
    moves = _order_register_moves({Reg.R3: Reg.R4, Reg.R4: Reg.R3})
    engine.begin_jit_patch()
    entry = engine.byte_write_pos
    for dst, src in moves:
        engine._emit_bytes(asm.mov_reg(dst, src))
    engine._emit_bytes(bytes.fromhex("00BE"))  # BKPT #0
    engine.commit_jit_patch()
    code = bytes(engine.byte_cache[entry : engine.byte_write_pos])
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_write(CODE_BASE, code)
    mu.reg_write(UC_ARM_REG_R3, 0xAAAAAAAA)
    mu.reg_write(UC_ARM_REG_R4, 0xCAFEF00D)
    try:
        mu.emu_start(CODE_BASE | 1, CODE_BASE + len(code))
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise
    assert mu.reg_read(UC_ARM_REG_R3) == 0xCAFEF00D, "R3 must end up with R4's original value"
    assert mu.reg_read(UC_ARM_REG_R4) == 0xAAAAAAAA, "R4 must end up with R3's original value"


def test_chain_branch_preserves_registers_on_real_hardware() -> None:
    engine = CopyPatchJITEngine()
    # Trace B (successor): flushes live R3 straight out to stack
    engine.compile_trace([], exit_kind="return", dirty_spills=[("r3", 0)], head_wasm_pc=0x200)
    succ_chain_entry = engine.last_chain_entry_byte_offset
    succ_start_byte, succ_length = engine.last_trace_byte_range
    # Trace A (predecessor): computes a value into R3 via ALU op, then chains into trace B
    r3_in, r4_in = 0x30, 0x0D
    engine.compile_trace(
        [("i32.add", None)],
        exit_kind="chain",
        head_wasm_pc=0x100,
    )
    pred_header_byte = engine.last_trace_header_range[0]
    pred_start_byte, pred_length = engine.last_trace_byte_range

    blob_start = succ_start_byte - 16  # trace B's own header
    blob_end = pred_start_byte + pred_length  # trace A's end

    succ_native_chain_entry = (CODE_BASE + (succ_chain_entry - blob_start)) | 1
    engine.set_chain_target(pred_header_byte, succ_native_chain_entry)

    code = engine.execute_native_bytes(blob_start, blob_end - blob_start)
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x4000)
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))
    mu.reg_write(UC_ARM_REG_R0, CTX_BASE)
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R2, 0)
    mu.reg_write(UC_ARM_REG_R3, r3_in)
    mu.reg_write(UC_ARM_REG_R4, r4_in)
    mu.reg_write(UC_ARM_REG_R12, 0)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)
    mu.reg_write(UC_ARM_REG_LR, SENTINEL_ADDR | 1)
    entry = CODE_BASE + (pred_start_byte - blob_start)
    try:
        mu.emu_start(entry | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise
    final_sp = mu.reg_read(UC_ARM_REG_SP)
    assert final_sp == CSTACK_TOP, "the chained pair must round-trip SP exactly once"
    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    expected = (r4_in + r3_in) & 0xFFFFFFFF
    assert spilled == expected, f"R3 did not survive chain branch: got {spilled:#x}, expected {expected:#x}"
    print(
        "[OK] chained pair executed: bypassed epilogue and jumped directly to successor, "
        "registers preserved and SP round-tripped once."
    )


def test_dynamic_chain_unlinked_falls_through_to_interpreter_on_real_hardware() -> None:
    engine = CopyPatchJITEngine()
    r3_in, r4_in = 0x50, 0x05
    engine.compile_trace(
        [("i32.add", None)],
        exit_kind="chain",
        dirty_spills=[("r3", 0)],
        chain_target_addr=0,  # Unlinked!
        head_wasm_pc=0x100,
    )
    pred_header_byte = engine.last_trace_header_range[0]
    pred_start_byte, pred_length = engine.last_trace_byte_range
    blob_start = pred_header_byte
    blob_len = (pred_start_byte + pred_length) - blob_start

    code = engine.execute_native_bytes(blob_start, blob_len)
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x4000)
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))
    mu.reg_write(UC_ARM_REG_R0, CTX_BASE)
    mu.reg_write(UC_ARM_REG_R1, WASM_STACK_BASE)
    mu.reg_write(UC_ARM_REG_R2, 0)
    mu.reg_write(UC_ARM_REG_R3, r3_in)
    mu.reg_write(UC_ARM_REG_R4, r4_in)
    mu.reg_write(UC_ARM_REG_R12, 0)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)
    mu.reg_write(UC_ARM_REG_LR, SENTINEL_ADDR | 1)
    entry = CODE_BASE + (pred_start_byte - blob_start)
    try:
        mu.emu_start(entry | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise
    final_sp = mu.reg_read(UC_ARM_REG_SP)
    assert final_sp == CSTACK_TOP
    spilled = int.from_bytes(mu.mem_read(WASM_STACK_BASE, 4), "little")
    expected = (r4_in + r3_in) & 0xFFFFFFFF
    assert spilled == expected, f"got {spilled:#x}, expected {expected:#x}"
    # Verify context IP and SP were synced on unlinked fallback
    ctx_sp = int.from_bytes(mu.mem_read(CTX_BASE + 0x0C, 4), "little")
    assert ctx_sp == WASM_STACK_BASE
    ctx_ip = int.from_bytes(mu.mem_read(CTX_BASE + 0x00, 4), "little")
    assert ctx_ip == 0x101
    print(
        f"[OK] unlinked dynamic chain fell through to epilogue on real CPU: "
        f"flushed r4+r3={expected:#x}, synced context, and returned cleanly."
    )


def test_inlined_control_flow_and_sp_rewind_on_real_hardware() -> None:
    engine = CopyPatchJITEngine()
    ops = [
        ("block", None),
        ("loop", None),
        ("nop", None),
        ("i32.const", 0x1234),
        ("br", (0x200, 16)),  # branch with 16-byte SP rewind
        ("end", None),
    ]
    start_pos, count = engine.compile_trace(ops, exit_kind="return", dirty_spills=[("r3", 0)])
    start_byte, length = engine.last_trace_byte_range
    code = engine.execute_native_bytes(start_byte, length)

    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_map(0x20000, 0x4000)
    mu.mem_write(CODE_BASE, code)
    mu.mem_write(SENTINEL_ADDR, bytes.fromhex("00BE"))
    initial_sp = WASM_STACK_BASE + 32
    mu.reg_write(UC_ARM_REG_R0, CTX_BASE)
    mu.reg_write(UC_ARM_REG_R1, initial_sp)
    mu.reg_write(UC_ARM_REG_R2, 0)
    mu.reg_write(UC_ARM_REG_SP, CSTACK_TOP)
    mu.reg_write(UC_ARM_REG_LR, SENTINEL_ADDR | 1)

    try:
        mu.emu_start(CODE_BASE | 1, SENTINEL_ADDR)
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise

    # 1. Check SP rewind in context: 32 - 16 = 16 (relative) -> initial_sp - 16
    expected_sp = initial_sp - 16
    ctx_sp = int.from_bytes(mu.mem_read(CTX_BASE + 0x0C, 4), "little")
    assert ctx_sp == expected_sp, f"Expected ctx_sp={expected_sp:#x}, got {ctx_sp:#x}"
    # 2. Check spill (at the rewound SP)
    spilled = int.from_bytes(mu.mem_read(expected_sp, 4), "little")
    assert spilled == 0x1234, f"Expected spilled 0x1234, got {spilled:#x}"
    # 3. Check native call-stack SP round-trip
    assert mu.reg_read(UC_ARM_REG_SP) == CSTACK_TOP
    print(
        f"[OK] inlined control flow verified on real CPU: delimiters eliminated, "
        f"SP rewound {initial_sp:#x} -> {ctx_sp:#x}, synced context, and returned cleanly."
    )


if __name__ == "__main__":
    if not HAVE_UNICORN:
        print("[SKIP] unicorn emulator not installed; skipping ARM trace verification.")
        sys.exit(0)
    test_compiled_trace_runs_on_real_cpu_and_spills_correctly()
    test_in_bounds_memory_access_executes_the_load()
    test_out_of_bounds_memory_access_traps_before_executing_the_load()
    test_intra_trace_variant_reconciliation_swap_on_real_hardware()
    test_chain_branch_preserves_registers_on_real_hardware()
    test_dynamic_chain_unlinked_falls_through_to_interpreter_on_real_hardware()
    test_inlined_control_flow_and_sp_rewind_on_real_hardware()
    print("[PASS] compile_trace() output is real, executable, and correct Thumb-2 machine code.")
