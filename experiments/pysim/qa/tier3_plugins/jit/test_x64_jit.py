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
    I32_ADD,
    I32_AND,
    I32_CONST,
    I32_EQ,
    I32_GE_S,
    I32_GT_S,
    I32_LE_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_SUB,
    I32_XOR,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)
from tier2_runtime.wasm.reader import parse_standalone


def _loader_block(wat_body: str, result: str = "", params: str = "", locals_decl: str = ""):
    """Compile the nonempty loader block of one Wasm function through the Loader.

    The function is `(func PARAMS RESULT LOCALS BODY)`; `result` is a value type or empty.
    The module is returned with the block because the trace reads its code through the module.
    """
    result_clause = f"(result {result})" if result else ""
    module = parse_standalone(
        wat_to_wasm(
            f'(module (func (export "f") {params} {result_clause} {locals_decl} {wat_body}))'
        )
    )
    block = next(b for b in module.blocks if b.byte_span > 0)
    trace = compile_module_block(TraceCompiler(), module, block)
    assert trace is not None
    return module, block, trace


def _invoke_loader_block(module, block, trace, ctx) -> int:
    """Run a loader trace on the shared context; ctx.code is the module code and ctx.ip the head."""
    code = bytes(module.source)
    ctx.code = ctypes.cast(ctypes.c_char_p(code), ctypes.c_void_p).value
    ctx.ip = block.head_pc
    return trace.invoke(ctx)


def _extract_test_blocks(code: bytes):
    code_view = memoryview(code)
    return extract_basic_blocks(code_view, build_control_map(code_view))


def _call_trace(trace: JITTrace, context, local_words, stack_words, sp_index: int = 0) -> None:
    """Invoke a trace's shared entry with its header-located constant pool as the rel argument."""
    context.local_base = ctypes.addressof(local_words)
    code_address = int(context.code or 0)
    ip = ctypes.c_void_p(code_address + int(context.ip))
    stack_address = ctypes.addressof(stack_words) + sp_index * ctypes.sizeof(ctypes.c_uint32)
    body_address = trace.raw_addr
    assert body_address is not None
    header_address = body_address - JIT_X64_TRACE_HEADER_BYTES
    header = native_abi.NativeTraceHeader.from_address(header_address)
    rel = ctypes.c_void_p(header_address + header.const_rel_offset)
    trace.fn(ctypes.byref(context), ip, ctypes.c_void_p(stack_address), rel)


def _assert_trace_uses_common_chain_dispatcher(trace: JITTrace) -> None:
    assert trace.raw_addr is not None and trace.code_offset is not None
    header_address = trace.raw_addr - JIT_X64_TRACE_HEADER_BYTES
    header = native_abi.NativeTraceHeader.from_address(header_address)
    common_dispatcher = header_address + header.common_code_relative
    region_address = header_address - trace.code_offset
    assert common_dispatcher == region_address + COMMON_CHAIN_DISPATCH_OFFSET


def test_x64_wide_arithmetic_uses_native_stencils():
    """Wide arithmetic executes through the compiled stencil and Runtime API paths."""
    ctx = ExecutionContext()

    def run_i64(op: str, left: int, right: int, expected: int) -> None:
        ctx.stack.set_size(0)
        module, block, trace = _loader_block(
            f"(i64.const {left}) (i64.const {right}) ({op})", "i64"
        )
        _invoke_loader_block(module, block, trace, ctx)
        assert tuple(ctx.stack) == (expected & 0xFFFF_FFFF, expected >> 32)

    def run_f32(op: str, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        module, block, trace = _loader_block(
            f"(f32.const {left}) (f32.const {right}) ({op})", "f32"
        )
        _invoke_loader_block(module, block, trace, ctx)
        actual = struct.unpack("<f", struct.pack("<I", ctx.stack[0]))[0]
        assert abs(actual - expected) < 1e-6

    def run_f64(op: str, left: float, right: float, expected: float) -> None:
        ctx.stack.set_size(0)
        module, block, trace = _loader_block(
            f"(f64.const {left}) (f64.const {right}) ({op})", "f64"
        )
        _invoke_loader_block(module, block, trace, ctx)
        actual = struct.unpack("<d", struct.pack("<II", ctx.stack[0], ctx.stack[1]))[0]
        assert abs(actual - expected) < 1e-12

    run_i64("i64.add", 0x0000_0001_0000_0002, 3, 0x0000_0001_0000_0005)
    run_i64("i64.sub", 9, 4, 5)
    run_i64("i64.mul", 9, 4, 36)
    run_f32("f32.add", 1.5, 2.25, 3.75)
    run_f32("f32.sub", 9.0, 4.0, 5.0)
    run_f32("f32.mul", 9.0, 4.0, 36.0)
    run_f32("f32.div", 9.0, 4.0, 2.25)
    run_f64("f64.add", 1.5, 2.25, 3.75)
    run_f64("f64.sub", 9.0, 4.0, 5.0)
    run_f64("f64.mul", 9.0, 4.0, 36.0)
    run_f64("f64.div", 9.0, 4.0, 2.25)


def test_trace_compiler_cps_4arg_and_pic():
    """TEST-JITC-01, TEST-JITC-22: Verify the logical entry ABI and execute a CPS trace."""
    # Block: local[1] = (local[0] + 10) * 3 - 5 -- real WASM bytecode, run through
    # the Loader's block metadata. Local 0 is the parameter, local 1 is declared.
    module, block, trace = _loader_block(
        "(local.get 0) (i32.const 10) (i32.add) (i32.const 3) (i32.mul) (i32.const 5) (i32.sub)"
        " (local.set 1)",
        params="(param i32)",
        locals_decl="(local i32)",
    )
    # The ordinary entry begins at the first assembled stencil.
    assert trace.head_pc == block.head_pc
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
    _call_trace(trace, context, locals_arr, stack_words)
    # The precomputed offset of the second i32 local is raw word 1.
    assert locals_arr[1] == 40


def test_x64_division_and_remainder_use_runtime_api_stencils() -> None:
    """Integer div/rem call their registered runtime APIs and preserve Wasm trap behavior."""
    for operation, left, right, expected in (
        ("i32.div_s", 0xFFFF_FFF9, 2, 0xFFFF_FFFD),
        ("i32.div_u", 0xFFFF_FFF0, 2, 0x7FFF_FFF8),
        ("i32.rem_s", 0xFFFF_FFF9, 2, 0xFFFF_FFFF),
        ("i32.rem_u", 0xFFFF_FFF0, 3, 0),
    ):
        module, block, trace = _loader_block(
            f"(i32.const {left}) (i32.const {right}) ({operation})", "i32"
        )
        ctx = ExecutionContext()
        _invoke_loader_block(module, block, trace, ctx)
        assert ctx.stack[0] == expected, (operation, ctx.stack[0], expected)


def test_trace_compiler_bitwise_and_shifts_pic():
    """TEST-JITC-02: TraceCompiler compiles bitwise ops into PIC code."""
    # local[2] = local[0] & local[1]; local[3] = local[0] << 2
    module, block, trace = _loader_block(
        "(local.get 0) (local.get 1) (i32.and) (local.set 2)"
        " (local.get 0) (i32.const 2) (i32.shl) (local.set 3)",
        params="(param i32 i32)",
        locals_decl="(local i32 i32)",
    )
    ctx = ExecutionContext()
    assert_result_1 = ctx.local_stack.extend((15, 7, 0, 0))
    assert assert_result_1
    _invoke_loader_block(module, block, trace, ctx)
    assert ctx.local_stack[2] == (0x0F & 0x07)
    assert ctx.local_stack[3] == (0x0F << 2)


def test_branch_traces_return_through_interpreter_cps():
    """A branch terminator returns to the Interpreter CPS handler."""
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
    # Compile trace B first, then A. A branch terminator still leaves JIT unchained.
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)
    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)
    assert trace_a.chain_next is None
    assert trace_a.header.chain_target_addr == 0
    results = engine.call(Interpreter(mod), 0, [10]).unwrap()
    assert results[0] == 30
    assert engine.stat_jit_invocations >= 2
    assert engine.stat_native_control_handlers > 0


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
    results = engine.call(Interpreter(mod, yield_threshold=3), 0, [5]).unwrap()
    assert results[0] == 15
    # Each iteration enters the trace; Interpreter CPS counts the backedges
    # and returns at the shared yield boundary.
    assert engine.stat_jit_invocations == 2
    assert engine.stat_native_control_handlers > 0
    assert engine.stat_interp_steps >= 3
    assert engine.jit_runtime.cache.find_trace(loop_pc) is not None


def test_jit_chaining_uses_loader_resolved_successors():
    """TEST-JITC-54: Straight-line successor chaining follows Loader metadata across delimiters."""
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 10
          i32.add
          local.set 0
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

    # Make B resident before compiling A so the delimiter-resolved straight-line edge can link.
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)

    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)

    # The `end` delimiter has no emitted operation; Loader metadata resolves to B.
    assert trace_a.chain_next == block_b.head_pc
    _assert_trace_uses_common_chain_dispatcher(trace_a)

    # Execute from A: the chain passes the delimiter and runs B; result is (5 + 10) * 3.
    results = engine.call(Interpreter(mod), 0, [5]).unwrap()
    assert results[0] == 45
    assert engine.stat_jit_invocations >= 2

    # 2. Forward chaining test:
    engine2 = make_runtime_engine(jit_compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2, block_b2 = instruction_blocks(mod2)[:2]

    trace_a2 = compile_module_block(engine2.jit_runtime.jit_compiler, mod2, block_a2)
    engine2.jit_runtime.cache.insert(trace_a2)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Inserting B resolves A's Loader-described straight-line successor.
    trace_b2 = compile_module_block(engine2.jit_runtime.jit_compiler, mod2, block_b2)
    engine2.jit_runtime.cache.insert(trace_b2)

    assert trace_a2.chain_next == block_b2.head_pc


def test_trace_local_addressing_uses_precomputed_compact_offsets():
    """TEST-JITC-59: direct local displacements use loader-computed offsets."""
    for params, types in (
        ("(param i32 i32 i32)", (I32, I32, I32)),
        ("(param i32 i64 i32)", (I32, I64, I32)),
    ):
        layout = LocalLayout(types)
        _, _, trace = _loader_block("(local.get 0) (local.set 2)", params=params)
        assert trace is not None
        locals_arr = (ctypes.c_uint32 * layout.total_words)()
        for index in range(len(locals_arr)):
            locals_arr[index] = 0x10203040 + index
        expected = list(locals_arr)
        expected[layout.offset(2)] = expected[layout.offset(0)]
        context = ExecutionContextABI()
        stack_words = (ctypes.c_uint32 * 8)()
        _call_trace(trace, context, locals_arr, stack_words)
        assert list(locals_arr) == expected, f"types {types}: all untouched words must survive"


def test_trace_local_layout_is_fixed_during_compilation():
    """TEST-JITC-65: local offsets and sizes are constants in the compiled trace."""
    value = 0x0123_4567_89AB_CDEF
    words = (value & 0xFFFF_FFFF, value >> 32)

    module, block, get_trace = _loader_block("(local.get 0)", result="i64", params="(param i64)")
    assert get_trace is not None
    context = ExecutionContext()
    assert_result_2 = context.local_stack.extend(words)
    assert assert_result_2
    _invoke_loader_block(module, block, get_trace, context)
    assert tuple(context.stack) == words
    assert context.local_stack.raw_at(0) == words[0]
    assert context.local_stack.raw_at(1) == words[1]
    assert get_trace.code_blob is not None

    module, block, add_trace = _loader_block(
        "(local.get 0) (i64.const 1) (i64.add)", result="i64", params="(param i64)"
    )
    assert add_trace is not None
    context = ExecutionContext()
    assert_result_3 = context.local_stack.extend(words)
    assert assert_result_3
    _invoke_loader_block(module, block, add_trace, context)
    result = value + 1
    assert tuple(context.stack) == (result & 0xFFFF_FFFF, result >> 32)

    for opcode, instruction, result_type in (
        (LOCAL_SET, "local.set", ""),
        (LOCAL_TEE, "local.tee", "i64"),
    ):
        module, block, trace = _loader_block(
            f"(i64.const {value}) ({instruction} 0)",
            result=result_type,
            params="(param i64)",
        )
        assert trace is not None
        context = ExecutionContext()
        assert_result_7 = context.local_stack.extend((0, 0))
        assert assert_result_7
        _invoke_loader_block(module, block, trace, context)
        assert context.local_stack.raw_at(0) == words[0]
        assert context.local_stack.raw_at(1) == words[1]
        expected_stack = words if opcode == LOCAL_TEE else ()
        assert tuple(context.stack) == expected_stack


def test_i32_const_local_store_preserves_existing_stack():
    """The constant store (ordinary path, no fusion) has no net operand-stack effect."""
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


def test_local_offsets_without_index_cap():
    """High local indexes use the ordinary per-instruction stencils when their offsets fit."""
    value = 0x1234_5678
    locals_decl = "(local " + "i32 " * 16 + ")"

    module, block, store_trace = _loader_block(
        f"(i32.const 2779077210) (i32.const {value}) (local.set 8)",
        result="i32",
        locals_decl=locals_decl,
    )
    assert store_trace is not None
    context = ExecutionContext()
    assert_result_4 = context.local_stack.extend((0,) * 16)
    assert assert_result_4
    _invoke_loader_block(module, block, store_trace, context)
    assert context.local_stack[8] == value
    assert tuple(context.stack) == (0xA5A5_5A5A,)

    module, block, pair_trace = _loader_block(
        "(local.get 8) (local.get 9) (i32.add)", result="i32", locals_decl=locals_decl
    )
    assert pair_trace is not None
    context = ExecutionContext()
    assert_result_6 = context.local_stack.extend((0,) * 8 + (17, 25) + (0,) * 6)
    assert assert_result_6
    _invoke_loader_block(module, block, pair_trace, context)
    assert tuple(context.stack) == (42,)


def test_local_stencils_encode_each_compact_offset_into_the_trace():
    """The x64 instruction encoder writes each compact local displacement."""
    layout_decl = "(local " + "i32 " * 16 + ")"
    for result_type, body in (
        ("i32", "(local.get 7)"),
        ("", "(i32.const 3405691582) (local.set 7)"),
        ("i32", "(i32.const 3405691582) (local.tee 7)"),
    ):
        _, _, trace = _loader_block(body, result=result_type, locals_decl=layout_decl)
        assert trace is not None

    wide_layout = LocalLayout((I64,) * 16)
    wide_decl = "(local " + "i64 " * 16 + ")"
    for operand in (8, 9, 15):
        module, block, trace = _loader_block(
            f"(local.get {operand})", result="i64", locals_decl=wide_decl
        )
        assert trace is not None
        context = ExecutionContext()
        values = tuple(range(32))
        assert_result_10 = context.local_stack.extend(values)
        assert assert_result_10
        _invoke_loader_block(module, block, trace, context)
        start = wide_layout.offset(operand)
        assert tuple(context.stack) == values[start : start + 2]

    # The local count is capped only by the loader (FB_CONF_MAX_LOCALS); 17 locals compile, and an
    # offset past byte 127 uses the disp32 form.
    many_locals = LocalLayout((I32,) * 40)
    module, block, far_local = _loader_block(
        "(local.get 39)", result="i32", locals_decl="(local " + "i32 " * 40 + ")"
    )
    assert far_local is not None
    context = ExecutionContext()
    extended = context.local_stack.extend(tuple(range(64)))
    assert extended
    _invoke_loader_block(module, block, far_local, context)
    assert tuple(context.stack) == (many_locals.offset(39),)


def test_jit_embeds_immutable_numeric_immediates_in_native_code():
    """Numeric constants live in generated instructions, not the trace-tail pool."""
    cases = (
        ("i32.const 305419896", "i32", 0x1234_5678, 4),
        ("f32.const nan:0x412345", "f32", 0x7FC1_2345, 4),
        ("i64.const 81985529216486895", "i64", 0x0123_4567_89AB_CDEF, 8),
        ("f64.const nan:0x8123456789abc", "f64", 0x7FF8_1234_5678_9ABC, 8),
    )
    for instruction, result_type, value, width in cases:
        _, _, trace = _loader_block(f"({instruction})", result=result_type)
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


_SPILL_OP_NAMES = {
    I32_ADD: "i32.add",
    I32_SUB: "i32.sub",
    I32_MUL: "i32.mul",
    I32_AND: "i32.and",
    I32_OR: "i32.or",
    I32_XOR: "i32.xor",
    I32_SHL: "i32.shl",
    I32_SHR_U: "i32.shr_u",
    I32_SHR_S: "i32.shr_s",
    I32_EQ: "i32.eq",
    I32_NE: "i32.ne",
    I32_LT_S: "i32.lt_s",
    I32_LT_U: "i32.lt_u",
    I32_GT_S: "i32.gt_s",
    I32_LE_U: "i32.le_u",
    I32_GE_S: "i32.ge_s",
}


def _spill_wat(program) -> str:
    """Wasm text of an RPN program: each entry is one instruction of the function body."""
    parts = []
    for op, arg in program:
        if op == I32_CONST:
            parts.append(f"(i32.const {arg})")
        elif op == LOCAL_GET:
            parts.append(f"(local.get {arg})")
        else:
            parts.append(f"({_SPILL_OP_NAMES[op]})")
    return " ".join(parts)


def _run_spill_trace(program, locals_values, sp_index: int, total: int = 96):
    """Run the compiled program at `sp_index` in a sentinel-filled operand stack buffer."""
    params = "(param " + "i32 " * len(locals_values) + ")"
    _, _, trace = _loader_block(_spill_wat(program), result="i32", params=params)
    assert trace is not None, "a straight-line i32 expression must compile"
    words = (ctypes.c_uint32 * total)(*([_SENTINEL] * total))
    locals_arr = (ctypes.c_uint32 * len(locals_values))(*locals_values)
    context = ExecutionContextABI()
    _call_trace(trace, context, locals_arr, words, sp_index)
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
        assert trace.stack_words <= max(peak_depth, 1), f"seed {seed}: stack_words"

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
