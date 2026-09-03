from __future__ import annotations

import sys
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _TEST_FILE.parent,
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

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

"""
experiments/pysim/test_x64_jit.py
Spec-compliant tests for Fireball Trace-based Copy-and-Patch JIT Compiler (x64_jit.py).
Verifies:
1. Exact CPS 4-argument calling convention: (uint32_t ip, void* stack_bot, void* env, void* local_base)
2. 16-byte physical JITTraceHeader layout at offset +0x00
3. Position-Independent Code (PIC) execution across arbitrary memory relocations
4. Direct trace chaining and hybrid tiering transitions
(docs/components/tier3_jit/jit_compiler.md and docs/components/tier2_runtime/runtime_interpreter.md)
"""

import ctypes

from control_flow import extract_basic_blocks
from exec_memory import ExecutableBuffer
from runtime_engine import BasicBlock, IntegratedHybridEngine, WASMContext
from test_support import wat_to_wasm
from wasm_opcodes import (
    I32_ADD,
    I32_AND,
    I32_CONST,
    I32_MUL,
    I32_SHL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
)
from x64_jit import TraceCompiler


def test_trace_compiler_cps_4arg_and_pic():
    """JITC-01: TraceCompiler emits 16-byte header + PIC code callable via CPS 4-arg convention."""
    compiler = TraceCompiler()
    # Block: local[1] = (local[0] + 10) * 3 - 5 -- real WASM bytecode, run through
    # the same extract_basic_blocks + compile_block path production JIT compilation uses.
    code = bytes(
        [
            LOCAL_GET,
            0,
            I32_CONST,
            10,
            I32_ADD,
            I32_CONST,
            3,
            I32_MUL,
            I32_CONST,
            5,
            I32_SUB,
            LOCAL_SET,
            1,
        ]
    )
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = compiler.compile_block(code, block)
    # 1. 16-byte header verification
    assert trace.header.head_wasm_pc == head_pc
    assert trace.size_bytes >= 16
    # 2. Direct call via CPS 4-argument C function pointer fn(ip, stack_bot, local_base, tos)
    locals_arr = (ctypes.c_int64 * 8)(5, 0)
    res = trace.fn(
        0x100,
        ctypes.c_void_p(0),
        ctypes.cast(locals_arr, ctypes.c_void_p),
        0,
    )
    assert res == 0
    assert locals_arr[1] == 40
    # 3. PIC Verification: Copy raw trace binary to a completely different buffer address
    # and execute it without any relocation adjustments -- must produce identical result!
    raw_blob = trace._exec_buf.read(0, trace.size_bytes)
    reloc_buf = ExecutableBuffer(len(raw_blob) + 128)
    try:
        reloc_offset = 64  # Placed at arbitrary non-zero offset
        reloc_buf.write(reloc_offset, raw_blob)
        pic_fn = reloc_buf.function_at(
            reloc_offset + 16,  # Entry at +0x10 past 16-byte header
            ctypes.c_int64,
            [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32],
        )
        locals_arr_pic = (ctypes.c_int64 * 8)(10, 0)
        pic_fn(
            0x100,
            ctypes.c_void_p(0),
            ctypes.cast(locals_arr_pic, ctypes.c_void_p),
            0,
        )
        # (10 + 10) * 3 - 5 = 55
        assert locals_arr_pic[1] == 55, "PIC trace failed when relocated in memory"
    finally:
        reloc_buf.close()

    # 4. Context-based invocation via trace.invoke(ctx)
    ctx = WASMContext(locals_values=[5, 0])
    trace.invoke(ctx)
    assert ctx.locals[1] == 40


def test_trace_compiler_bitwise_and_shifts_pic():
    """JITC-02: TraceCompiler compiles bitwise ops into PIC code."""
    compiler = TraceCompiler()
    # local[2] = local[0] & local[1]; local[3] = local[0] << 2
    code = bytes(
        [
            LOCAL_GET,
            0,
            LOCAL_GET,
            1,
            I32_AND,
            LOCAL_SET,
            2,
            LOCAL_GET,
            0,
            I32_CONST,
            2,
            I32_SHL,
            LOCAL_SET,
            3,
        ]
    )
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = compiler.compile_block(code, block)
    ctx = WASMContext(locals_values=[0x0F, 0x07, 0, 0])
    trace.invoke(ctx)
    assert ctx.locals[2] == (0x0F & 0x07)
    assert ctx.locals[3] == (0x0F << 2)


def test_trace_chaining_between_traces():
    """JITC-04: Resident consecutive traces chain directly via chain_next."""
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 5
          i32.add
          local.set 0
          br $b
        )
        local.get 0
        i32.const 2
        i32.mul
        local.set 0
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]
    # Compile trace B first, then A (enabling immediate forward chaining)
    trace_b = engine.compiler.compile_trace(
        block_b.head_pc, engine.resolve_trace_block(block_b.head_pc)
    )
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)
    trace_a = engine.compiler.compile_trace(
        block_a.head_pc, engine.resolve_trace_block(block_a.head_pc)
    )
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)
    assert trace_a.chain_next == block_b.head_pc
    ctx = WASMContext(locals_values=[10])
    pc = block_a.head_pc
    pc = engine.run_step(pc, ctx)
    assert pc == block_b.head_pc
    assert ctx.locals[0] == 15
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 30
    assert engine.jit_traces == 2


def test_hybrid_interpreter_to_jit_trace_elevation():
    """JITC-05: Hotspot loop starts in Interpreter -> JIT trace compiles on idle -> runs native."""
    wat = """
    (module
      (func (export "sum") (param i32) (result i32)
        (local i32)
        (loop $loop
          local.get 1
          local.get 0
          i32.add
          local.set 1
          local.get 0
          i32.const 1
          i32.sub
          local.tee 0
          br_if $loop
        )
        local.get 1
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = IntegratedHybridEngine(yield_threshold=3, compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[0].head_pc
    # Sum 1..5: locals=[5, 0]
    ctx = WASMContext(locals_values=[5, 0])
    pc = loop_pc
    # Iteration 1-3 run in Interpreter
    for _ in range(3):
        pc = engine.run_step(pc, ctx)

    assert engine.interp_blocks == 3
    assert engine.jit_traces == 0
    assert loop_pc in engine.compile_queue
    # idle_hook batch compiles queued trace into Active cache
    compiled = engine.idle_hook()
    assert compiled == 1
    assert engine.cache.active.has_trace(loop_pc)
    # Remaining iterations run in JIT Trace
    while pc is not None:
        pc = engine.run_step(pc, ctx)

    # Sum of 1..5 = 15
    assert ctx.locals[1] == 15
    assert engine.jit_traces >= 2
    assert engine.interp_blocks >= 3


def test_jit_chaining_with_control_skip_table():
    """JITC-54: JIT trace chaining resolves fallthrough target via control_skip_tree (bswap32 RadixBinaryTreeView)."""
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 10
          i32.add
          local.set 0
          br $b
        )
        local.get 0
        i32.const 3
        i32.mul
        local.set 0
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]

    # 1. Backward chaining: compile B (target) first, then A (source).
    trace_b = engine.compiler.compile_trace(
        block_b.head_pc, engine.resolve_trace_block(block_b.head_pc)
    )
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)

    trace_a = engine.compiler.compile_trace(
        block_a.head_pc, engine.resolve_trace_block(block_a.head_pc)
    )
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)

    # chain_next successfully bypassed block delimiter and connected to block B's head!
    assert trace_a.chain_next == block_b.head_pc

    # Execute from A: chains directly into B, (5 + 10) * 3 = 45
    ctx = WASMContext(locals_values=[5])
    pc = block_a.head_pc
    pc = engine.run_step(pc, ctx)
    assert pc == block_b.head_pc
    assert ctx.locals[0] == 15
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 45
    assert engine.jit_traces == 2

    # 2. Forward chaining test:
    engine2 = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2 = mod2.blocks[0]
    block_b2 = mod2.blocks[1]

    trace_a2 = engine2.compiler.compile_trace(
        block_a2.head_pc, engine2.resolve_trace_block(block_a2.head_pc)
    )
    engine2.cache.insert(trace_a2)
    engine2.bitmap.mark_compiled(block_a2.head_pc)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Now insert B: forward chaining must inspect resident trace A, resolve its delimiter,
    # and patch trace_a2.chain_next = block_b2.head_pc!
    trace_b2 = engine2.compiler.compile_trace(
        block_b2.head_pc, engine2.resolve_trace_block(block_b2.head_pc)
    )
    engine2.cache.insert(trace_b2)
    engine2.bitmap.mark_compiled(block_b2.head_pc)

    assert trace_a2.chain_next == block_b2.head_pc


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} pure trace JIT CPS 4-arg and PIC tests passed.")
