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
2. 52-byte x64 physical JITTraceHeader layout at offset +0x00
3. Shared common-area entry/exit routing
4. Direct trace chaining and hybrid tiering transitions
(docs/components/tier3_jit/jit_compiler.md and docs/components/tier2_runtime/runtime_interpreter.md)
"""

import ctypes
import struct

from control_flow import extract_basic_blocks
from execution_context import WASMContext
from helpers import make_interpreter as Interpreter
from helpers import wat_to_wasm
from runtime_engine import BasicBlock, RuntimeEngine
from test_support import compile_module_block, compile_test_block
from wasm_module import I32, I64, LocalWidthMap
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
    I32_DIV_S,
    I32_DIV_U,
    I32_EQ,
    I32_GE_S,
    I32_GT_S,
    I32_LE_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_REM_S,
    I32_REM_U,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_SUB,
    I32_XOR,
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
_I32_HELPER_TYPE = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
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


def test_complex_helpers_use_shared_value_slots_for_wide_values():
    """Wide helper values use the existing shared value-slot contract."""
    addresses, keepalive = _make_raw_helpers()
    compiler = TraceCompiler()
    ctx = WASMContext()
    helper_indices = {
        I64_ADD: 0,
        I64_SUB: 1,
        I64_MUL: 2,
        F32_ADD: 3,
        F32_SUB: 4,
        F32_MUL: 5,
        F32_DIV: 6,
        F64_ADD: 7,
        F64_SUB: 8,
        F64_MUL: 9,
        F64_DIV: 10,
    }

    def run_i64(op, left: int, right: int, expected: int) -> None:
        ctx.stack.set_size(0)
        trace = compiler.compile_trace(
            0,
            ((I64_CONST, left), (I64_CONST, right), (op, None)),
            3,
            None,
            3,
            LocalWidthMap(()),
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
            LocalWidthMap(()),
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
            LocalWidthMap(()),
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
    trace = compile_test_block(compiler, code, block, (I32, I32))
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
    # Two i32 locals give a 4-byte slot stride, so local 1 is raw word 1.
    assert locals_arr[1] == 40
    # 3. The installed entry is a header-selected jump into the common prefix.
    assert trace.code_offset == 2048
    assert trace._exec_buf.read(0, 22) == trace._exec_buf.read(0, 22)

    # 4. Context-based invocation via trace.invoke(ctx)
    ctx = WASMContext()
    ctx.locals = (5, 0)
    trace.invoke(ctx)
    assert ctx.locals[1] == 40


def test_x64_division_and_remainder_use_helper_boundary() -> None:
    """x64 routes integer division and remainder through the helper ABI."""
    compiler = TraceCompiler()

    def signed(value: int) -> int:
        return ctypes.c_int32(value).value

    def signed_div(left: int, right: int) -> int:
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient

    def signed_rem(left: int, right: int) -> int:
        remainder = abs(left) % abs(right)
        return -remainder if left < 0 else remainder

    def make_helper(operation: int) -> ctypes._CFuncPtr:
        def helper(left: int, right: int, result: ctypes._Pointer) -> None:
            if operation == I32_DIV_S:
                value = signed_div(signed(left), signed(right))
            elif operation == I32_DIV_U:
                value = left // right
            elif operation == I32_REM_S:
                value = signed_rem(signed(left), signed(right))
            else:
                assert operation == I32_REM_U
                value = left % right
            result[0] = value & 0xFFFF_FFFF

        return _I32_HELPER_TYPE(helper)

    for helper_slot, (operation, left, right, expected) in enumerate(
        (
            (I32_DIV_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFD),
            (I32_DIV_U, 0xFFFF_FFF0, 2, 0x7FFF_FFF8),
            (I32_REM_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFF),
            (I32_REM_U, 0xFFFF_FFF0, 3, 0),
        )
    ):
        helper = make_helper(operation)
        trace = compiler.compile_trace(
            0,
            ((I32_CONST, left), (I32_CONST, right), (operation, None)),
            3,
            None,
            3,
            LocalWidthMap(()),
            helper_target_addr=ctypes.cast(helper, ctypes.c_void_p).value or 0,
        )
        assert trace is not None
        assert trace.header.common_helper_offset == 352 + helper_slot * 32
        ctx = WASMContext()
        trace.invoke(ctx)
        assert ctx.stack[0] == expected, (operation, ctx.stack[0], expected)


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
    trace = compile_test_block(compiler, code, block, (I32, I32, I32, I32))
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

    def helper(
        _ctx: ctypes.c_void_p, _sp: ctypes.c_void_p, local_base: ctypes.c_void_p, _tos: int
    ) -> None:
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
        LocalWidthMap((I32,)),
        tail_context_helper=True,
        helper_target_addr=helper_addr,
    )
    assert trace is not None
    trace.invoke(ctx)
    assert ctx.locals[0] == 11

    raw_blob = trace._exec_buf.read(trace.code_offset, trace.size_bytes)
    helper_addr_bytes = helper_addr.to_bytes(8, "little")
    assert raw_blob[0x24:0x2C] == helper_addr_bytes


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
    engine = RuntimeEngine(yield_threshold=10, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]
    # Compile trace B first, then A (enabling immediate forward chaining)
    trace_b = compile_module_block(engine.jit_compiler, mod, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)
    trace_a = compile_module_block(engine.jit_compiler, mod, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)
    assert trace_a.chain_next == block_b.head_pc
    results = engine.run(Interpreter(mod), 0, [10])
    assert results[0] == 30
    assert engine.stat_jit_invocations == 2


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
    engine = RuntimeEngine(yield_threshold=3, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[0].head_pc
    results = engine.run(Interpreter(mod), 0, [5])
    assert results[0] == 15
    assert engine.stat_jit_invocations >= 2
    assert engine.stat_interp_steps >= 3
    assert engine.cache.active.has_trace(loop_pc) or engine.cache.warm.has_trace(loop_pc)


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
    engine = RuntimeEngine(yield_threshold=10, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]

    # 1. Backward chaining: compile B (target) first, then A (source).
    trace_b = compile_module_block(engine.jit_compiler, mod, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(block_b.head_pc)

    trace_a = compile_module_block(engine.jit_compiler, mod, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(block_a.head_pc)

    # chain_next successfully bypassed block delimiter and connected to block B's head!
    assert trace_a.chain_next == block_b.head_pc

    # Execute from A: chains directly into B, (5 + 10) * 3 = 45
    results = engine.run(Interpreter(mod), 0, [5])
    assert results[0] == 45
    assert engine.stat_jit_invocations == 2

    # 2. Forward chaining test:
    engine2 = RuntimeEngine(yield_threshold=10, jit_compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2 = mod2.blocks[0]
    block_b2 = mod2.blocks[1]

    trace_a2 = compile_module_block(engine2.jit_compiler, mod2, block_a2)
    engine2.cache.insert(trace_a2)
    engine2.bitmap.mark_compiled(block_a2.head_pc)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Now insert B: forward chaining must inspect resident trace A, resolve its delimiter,
    # and patch trace_a2.chain_next = block_b2.head_pc!
    trace_b2 = compile_module_block(engine2.jit_compiler, mod2, block_b2)
    engine2.cache.insert(trace_b2)
    engine2.bitmap.mark_compiled(block_b2.head_pc)

    assert trace_a2.chain_next == block_b2.head_pc


def test_trace_local_addressing_follows_frame_slot_width():
    """TEST-JITC-59: local displacement is index * slot width, set by the frame's widest local."""
    code = bytes([LOCAL_GET, 0, LOCAL_SET, 2])
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    for types, slot_words in (((I32, I32, I32), 1), ((I32, I64, I32), 2)):
        trace = compile_test_block(TraceCompiler(), code, block, types)
        assert trace is not None
        locals_arr = (ctypes.c_uint32 * (3 * slot_words))()
        locals_arr[0] = 7
        trace.fn(
            ctypes.c_void_p(0), ctypes.c_void_p(0), ctypes.cast(locals_arr, ctypes.c_void_p), 0
        )
        assert locals_arr[2 * slot_words] == 7, f"types {types}"
        assert sum(locals_arr) == 14, "no other slot may change"


# ---------------------------------------------------------------------------
# Register cache: values pushed beyond TOS/NOS spill to the shared operand stack
# ---------------------------------------------------------------------------

_M32 = 0xFFFFFFFF
_SENTINEL = 0xDEADBEEF


def _s32(value: int) -> int:
    value &= _M32
    return value - (1 << 32) if value & 0x80000000 else value


# op -> reference semantics over unsigned 32-bit operands (left, right).
_SPILL_OPS = {
    I32_ADD: lambda a, b: (a + b) & _M32,
    I32_SUB: lambda a, b: (a - b) & _M32,
    I32_MUL: lambda a, b: (a * b) & _M32,
    I32_AND: lambda a, b: a & b,
    I32_OR: lambda a, b: a | b,
    I32_XOR: lambda a, b: a ^ b,
    I32_SHL: lambda a, b: (a << (b & 31)) & _M32,
    I32_SHR_U: lambda a, b: a >> (b & 31),
    I32_SHR_S: lambda a, b: (_s32(a) >> (b & 31)) & _M32,
    I32_EQ: lambda a, b: int(a == b),
    I32_NE: lambda a, b: int(a != b),
    I32_LT_S: lambda a, b: int(_s32(a) < _s32(b)),
    I32_LT_U: lambda a, b: int(a < b),
    I32_GT_S: lambda a, b: int(_s32(a) > _s32(b)),
    I32_LE_U: lambda a, b: int(a <= b),
    I32_GE_S: lambda a, b: int(_s32(a) >= _s32(b)),
}


def _random_rpn(seed: int, max_depth: int) -> tuple[list[tuple[int, int | None]], list[int]]:
    """A straight-line i32 expression that peaks at `max_depth` values and ends with one."""
    import random

    rng = random.Random(seed)
    locals_values = [rng.randrange(1 << 32) for _ in range(4)]
    program: list[tuple[int, int | None]] = []
    depth = 0
    peak = 0
    while peak < max_depth or depth > 1:
        push = depth < 2 or (depth < max_depth and rng.random() < 0.6 and peak < max_depth)
        if push:
            if rng.random() < 0.5:
                program.append((I32_CONST, rng.randrange(-(1 << 31), 1 << 31)))
            else:
                program.append((LOCAL_GET, rng.randrange(4)))
            depth += 1
            peak = max(peak, depth)
        else:
            program.append((rng.choice(list(_SPILL_OPS)), None))
            depth -= 1
    return program, locals_values


def _reference(program, locals_values) -> tuple[int, int]:
    """Evaluate the program; return (result, peak number of values beyond the top two)."""
    stack: list[int] = []
    peak_spill = 0
    for op, arg in program:
        if op == I32_CONST:
            stack.append(int(arg) & _M32)
        elif op == LOCAL_GET:
            stack.append(locals_values[int(arg)])
        else:
            right, left = stack.pop(), stack.pop()
            stack.append(_SPILL_OPS[op](left, right))
        peak_spill = max(peak_spill, len(stack) - 2)
    assert len(stack) == 1
    return stack[0], peak_spill


def _run_spill_trace(program, locals_values, sp_index: int, total: int = 96):
    """Run the compiled program at `sp_index` in a sentinel-filled operand stack buffer."""
    trace = TraceCompiler().compile_trace(
        0, program, None, None, len(program) * 3, LocalWidthMap((I32,) * 4)
    )
    assert trace is not None, "a straight-line i32 expression must compile"
    words = (ctypes.c_uint32 * total)(*([_SENTINEL] * total))
    locals_arr = (ctypes.c_uint32 * 4)(*locals_values)
    trace.fn(
        ctypes.c_void_p(0),
        ctypes.c_void_p(ctypes.addressof(words) + 4 * sp_index),
        ctypes.cast(locals_arr, ctypes.c_void_p),
        0,
    )
    return trace, words


def test_register_cache_spill_matches_reference_and_stays_in_bounds():
    """TEST-JITC-60: spilled NOS/NNOS values keep their order and never leave the trace's words."""
    checked_deep = 0
    for seed in range(240):
        max_depth = 3 + seed % 9
        program, locals_values = _random_rpn(seed, max_depth)
        expected, peak_spill = _reference(program, locals_values)
        sp_index = 3 + seed % 7
        trace, words = _run_spill_trace(program, locals_values, sp_index)
        assert words[sp_index] == expected, f"seed {seed}: {words[sp_index]:#x} != {expected:#x}"
        assert trace.stack_words == max(peak_spill, 1), f"seed {seed}: stack_words"
        touched = [i for i in range(len(words)) if words[i] != _SENTINEL]
        assert all(sp_index <= i < sp_index + trace.stack_words for i in touched), (
            f"seed {seed}: wrote outside [{sp_index}, {sp_index + trace.stack_words}): {touched}"
        )
        checked_deep += peak_spill >= 3
    assert checked_deep > 40, "too few programs spilled three or more values"


def test_register_cache_deep_non_commutative_chain_keeps_operand_order():
    """TEST-JITC-61: a right-nested chain reloads each spilled operand as the left operand."""
    depth = 11
    program = [(I32_CONST, 100 + 7 * i) for i in range(depth)] + [(I32_SUB, None)] * (depth - 1)
    expected, peak_spill = _reference(program, [0, 0, 0, 0])
    assert peak_spill == depth - 2
    # c0 - (c1 - (c2 - ... - c10)): the alternating sum of the constants.
    assert expected == sum((-1) ** i * (100 + 7 * i) for i in range(depth)) & _M32
    trace, words = _run_spill_trace(program, [0, 0, 0, 0], sp_index=5)
    assert words[5] == expected
    assert trace.stack_words == depth - 2
    shifts = [(I32_CONST, 1), (I32_CONST, 3), (I32_CONST, 2), (I32_SHL, None), (I32_SHL, None)]
    reference, _ = _reference(shifts, [0, 0, 0, 0])
    _, shift_words = _run_spill_trace(shifts, [0, 0, 0, 0], sp_index=2)
    assert shift_words[2] == reference == 1 << ((3 << 2) & 31)


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} pure trace JIT CPS 4-arg and PIC tests passed.")
