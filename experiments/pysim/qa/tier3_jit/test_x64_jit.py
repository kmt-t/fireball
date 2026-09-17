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
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
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
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

"""
experiments/pysim/qa/tier3_jit/test_x64_jit.py
Spec-compliant tests for Fireball Trace-based Copy-and-Patch JIT Compiler (x64_jit.py).
Verifies:
1. Exact CPS 4-argument calling convention: (void* ctx, void* sp, void* local_base, uint32_t tos)
2. 56-byte x64 physical JITTraceHeader layout at offset +0x00
3. Shared common-area entry/exit routing
4. Direct trace chaining and hybrid tiering transitions
(docs/components/tier3_jit/jit_compiler.md and docs/components/tier2_runtime/runtime_interpreter.md)
"""

import ctypes
import struct

from control_flow import extract_basic_blocks
from helpers import wat_to_wasm
from legacy_runtime_engine import IntegratedHybridEngine, WASMContext
from runtime_engine import BasicBlock
from test_support import compile_module_block, compile_test_block
from wasm_module import WASM_LOCAL_SLOT_WORDS
from wasm_opcodes import (
    F32_ADD,
    F32_CONST,
    F32_DIV,
    F32_MUL,
    F32_SUB,
    F64_ADD,
    F64_CONST,
    F64_DIV,
    F64_MUL,
    F64_SUB,
    I32_ADD,
    I32_AND,
    I32_CONST,
    I32_MUL,
    I32_SHL,
    I32_SUB,
    I64_ADD,
    I64_CONST,
    I64_MUL,
    I64_SUB,
    LOCAL_GET,
    LOCAL_SET,
)
from x64_jit import TraceCompiler

_HELPER_TYPE = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
    ctypes.c_uint32,
)


def _make_raw_helpers() -> tuple[tuple[int, ...], tuple[ctypes._CFuncPtr, ...]]:
    def i64_binary(sp, operation):
        left = sp[0] | (sp[1] << 32)
        right = sp[2] | (sp[3] << 32)
        value = operation(left, right) & 0xFFFF_FFFF_FFFF_FFFF
        sp[0] = value & 0xFFFF_FFFF
        sp[1] = (value >> 32) & 0xFFFF_FFFF

    def f32_binary(sp, operation):
        left = struct.unpack("<f", struct.pack("<I", sp[0]))[0]
        right = struct.unpack("<f", struct.pack("<I", sp[1]))[0]
        value = operation(left, right)
        sp[0] = struct.unpack("<I", struct.pack("<f", value))[0]

    def f64_binary(sp, operation):
        left = struct.unpack("<d", struct.pack("<II", sp[0], sp[1]))[0]
        right = struct.unpack("<d", struct.pack("<II", sp[2], sp[3]))[0]
        low, high = struct.unpack("<II", struct.pack("<d", operation(left, right)))
        sp[0] = low
        sp[1] = high

    callbacks = tuple(
        _HELPER_TYPE(fn)
        for fn in (
            lambda c, s, l, t: i64_binary(s, lambda a, b: a + b),
            lambda c, s, l, t: i64_binary(s, lambda a, b: a - b),
            lambda c, s, l, t: i64_binary(s, lambda a, b: a * b),
            lambda c, s, l, t: f32_binary(s, lambda a, b: a + b),
            lambda c, s, l, t: f32_binary(s, lambda a, b: a - b),
            lambda c, s, l, t: f32_binary(s, lambda a, b: a * b),
            lambda c, s, l, t: f32_binary(s, lambda a, b: a / b),
            lambda c, s, l, t: f64_binary(s, lambda a, b: a + b),
            lambda c, s, l, t: f64_binary(s, lambda a, b: a - b),
            lambda c, s, l, t: f64_binary(s, lambda a, b: a * b),
            lambda c, s, l, t: f64_binary(s, lambda a, b: a / b),
        )
    )
    return tuple(ctypes.cast(fn, ctypes.c_void_p).value or 0 for fn in callbacks), callbacks


def test_complex_helpers_use_shared_raw_slots_for_i64_and_floating_point():
    """Complex values cross the JIT boundary as raw 32-bit words, not Python values."""
    addresses, keepalive = _make_raw_helpers()
    compiler = TraceCompiler()
    ctx = WASMContext()
    helper_indices = {
        I64_ADD: 0, I64_SUB: 1, I64_MUL: 2,
        F32_ADD: 3, F32_SUB: 4, F32_MUL: 5, F32_DIV: 6,
        F64_ADD: 7, F64_SUB: 8, F64_MUL: 9, F64_DIV: 10,
    }

    def run_i64(op, left: int, right: int, expected: int) -> None:
        ctx.stack.set_size(0)
        trace = compiler.compile_trace(
            0,
            ((I64_CONST, left), (I64_CONST, right), (op, None)),
            3,
            None,
            3,
            (),
            helper_target_addr=addresses[helper_indices[op]],
        )
        assert trace is not None
        trace.invoke(ctx)
        assert tuple(ctx.stack) == (expected & 0xFFFF_FFFF, expected >> 32)

    def run_f32(op, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        left_bits = struct.unpack("<I", struct.pack("<f", left))[0]
        right_bits = struct.unpack("<I", struct.pack("<f", right))[0]
        trace = compiler.compile_trace(
            0,
            ((F32_CONST, left_bits), (F32_CONST, right_bits), (op, None)),
            3,
            None,
            3,
            (),
            helper_target_addr=addresses[helper_indices[op]],
        )
        assert trace is not None
        trace.invoke(ctx)
        actual = struct.unpack("<f", struct.pack("<I", ctx.stack[0]))[0]
        assert abs(actual - expected) < 1e-6

    def run_f64(op, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        left_bits = struct.unpack("<Q", struct.pack("<d", left))[0]
        right_bits = struct.unpack("<Q", struct.pack("<d", right))[0]
        trace = compiler.compile_trace(
            0,
            ((F64_CONST, left_bits), (F64_CONST, right_bits), (op, None)),
            3,
            None,
            3,
            (),
            helper_target_addr=addresses[helper_indices[op]],
        )
        assert trace is not None
        trace.invoke(ctx)
        actual = struct.unpack("<d", struct.pack("<II", ctx.stack[0], ctx.stack[1]))[0]
        assert abs(actual - expected) < 1e-12

    run_i64(I64_ADD, 0x0000_0001_0000_0002, 3, 0x0000_0001_0000_0005)
    run_i64(I64_SUB, 9, 4, 5)
    run_i64(I64_MUL, 9, 4, 36)
    run_f32(F32_ADD, 1.5, 2.25, 3.75)
    run_f32(F32_SUB, 9.0, 4.0, 5.0)
    run_f32(F32_MUL, 9.0, 4.0, 36.0)
    run_f32(F32_DIV, 9.0, 4.0, 2.25)
    run_f64(F64_ADD, 1.5, 2.25, 3.75)
    run_f64(F64_SUB, 9.0, 4.0, 5.0)
    run_f64(F64_MUL, 9.0, 4.0, 36.0)
    run_f64(F64_DIV, 9.0, 4.0, 2.25)
    assert keepalive


def test_trace_compiler_cps_4arg_and_pic():
    """TEST-JITC-01: TraceCompiler emits a header-routed CPS trace."""
    compiler = TraceCompiler()
    # Block: local[1] = (local[0] + 10) * 3 - 5 -- real WASM bytecode, run through
    # The test prepares the same loader metadata passed by the runtime.
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
    trace = compile_test_block(compiler, code, block, (1, 1))
    # 1. Header and common-area offsets
    assert trace.header.head_wasm_pc == head_pc
    assert trace.size_bytes >= 48
    assert trace.header.common_prologue_offset == 0
    assert trace.header.common_epilogue_offset == 32
    # 2. Direct call via CPS 4-argument C function pointer fn(ctx, sp, local_base, tos)
    locals_arr = (ctypes.c_uint32 * 8)()
    locals_arr[0] = 5
    res = trace.fn(
        ctypes.c_void_p(0),
        ctypes.c_void_p(0),
        ctypes.cast(locals_arr, ctypes.c_void_p),
        0,
    )
    assert res is None
    assert locals_arr[WASM_LOCAL_SLOT_WORDS] == 40
    # 3. The installed entry is a header-selected jump into the common prefix.
    assert trace.code_offset == 2048
    assert trace._exec_buf.read(0, 22) == trace._exec_buf.read(0, 22)

    # 4. Context-based invocation via trace.invoke(ctx)
    ctx = WASMContext()
    ctx.locals = (5, 0)
    trace.invoke(ctx)
    assert ctx.locals[1] == 40


def test_trace_compiler_bitwise_and_shifts_pic():
    """TEST-JITC-02: TraceCompiler compiles bitwise ops into PIC code."""
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
    trace = compile_test_block(compiler, code, block, (1, 1, 1, 1))
    ctx = WASMContext()
    ctx.locals = (0x0F, 0x07, 0, 0)
    trace.invoke(ctx)
    assert ctx.locals[2] == (0x0F & 0x07)
    assert ctx.locals[3] == (0x0F << 2)


def test_trace_header_helper_tail_jump_uses_per_trace_pointer():
    """Complex JIT boundaries load the helper target from the trace header."""

    compiler = TraceCompiler()
    code = bytes([LOCAL_GET, 0, LOCAL_SET, 0])
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    helper_type = ctypes.CFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )

    def helper(_ctx: ctypes.c_void_p, _sp: ctypes.c_void_p, local_base: ctypes.c_void_p, _tos: int) -> None:
        locals_ptr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_uint32))
        locals_ptr[0] += 1

    helper_fn = helper_type(helper)
    ctx = WASMContext()
    ctx.locals = (10,)
    helper_addr = ctypes.cast(helper_fn, ctypes.c_void_p).value or 0
    trace = compiler.compile_trace(
        head_pc,
        ((LOCAL_GET, 0), (LOCAL_SET, 0)),
        next_pc,
        loops_to,
        byte_span,
        (1,),
        tail_context_helper=True,
        helper_target_addr=helper_addr,
    )
    assert trace is not None
    trace.invoke(ctx)
    assert ctx.locals[0] == 11

    raw_blob = trace._exec_buf.read(trace.code_offset, trace.size_bytes)
    helper_addr_bytes = helper_addr.to_bytes(8, "little")
    assert raw_blob[0x28:0x30] == helper_addr_bytes


def test_trace_chaining_between_traces():
    """TEST-JITC-04: Resident consecutive traces chain directly via chain_next."""
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
        local.get 0
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
    trace_b = compile_module_block(engine.compiler, mod, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)
    trace_a = compile_module_block(engine.compiler, mod, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)
    assert trace_a.chain_next == block_b.head_pc
    ctx = WASMContext()
    ctx.locals = (10,)
    pc = block_a.head_pc
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 30
    assert engine.jit_traces == 2


def test_hybrid_interpreter_to_jit_trace_elevation():
    """TEST-JITC-05: Hotspot loop starts in Interpreter -> JIT trace compiles on idle -> runs native."""
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
    ctx = WASMContext()
    ctx.locals = (5, 0)
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


def test_jit_chaining_uses_loader_resolved_successors():
    """TEST-JITC-54: JIT chaining uses loader-resolved block successors directly."""
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
        local.get 0
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
    trace_b = compile_module_block(engine.compiler, mod, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)

    trace_a = compile_module_block(engine.compiler, mod, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)

    # chain_next successfully bypassed block delimiter and connected to block B's head!
    assert trace_a.chain_next == block_b.head_pc

    # Execute from A: chains directly into B, (5 + 10) * 3 = 45
    ctx = WASMContext()
    ctx.locals = (5,)
    pc = block_a.head_pc
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 45
    assert engine.jit_traces == 2

    # 2. Forward chaining test:
    engine2 = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2 = mod2.blocks[0]
    block_b2 = mod2.blocks[1]

    trace_a2 = compile_module_block(engine2.compiler, mod2, block_a2)
    engine2.cache.insert(trace_a2)
    engine2.bitmap.mark_compiled(block_a2.head_pc)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Now insert B: forward chaining must inspect resident trace A, resolve its delimiter,
    # and patch trace_a2.chain_next = block_b2.head_pc!
    trace_b2 = compile_module_block(engine2.compiler, mod2, block_b2)
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
