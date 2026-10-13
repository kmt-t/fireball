from __future__ import annotations

from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

"""
experiments/pysim/qa/tier3_plugins/jit/test_x64_jit.py
Spec-compliant tests for the Fireball x64 assembly-stencil JIT compiler.
Verifies:
1. Exact JIT 4-argument calling convention: (ctx*, ip*, sp*, rel*)
2. 40-byte x64 physical JIT trace header immediately before its entry stencil
3. Shared common-area entry/exit routing
4. Common-code trace chaining and hybrid tiering transitions
(docs/components/tier3_plugins/jit_compiler.md and docs/components/tier2_runtime/interpreter.md)
"""

import ctypes
import struct

from config import (
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_X64_TRACE_HEADER_BYTES,
)
from qa.private import jit_native_abi as native_abi
from qa.shared.common_code import (
    COMMON_CHAIN_DISPATCH_OFFSET,
)
from qa.shared.helpers import make_native_interpreter as Interpreter
from qa.shared.helpers import wat_to_wasm
from qa.shared.jit_cache import JITTrace
from qa.shared.runtime_support import (
    compile_module_block,
    compile_test_block,
    instruction_blocks,
    make_runtime_engine,
)
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.abi.interpreter_abi import (
    ExecutionContextABI,
)
from tier2_runtime.interpreter.control_flow import build_control_map, extract_basic_blocks
from tier2_runtime.interpreter.interpreter import ExecutionContext
from tier2_runtime.wasm.module import I32, I64, BasicBlock, LocalLayout
from tier2_runtime.wasm.opcodes import (
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
    LOCAL_TEE,
)


def _extract_test_blocks(code: bytes):
    code_view = memoryview(code)
    return extract_basic_blocks(code_view, build_control_map(code_view))


def _call_trace(fn, context, local_words, stack_words, sp_index: int = 0) -> None:
    """Invoke a trace with its header-located constant pool as the rel argument."""
    context.local_base = ctypes.addressof(local_words)
    code_address = int(context.code or 0)
    ip = ctypes.c_void_p(code_address + int(context.ip))
    stack_address = ctypes.addressof(stack_words) + sp_index * ctypes.sizeof(ctypes.c_uint32)
    entry_address = ctypes.cast(fn, ctypes.c_void_p).value
    assert entry_address is not None
    header_address = entry_address - JIT_X64_TRACE_HEADER_BYTES
    header = native_abi.NativeTraceHeader.from_address(header_address)
    rel = ctypes.c_void_p(header_address + header.const_rel_offset)
    fn(ctypes.byref(context), ip, ctypes.c_void_p(stack_address), rel)


def _assert_trace_uses_common_chain_dispatcher(trace: JITTrace) -> None:
    assert trace.raw_addr is not None and trace.code_offset is not None
    header_address = trace.raw_addr - JIT_X64_TRACE_HEADER_BYTES
    header = native_abi.NativeTraceHeader.from_address(header_address)
    common_dispatcher = header_address + header.common_code_relative
    region_address = header_address - trace.code_offset
    assert common_dispatcher == region_address + COMMON_CHAIN_DISPATCH_OFFSET


def test_x64_wide_arithmetic_uses_native_stencils():
    """Wide arithmetic executes through the compiled stencil and Runtime API paths."""
    compiler = TraceCompiler()
    ctx = ExecutionContext()

    def run_i64(op, left: int, right: int, expected: int) -> None:
        ctx.stack.set_size(0)
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((I64_CONST, left), (I64_CONST, right), (op, None)),
            next_pc=3,
            loops_to=None,
            byte_length=3,
            local_layout=LocalLayout(()),
        )
        assert trace is not None
        trace.invoke(ctx)
        assert tuple(ctx.stack) == (expected & 0xFFFF_FFFF, expected >> 32)

    def run_f32(op, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        left_bits = struct.unpack("<I", struct.pack("<f", left))[0]
        right_bits = struct.unpack("<I", struct.pack("<f", right))[0]
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((F32_CONST, left_bits), (F32_CONST, right_bits), (op, None)),
            next_pc=3,
            loops_to=None,
            byte_length=3,
            local_layout=LocalLayout(()),
        )
        assert trace is not None
        trace.invoke(ctx)
        actual = struct.unpack("<f", struct.pack("<I", ctx.stack[0]))[0]
        assert abs(actual - expected) < 1e-6

    def run_f64(op, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        left_bits = struct.unpack("<Q", struct.pack("<d", left))[0]
        right_bits = struct.unpack("<Q", struct.pack("<d", right))[0]
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((F64_CONST, left_bits), (F64_CONST, right_bits), (op, None)),
            next_pc=3,
            loops_to=None,
            byte_length=3,
            local_layout=LocalLayout(()),
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


def test_trace_compiler_cps_4arg_and_pic():
    """TEST-JITC-01/22: Verify the logical entry ABI and execute a CPS trace."""
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
    head_pc, next_pc, loops_to, frame_depth, byte_span = _extract_test_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = compile_test_block(compiler, code, block, (I32, I32))
    # The ordinary entry begins at the first assembled stencil.
    assert trace.head_pc == head_pc
    assert trace.size_bytes > 31
    assert trace.raw_addr is not None and trace.code_offset is not None
    header_address = trace.raw_addr - JIT_X64_TRACE_HEADER_BYTES
    region_address = header_address - trace.code_offset
    header = native_abi.NativeTraceHeader.from_address(header_address)
    branch_target = header_address + header.common_code_relative
    assert branch_target == region_address + COMMON_CHAIN_DISPATCH_OFFSET
    # Direct invocation uses the platform C ABI; the assembly source follows its argument register mapping.
    locals_arr = (ctypes.c_uint32 * 8)()
    locals_arr[0] = 5
    context = ExecutionContextABI()
    stack_words = (ctypes.c_uint32 * 8)()
    _call_trace(trace.fn, context, locals_arr, stack_words)
    # The precomputed offset of the second i32 local is raw word 1.
    assert locals_arr[1] == 40


def test_x64_division_and_remainder_use_runtime_api_stencils() -> None:
    """Integer div/rem call their registered runtime APIs and preserve Wasm trap behavior."""
    compiler = TraceCompiler()
    for operation, left, right, expected in (
        (I32_DIV_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFD),
        (I32_DIV_U, 0xFFFF_FFF0, 2, 0x7FFF_FFF8),
        (I32_REM_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFF),
        (I32_REM_U, 0xFFFF_FFF0, 3, 0),
    ):
        instructions = [(I32_CONST, left), (I32_CONST, right), (operation, None)]
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=instructions,
            next_pc=len(instructions),
            loops_to=None,
            byte_length=len(instructions),
            local_layout=LocalLayout(()),
        )
        assert trace is not None
        ctx = ExecutionContext()
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
    head_pc, next_pc, loops_to, frame_depth, byte_span = _extract_test_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = compile_test_block(compiler, code, block, (I32, I32, I32, I32))
    ctx = ExecutionContext()
    assert_result_1 = ctx.local_stack.extend((15, 7, 0, 0))
    assert assert_result_1
    trace.invoke(ctx)
    assert ctx.local_stack[2] == (0x0F & 0x07)
    assert ctx.local_stack[3] == (0x0F << 2)


def test_trace_chaining_between_traces():
    """TEST-JITC-04: Resident consecutive traces chain through common code."""
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a, block_b = instruction_blocks(mod)[:2]
    # Compile trace B first, then A (enabling immediate forward chaining)
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)
    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)
    assert trace_a.chain_next == block_b.head_pc
    _assert_trace_uses_common_chain_dispatcher(trace_a)
    results = engine.call(Interpreter(mod), 0, [10])
    assert results[0] == 30
    assert engine.stat_jit_invocations >= 2


def test_hybrid_interpreter_to_jit_trace_elevation():
    """TEST-JITR-64: Warm-up enters a JIT trace with C++ LOOP dispatch."""
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = next(block.head_pc for block in instruction_blocks(mod) if block.loops_to is not None)
    results = engine.call(Interpreter(mod, yield_threshold=3), 0, [5])
    assert results[0] == 15
    # The C++ dispatcher repeats the JIT body and invokes its C++ branch handler
    # before returning at the count-based yield boundary.
    assert engine.stat_jit_invocations > 1
    assert engine.stat_native_control_handlers > 0
    assert engine.stat_interp_steps >= 3
    assert engine.jit_runtime.cache.find_trace(loop_pc) is not None


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
    engine = make_runtime_engine(jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a, block_b = instruction_blocks(mod)[:2]

    # Make B resident before compiling A so the native successor chain can be formed.
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)

    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)

    # Logical successor metadata resolves past the block delimiter to B's head.
    assert trace_a.chain_next == block_b.head_pc
    _assert_trace_uses_common_chain_dispatcher(trace_a)

    # Execute from A: the common chain dispatcher tail-jumps into B; result is (5 + 10) * 3.
    results = engine.call(Interpreter(mod), 0, [5])
    assert results[0] == 45
    assert engine.stat_jit_invocations >= 2

    # 2. Forward chaining test:
    engine2 = make_runtime_engine(jit_compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2, block_b2 = instruction_blocks(mod2)[:2]

    trace_a2 = compile_module_block(engine2.jit_runtime.jit_compiler, mod2, block_a2)
    engine2.jit_runtime.cache.insert(trace_a2)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Inserting B resolves A's logical successor metadata and patches its native chain target.
    trace_b2 = compile_module_block(engine2.jit_runtime.jit_compiler, mod2, block_b2)
    engine2.jit_runtime.cache.insert(trace_b2)

    assert trace_a2.chain_next == block_b2.head_pc


def test_trace_local_addressing_uses_precomputed_compact_offsets():
    """TEST-JITC-59: direct local displacements use loader-computed offsets."""
    code = bytes([LOCAL_GET, 0, LOCAL_SET, 2])
    head_pc, next_pc, loops_to, frame_depth, byte_span = _extract_test_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    for types in ((I32, I32, I32), (I32, I64, I32)):
        layout = LocalLayout(types)
        trace = compile_test_block(TraceCompiler(), code, block, types)
        assert trace is not None
        locals_arr = (ctypes.c_uint32 * layout.total_words)()
        for index in range(len(locals_arr)):
            locals_arr[index] = 0x10203040 + index
        expected = list(locals_arr)
        expected[layout.offset(2)] = expected[layout.offset(0)]
        context = ExecutionContextABI()
        stack_words = (ctypes.c_uint32 * 8)()
        _call_trace(trace.fn, context, locals_arr, stack_words)
        assert list(locals_arr) == expected, f"types {types}: all untouched words must survive"


def test_trace_local_layout_is_fixed_during_compilation():
    """TEST-JITC-65: local offsets and sizes are constants in the compiled trace."""
    compiler = TraceCompiler()
    value = 0x0123_4567_89AB_CDEF
    words = (value & 0xFFFF_FFFF, value >> 32)
    layout = LocalLayout((I64,))

    get_trace = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 0),),
        next_pc=1,
        loops_to=None,
        byte_length=1,
        local_layout=layout,
    )
    assert get_trace is not None
    context = ExecutionContext()
    assert_result_2 = context.local_stack.extend(words)
    assert assert_result_2
    get_trace.invoke(context)
    assert tuple(context.stack) == words
    assert context.local_stack.raw_at(0) == words[0]
    assert context.local_stack.raw_at(1) == words[1]
    assert get_trace.code_blob is not None

    add_trace = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 0), (I64_CONST, 1), (I64_ADD, None)),
        next_pc=1,
        loops_to=None,
        byte_length=3,
        local_layout=layout,
    )
    assert add_trace is not None
    context = ExecutionContext()
    assert_result_3 = context.local_stack.extend(words)
    assert assert_result_3
    add_trace.invoke(context)
    result = value + 1
    assert tuple(context.stack) == (result & 0xFFFF_FFFF, result >> 32)

    for opcode in (LOCAL_SET, LOCAL_TEE):
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((I64_CONST, value), (opcode, 0)),
            next_pc=1,
            loops_to=None,
            byte_length=2,
            local_layout=layout,
        )
        assert trace is not None
        context = ExecutionContext()
        assert_result_7 = context.local_stack.extend((0, 0))
        assert assert_result_7
        trace.invoke(context)
        assert context.local_stack.raw_at(0) == words[0]
        assert context.local_stack.raw_at(1) == words[1]
        expected_stack = words if opcode == LOCAL_TEE else ()
        assert tuple(context.stack) == expected_stack


def test_i32_const_local_set_fusion_preserves_existing_stack():
    """The fused constant store has no net operand-stack effect."""
    compiler = TraceCompiler()
    sentinel = 0xA5A5_5A5A
    value = 0x1234_5678
    for opcode, expected_stack in (
        (LOCAL_SET, (sentinel,)),
        (LOCAL_TEE, (sentinel, value)),
    ):
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((I32_CONST, value), (opcode, 0)),
            next_pc=1,
            loops_to=None,
            byte_length=2,
            local_layout=LocalLayout((I32,)),
        )
        assert trace is not None
        context = ExecutionContext()
        assert_result_8 = context.local_stack.extend((0,))
        assert assert_result_8
        assert_result_9 = context.stack.extend((sentinel,))
        assert assert_result_9
        trace.invoke(context)
        assert context.local_stack[0] == value
        assert tuple(context.stack) == expected_stack


def test_local_fusions_select_by_offset_without_index_cap():
    """High local indexes use fused native stencils when their offsets fit."""
    compiler = TraceCompiler()
    layout = LocalLayout((I32,) * 16)
    value = 0x1234_5678

    store_trace = compiler.compile_instructions(
        head_pc=0,
        instructions=((I32_CONST, value), (LOCAL_SET, 8)),
        next_pc=1,
        loops_to=None,
        byte_length=2,
        local_layout=layout,
    )
    assert store_trace is not None
    context = ExecutionContext()
    assert_result_4 = context.local_stack.extend((0,) * 16)
    assert assert_result_4
    assert_result_5 = context.stack.extend((2779077210,))
    assert assert_result_5
    store_trace.invoke(context)
    assert context.local_stack[8] == value
    assert tuple(context.stack) == (0xA5A5_5A5A,)

    pair_trace = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 8), (LOCAL_GET, 9), (I32_ADD, None)),
        next_pc=1,
        loops_to=None,
        byte_length=3,
        local_layout=layout,
    )
    assert pair_trace is not None
    context = ExecutionContext()
    assert_result_6 = context.local_stack.extend((0,) * 8 + (17, 25) + (0,) * 6)
    assert assert_result_6
    pair_trace.invoke(context)
    assert tuple(context.stack) == (42,)


def test_local_stencils_encode_each_compact_offset_into_the_trace():
    """The x64 instruction encoder writes each compact local displacement."""
    compiler = TraceCompiler()
    layout = LocalLayout((I32,) * 16)
    for opcode in (LOCAL_GET, LOCAL_SET, LOCAL_TEE):
        operand = 7
        instructions = (
            ((opcode, operand),)
            if opcode == LOCAL_GET
            else ((I32_CONST, 0xCAFEBABE), (opcode, operand))
        )
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=instructions,
            next_pc=1,
            loops_to=None,
            byte_length=len(instructions),
            local_layout=layout,
        )
        assert trace is not None

    wide_layout = LocalLayout((I64,) * 16)
    for operand in (8, 9, 15):
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((LOCAL_GET, operand),),
            next_pc=1,
            loops_to=None,
            byte_length=1,
            local_layout=wide_layout,
        )
        assert trace is not None
        context = ExecutionContext()
        values = tuple(range(32))
        assert_result_10 = context.local_stack.extend(values)
        assert assert_result_10
        trace.invoke(context)
        start = wide_layout.offset(operand)
        assert tuple(context.stack) == values[start : start + 2]

    too_many_locals = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 0),),
        next_pc=1,
        loops_to=None,
        byte_length=1,
        local_layout=LocalLayout((I32,) * 17),
    )
    assert too_many_locals is None


def test_jit_embeds_immutable_numeric_immediates_in_native_code():
    """Numeric constants live in generated instructions, not the trace-tail pool."""
    compiler = TraceCompiler()
    cases = (
        (I32_CONST, 0x1234_5678, 4),
        (F32_CONST, 0x7FC1_2345, 4),
        (I64_CONST, 0x0123_4567_89AB_CDEF, 8),
        (F64_CONST, 0x7FF8_1234_5678_9ABC, 8),
    )
    for opcode, value, width in cases:
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((opcode, value),),
            next_pc=1,
            loops_to=None,
            byte_length=1,
            local_layout=LocalLayout(()),
        )
        assert trace is not None and trace.code_blob is not None
        header = native_abi.NativeTraceHeader.from_buffer_copy(
            trace.code_blob[:JIT_X64_TRACE_HEADER_BYTES]
        )
        encoded = value.to_bytes(width, "little")
        code = trace.code_blob[JIT_X64_TRACE_HEADER_BYTES : header.const_rel_offset]
        assert encoded in code
        assert header.const_rel_offset + 4 == header.blob_bytes


# ---------------------------------------------------------------------------
# Every compiled WASM value uses the shared operand stack
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
    for _ in range(max_depth):
        if rng.random() < 0.5:
            program.append((I32_CONST, rng.randrange(-(1 << 31), 1 << 31)))
        else:
            program.append((LOCAL_GET, rng.randrange(4)))
    program.extend((rng.choice(list(_SPILL_OPS)), None) for _ in range(max_depth - 1))
    return program, locals_values


def _reference(program, locals_values) -> tuple[int, int]:
    """Evaluate the program; return (result, peak shared-stack word count)."""
    stack: list[int] = []
    peak_depth = 0
    for op, arg in program:
        if op == I32_CONST:
            stack.append(int(arg) & _M32)
        elif op == LOCAL_GET:
            stack.append(locals_values[int(arg)])
        else:
            right, left = stack.pop(), stack.pop()
            stack.append(_SPILL_OPS[op](left, right))
        peak_depth = max(peak_depth, len(stack))
    assert len(stack) == 1
    return stack[0], peak_depth


def _run_spill_trace(program, locals_values, sp_index: int, total: int = 96):
    """Run the compiled program at `sp_index` in a sentinel-filled operand stack buffer."""
    trace = TraceCompiler().compile_instructions(
        head_pc=0,
        instructions=program,
        next_pc=None,
        loops_to=None,
        byte_length=len(program) * 3,
        local_layout=LocalLayout((I32,) * len(locals_values)),
    )
    assert trace is not None, "a straight-line i32 expression must compile"
    words = (ctypes.c_uint32 * total)(*([_SENTINEL] * total))
    locals_arr = (ctypes.c_uint32 * len(locals_values))(*locals_values)
    context = ExecutionContextABI()
    _call_trace(trace.fn, context, locals_arr, words, sp_index)
    return trace, words


def test_compiled_expressions_match_reference_with_bounded_stack_writes():
    """TEST-JITC-60: Fused traces match WASM and stay within their declared stack range."""
    checked_deep = 0
    for seed in range(240):
        max_depth = 3 + seed % 9
        program, locals_values = _random_rpn(seed, max_depth)
        expected, peak_depth = _reference(program, locals_values)
        sp_index = 3 + seed % 7
        trace, words = _run_spill_trace(program, locals_values, sp_index)
        assert words[sp_index] == expected, f"seed {seed}: {words[sp_index]:#x} != {expected:#x}"
        assert trace.result_words == 1, f"seed {seed}: result_words"
        assert trace.result_words <= trace.stack_words <= max(peak_depth, 1), (
            f"seed {seed}: stack_words"
        )
        touched = [i for i in range(len(words)) if words[i] != _SENTINEL]
        assert all(sp_index <= i < sp_index + trace.stack_words for i in touched), (
            f"seed {seed}: wrote outside [{sp_index}, {sp_index + trace.stack_words}): {touched}"
        )
        checked_deep += peak_depth >= 3
    assert checked_deep > 40, "too few programs spilled three or more values"


def test_shared_stack_deep_dynamic_non_commutative_chain_keeps_operand_order():
    """TEST-JITC-61: Dynamic local operands remain memory-backed across a deep expression."""
    # Keep all local accesses in the direct-variant range so this test focuses
    # on operand-stack spilling rather than rel-offset local access.
    depth = 8
    locals_values = [100 + 7 * i for i in range(depth)]
    program = [(LOCAL_GET, i) for i in range(depth)] + [(I32_SUB, None)] * (depth - 1)
    expected, peak_depth = _reference(program, locals_values)
    assert peak_depth == depth
    # v0 - (v1 - (v2 - ... - v7)): the alternating sum of the locals.
    assert expected == sum((-1) ** i * value for i, value in enumerate(locals_values)) & _M32
    trace, words = _run_spill_trace(program, locals_values, sp_index=5)
    assert words[5] == expected
    assert trace.stack_words == depth
    assert [index for index, word in enumerate(words) if word != _SENTINEL] == list(
        range(5, 5 + depth)
    )
    shifts = [(LOCAL_GET, i) for i in range(3)] + [(I32_SHL, None), (I32_SHL, None)]
    shift_values = [1, 3, 2, 0]
    reference, _ = _reference(shifts, shift_values)
    shift_trace, shift_words = _run_spill_trace(shifts, shift_values, sp_index=2)
    assert shift_words[2] == reference == 1 << ((3 << 2) & 31)
    assert shift_trace.stack_words == 3
    assert [index for index, word in enumerate(shift_words) if word != _SENTINEL] == [2, 3, 4]


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} pure trace JIT ABI and PIC tests passed.")


def test_trace_generated_size_is_variable_and_uses_compact_header():
    """TEST-JITC-23: actual generated length is independent of the 64-byte default."""
    compiler = TraceCompiler()
    sizes = []
    for code in (
        bytes((LOCAL_GET, 0, LOCAL_SET, 0)),
        bytes((LOCAL_GET, 0, I32_CONST, 3, I32_ADD, LOCAL_SET, 0)),
    ):
        head, next_pc, loops_to, depth, span = _extract_test_blocks(code)[0]
        block = BasicBlock(
            head_pc=head, next_pc=next_pc, loops_to=loops_to, frame_depth=depth, byte_span=span
        )
        trace = compile_test_block(compiler, code, block, (I32,))
        assert trace.code_blob is not None
        assert trace.size_bytes == len(trace.code_blob)
        source_header = native_abi.NativeTraceHeader.from_buffer_copy(
            trace.code_blob[:JIT_X64_TRACE_HEADER_BYTES]
        )
        resident_header = trace.header._native
        assert trace.code_offset == JIT_CACHE_ACTIVE_OFFSET_BYTES
        assert resident_header.common_code_relative == (
            COMMON_CHAIN_DISPATCH_OFFSET - JIT_CACHE_ACTIVE_OFFSET_BYTES
        )
        source_header.common_code_relative = resident_header.common_code_relative
        assert bytes(source_header) == trace.header.pack()
        sizes.append(trace.size_bytes)
    assert sizes[1] > sizes[0]
