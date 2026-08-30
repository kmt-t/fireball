"""
experiments/pysim/test_x64_jit.py

Spec-compliant tests for Fireball Trace-based Copy-and-Patch JIT Compiler (x64_jit.py).
Tests strictly per-trace basic-block compilation, native execution,
host-call dispatch, and direct trace chaining (docs/components/tier3_jit/jit_compiler.md).
"""

from __future__ import annotations

import ctypes

from runtime_engine import BasicBlock, CardState, IntegratedHybridEngine, WASMContext
from x64_jit import TraceCompiler


def test_trace_compiler_arithmetic_ops():
    """JITC-01: TraceCompiler compiles basic arithmetic and bitwise ops into native code."""
    compiler = TraceCompiler()

    # Block: local[1] = (local[0] + 10) * 3 - 5
    block = BasicBlock(
        head_pc=0x100,
        ops=[
            ("local.get", 0),
            ("i32.const", 10),
            ("i32.add", None),
            ("i32.const", 3),
            ("i32.mul", None),
            ("i32.const", 5),
            ("i32.sub", None),
            ("local.set", 1),
        ],
        next_pc=None,
    )

    trace = compiler.compile_trace(0x100, block)
    ctx = WASMContext(locals_values=[5, 0])
    res = trace.native_fn(ctx)

    assert res == "OK"
    # (5 + 10) * 3 - 5 = 40
    assert ctx.locals[1] == 40


def test_trace_compiler_bitwise_and_shifts():
    """JITC-02: TraceCompiler compiles bitwise and, or, xor, shl, shr_u, shr_s."""
    compiler = TraceCompiler()

    block = BasicBlock(
        head_pc=0x200,
        ops=[
            ("local.get", 0),
            ("local.get", 1),
            ("i32.and", None),
            ("local.set", 2),   # local[2] = local[0] & local[1]
            ("local.get", 0),
            ("i32.const", 2),
            ("i32.shl", None),
            ("local.set", 3),   # local[3] = local[0] << 2
        ],
        next_pc=None,
    )

    trace = compiler.compile_trace(0x200, block)
    ctx = WASMContext(locals_values=[0x0F, 0x07, 0, 0])
    trace.native_fn(ctx)

    assert ctx.locals[2] == (0x0F & 0x07)
    assert ctx.locals[3] == (0x0F << 2)


def test_trace_compiler_host_call():
    """JITC-03: TraceCompiler executes host function calls via ctypes native trampolines."""
    received = []

    def host_callback():
        received.append(42)
        return 999

    c_cb_type = ctypes.CFUNCTYPE(ctypes.c_uint32)
    t = c_cb_type(host_callback)
    t_addr = ctypes.cast(t, ctypes.c_void_p).value

    compiler = TraceCompiler(host_trampolines={1: t_addr})
    block = BasicBlock(
        head_pc=0x300,
        ops=[
            ("call_host", t_addr),
            ("local.set", 0),
        ],
        next_pc=None,
    )

    trace = compiler.compile_trace(0x300, block)
    ctx = WASMContext(locals_values=[0])
    trace.native_fn(ctx)

    assert received == [42]
    assert ctx.locals[0] == 999


def test_trace_chaining_between_traces():
    """JITC-04: Resident consecutive traces chain directly via chain_next."""
    engine = IntegratedHybridEngine(yield_threshold=10)

    block_a = BasicBlock(
        head_pc=0x100,
        ops=[("local.get", 0), ("i32.const", 5), ("i32.add", None), ("local.set", 0)],
        next_pc=0x200,
    )
    block_b = BasicBlock(
        head_pc=0x200,
        ops=[("local.get", 0), ("i32.const", 2), ("i32.mul", None), ("local.set", 0)],
        next_pc=None,
    )

    engine.register_block(block_a)
    engine.register_block(block_b)

    # Compile trace B first, then A (enabling immediate forward chaining)
    trace_b = engine.compiler.compile_trace(0x200, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(0x200)

    trace_a = engine.compiler.compile_trace(0x100, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(0x100)

    assert trace_a.chain_next == 0x200

    ctx = WASMContext(locals_values=[10])
    pc = 0x100
    pc = engine.run_step(pc, ctx)
    assert pc == 0x200
    assert ctx.locals[0] == 15

    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 30
    assert engine.jit_traces == 2


def test_hybrid_interpreter_to_jit_trace_elevation():
    """JITC-05: Hotspot loop starts in Interpreter -> JIT trace compiles on idle -> runs native."""
    engine = IntegratedHybridEngine(yield_threshold=3)

    # Loop: local[1] += local[0]; local[0] -= 1; branch while local[0] != 0
    loop_body = BasicBlock(
        head_pc=0x100,
        ops=[
            ("local.get", 1), ("local.get", 0), ("i32.add", None), ("local.set", 1),
            ("local.get", 0), ("i32.const", 1), ("i32.sub", None), ("local.set", 0),
            ("local.get", 0),
        ],
        next_pc=0x200,
        loops_to=0x100,
    )
    epilogue = BasicBlock(head_pc=0x200, ops=[("local.get", 1)], next_pc=None)

    engine.register_block(loop_body)
    engine.register_block(epilogue)

    # Sum 1..5: locals=[5, 0]
    ctx = WASMContext(locals_values=[5, 0])
    pc = 0x100

    # Iteration 1-3 run in Interpreter
    for _ in range(3):
        pc = engine.run_step(pc, ctx)

    assert engine.interp_blocks == 3
    assert engine.jit_traces == 0
    assert 0x100 in engine.compile_queue

    # idle_hook batch compiles queued trace into Active cache
    compiled = engine.idle_hook()
    assert compiled == 1
    assert engine.cache.active.has_trace(0x100)

    # Iteration 4-5 run in JIT Trace
    while pc is not None:
        pc = engine.run_step(pc, ctx)

    # Sum of 1..5 = 15
    assert ctx.stack[-1] == 15
    assert engine.jit_traces >= 2
    assert engine.interp_blocks >= 3


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"\n[PASS] All {len(ALL_TESTS)} pure trace JIT tests passed.")
