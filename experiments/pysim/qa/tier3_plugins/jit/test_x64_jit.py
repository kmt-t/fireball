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
Spec-compliant tests for Fireball Trace-based Copy-and-Patch JIT Compiler (x64_jit.py).
Verifies:
1. Exact CPS 4-argument calling convention: (void* ctx, void* sp, void* local_base, uint32_t tos)
2. 32-byte x64 physical JIT trace header immediately before its entry stencil
3. Shared common-area entry/exit routing
4. Common-code trace chaining and hybrid tiering transitions
(docs/components/tier3_plugins/jit_compiler.md and docs/components/tier2_runtime/interpreter.md)
"""

import ctypes
import struct
import sys

from config import (
    JIT_CACHE_ABSOLUTE_ADDRESS_POOL_OFFSET,
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_TRACE_COMMON_PROLOGUE_OFFSET,
    JIT_X64_COMMON_CODE_RELATIVE_OFFSET,
    JIT_X64_TRACE_HEADER_BYTES,
)
from qa.private import jit_native_abi as native_abi
from qa.shared.common_code import (
    COMMON_CHAIN_DISPATCH_OFFSET,
    COMMON_EPILOGUE_OFFSET,
    TRACE_ENTRY_STUB_BYTES,
    JITCodeCacheRegion,
)
from qa.shared.helpers import make_native_interpreter as Interpreter
from qa.shared.helpers import wat_to_wasm
from qa.shared.jit_cache import JITTrace
from qa.shared.runtime_support import compile_module_block, compile_test_block, make_runtime_engine
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.abi.interpreter_abi import (
    NATIVE_OP_CONTINUE,
    CallStackNative,
    ExecutionContextABI,
)
from tier2_runtime.interpreter.control_flow import extract_basic_blocks
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

_I32_HELPER_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
)


def _assert_trace_uses_common_chain_dispatcher(trace: JITTrace) -> None:
    assert trace.code_blob is not None
    chain_jump = bytes.fromhex("4d 63 5e") + bytes((JIT_X64_COMMON_CODE_RELATIVE_OFFSET,))
    chain_jump += bytes.fromhex("4d 01 f3 49 81 c3")
    chain_jump += struct.pack("<I", COMMON_CHAIN_DISPATCH_OFFSET) + bytes.fromhex("41 ff e3")
    assert chain_jump in trace.code_blob[JIT_X64_TRACE_HEADER_BYTES:]


def test_x64_wide_arithmetic_uses_native_stencils():
    """Wide arithmetic executes from native x64 stencils without helper callbacks."""
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
    """TEST-JITC-01/22: Verify the physical entry ABI and execute a CPS trace."""
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
    assert trace.head_pc == head_pc
    assert trace.size_bytes > 31
    assert trace.raw_addr is not None and trace.code_offset is not None
    header_address = trace.raw_addr - JIT_X64_TRACE_HEADER_BYTES
    region_address = header_address - trace.code_offset
    entry_stub = ctypes.string_at(
        header_address + JIT_X64_TRACE_HEADER_BYTES, TRACE_ENTRY_STUB_BYTES
    )
    assert entry_stub == (
        bytes.fromhex("48 8d 05")
        + struct.pack("<i", -(JIT_X64_TRACE_HEADER_BYTES + 7))
        + bytes.fromhex("4c 63 58 10 49 01 c3 41 ff e3")
    )
    header = native_abi.NativeTraceHeader.from_address(header_address)
    branch_target = header_address + header.common_code_relative
    assert branch_target == region_address + JIT_TRACE_COMMON_PROLOGUE_OFFSET
    saved_registers = bytes.fromhex("53 41 54 41 55 41 56 41 57")
    if sys.platform == "win32":
        # Save RDI; retain the stack pointer; R8->R10, RDX->R12, RCX->R13.
        argument_moves = bytes.fromhex("57 48 89 e7 4d 89 c2 49 89 d4 49 89 cd")
    else:
        # Save RBP; retain the stack pointer; RDX->R10, RSI->R12, RDI->R13, ECX->R9D.
        argument_moves = bytes.fromhex("55 48 89 e5 49 89 d2 49 89 f4 49 89 fd 41 89 c9")
    # R14 retains the header and the common prologue jumps to the code after the entry stencil.
    expected_prologue = (
        saved_registers
        + argument_moves
        + bytes.fromhex("49 89 c6 49 8d 46")
        + bytes((JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES,))
        + bytes.fromhex("ff e0")
    )
    assert ctypes.string_at(branch_target, len(expected_prologue)) == expected_prologue
    # 2. Direct call via CPS 4-argument C function pointer fn(ctx, sp, local_base, tos)
    locals_arr = (ctypes.c_uint32 * 8)()
    locals_arr[0] = 5
    res = trace.fn(
        ctypes.c_void_p(0),
        ctypes.c_void_p(0),
        ctypes.cast(locals_arr, ctypes.c_void_p),
        0,
    )
    assert res == NATIVE_OP_CONTINUE
    # The precomputed offset of the second i32 local is raw word 1.
    assert locals_arr[1] == 40


def test_native_common_prologue_preserves_nonzero_tos() -> None:
    """TEST-JITC-22: Observe the fourth ABI argument in the physical R9D register."""
    region = JITCodeCacheRegion()
    # Header, shared entry stencil, then mov [r10], r9d; jmp common epilogue.
    body = bytes.fromhex("45 89 0a e9") + struct.pack(
        "<i",
        COMMON_EPILOGUE_OFFSET
        - (JIT_CACHE_ACTIVE_OFFSET_BYTES + JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES + 8),
    )
    blob_bytes = JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES + len(body)
    header = struct.pack("<QQiIIBBBBB7x", 0, 0, 0, 0, blob_bytes, 1, 0, 0, 1, 0)
    entry = (
        bytes.fromhex("48 8d 05")
        + struct.pack("<i", -(JIT_X64_TRACE_HEADER_BYTES + 7))
        + bytes.fromhex("4c 63 58 10 49 01 c3 41 ff e3")
    )
    fn, _ = region.install_trace(JIT_CACHE_ACTIVE_OFFSET_BYTES, header + entry + body)
    observed = (ctypes.c_uint32 * 1)()
    for expected in (0x1234_5678, 0x8000_0001, 0xFFFF_FFFF):
        observed[0] = 0
        fn(ctypes.c_void_p(0), ctypes.c_void_p(0), ctypes.cast(observed, ctypes.c_void_p), expected)
        assert observed[0] == expected


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
        def helper(
            _ctx: ctypes.c_void_p,
            left: int,
            right: int,
            result: ctypes._Pointer,
        ) -> int:
            calls.append(operation)
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
            return 0

        return _I32_HELPER_TYPE(helper)

    calls: list[int] = []
    for helper_slot, (operation, left, right, expected, suffix) in enumerate(
        (
            (I32_DIV_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFD, True),
            (I32_DIV_U, 0xFFFF_FFF0, 2, 0x7FFF_FFF8, False),
            (I32_REM_S, 0xFFFF_FFF9, 2, 0xFFFF_FFFF, False),
            (I32_REM_U, 0xFFFF_FFF0, 3, 0, False),
        )
    ):
        helper = make_helper(operation)
        instructions = [(I32_CONST, left), (I32_CONST, right), (operation, None)]
        if suffix:
            instructions.extend(((I32_CONST, 3), (I32_ADD, None)))
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=instructions,
            next_pc=len(instructions),
            loops_to=None,
            byte_length=len(instructions),
            local_layout=LocalLayout(()),
            helper_address=ctypes.cast(helper, ctypes.c_void_p).value or 0,
        )
        assert trace is not None
        assert trace.common_helper_offset == 352 + helper_slot * 32
        ctx = ExecutionContext()
        trace.invoke(ctx)
        expected_value = (expected + 3) & 0xFFFF_FFFF if suffix else expected
        assert ctx.stack[0] == expected_value, (operation, ctx.stack[0], expected_value)
    assert calls == [I32_DIV_S, I32_DIV_U, I32_REM_S, I32_REM_U]


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
    ctx = ExecutionContext()
    assert ctx.local_stack.extend((0x0F, 0x07, 0, 0))
    trace.invoke(ctx)
    assert ctx.local_stack[2] == (0x0F & 0x07)
    assert ctx.local_stack[3] == (0x0F << 2)


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
    ctx = ExecutionContext()
    assert ctx.local_stack.extend((10,))
    helper_addr = ctypes.cast(helper_fn, ctypes.c_void_p).value or 0
    trace = compiler.compile_instructions(
        head_pc=head_pc,
        instructions=((LOCAL_GET, 0), (LOCAL_SET, 0)),
        next_pc=next_pc,
        loops_to=loops_to,
        byte_length=byte_span,
        local_layout=LocalLayout((I32,)),
        context_helper=True,
        helper_address=helper_addr,
    )
    assert trace is not None
    trace.invoke(ctx)
    assert ctx.local_stack[0] == 11

    raw_blob = ctypes.string_at(trace.raw_addr - JIT_X64_TRACE_HEADER_BYTES, trace.size_bytes)
    helper_addr_bytes = helper_addr.to_bytes(8, "little")
    assert raw_blob[0x08:0x10] == helper_addr_bytes


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
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]
    # Compile trace B first, then A (enabling immediate forward chaining)
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)
    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)
    assert trace_a.chain_next == block_b.head_pc
    _assert_trace_uses_common_chain_dispatcher(trace_a)
    results = engine.call(Interpreter(mod), 0, [10])
    assert results[0] == 30
    assert engine.stat_jit_invocations == 2


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
    loop_pc = mod.blocks[0].head_pc
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
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]

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
    assert engine.stat_jit_invocations == 2

    # 2. Forward chaining test:
    engine2 = make_runtime_engine(jit_compiler=TraceCompiler())
    mod2 = engine2.load_wasm(wasm_bytes)
    block_a2 = mod2.blocks[0]
    block_b2 = mod2.blocks[1]

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
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
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
        trace.fn(
            ctypes.c_void_p(0), ctypes.c_void_p(0), ctypes.cast(locals_arr, ctypes.c_void_p), 0
        )
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
    assert context.local_stack.extend(words)
    get_trace.invoke(context)
    assert tuple(context.stack) == words
    assert context.local_stack.raw_at(0) == words[0]
    assert context.local_stack.raw_at(1) == words[1]
    assert get_trace.code_blob is not None
    body = get_trace.code_blob[JIT_X64_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES :]
    assert bytes.fromhex("45 8b 4a 00") in body
    assert bytes.fromhex("45 8b 4a 04") in body
    assert bytes.fromhex("45 89 4c 24 00") in body
    assert bytes.fromhex("45 89 4c 24 04") in body
    assert bytes.fromhex("41 ff d2") not in body

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
    assert context.local_stack.extend(words)
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
        assert context.local_stack.extend((0, 0))
        trace.invoke(context)
        assert context.local_stack.raw_at(0) == words[0]
        assert context.local_stack.raw_at(1) == words[1]
        expected_stack = words if opcode == LOCAL_TEE else ()
        assert tuple(context.stack) == expected_stack


def test_local_runtime_api_table_is_used_only_above_128_locals():
    """TEST-JITC-66: local count selects direct code or a PIC runtime API call."""

    def assert_local_api_call(trace, api_index: int) -> None:
        assert trace.code_blob is not None
        table_entry = JIT_CACHE_ABSOLUTE_ADDRESS_POOL_OFFSET + api_index * 8
        pic_call = (
            bytes.fromhex("4d 63 5e")
            + bytes((JIT_X64_COMMON_CODE_RELATIVE_OFFSET,))
            + bytes.fromhex("4d 01 f3 49 81 c3")
            + struct.pack("<I", table_entry)
            + bytes.fromhex("41 ff 13")
        )
        assert pic_call in trace.code_blob

    compiler = TraceCompiler()
    direct = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 127),),
        next_pc=1,
        loops_to=None,
        byte_length=1,
        local_layout=LocalLayout((I32,) * 128),
    )
    assert direct is not None and direct.code_blob is not None
    assert bytes.fromhex("41 ff 13") not in direct.code_blob

    fallback = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 128),),
        next_pc=1,
        loops_to=None,
        byte_length=1,
        local_layout=LocalLayout((I32,) * 129),
    )
    assert fallback is not None and fallback.code_blob is not None
    assert_local_api_call(fallback, 0)

    call_stack = CallStackNative()
    call_stack.size = 1
    frame = call_stack.frames[0]
    frame.local_count = 129
    context = ExecutionContextABI()
    context.call_stack = ctypes.addressof(call_stack)
    context.call_base = 0
    narrow_offsets = (ctypes.c_uint16 * 129)(*range(129))
    narrow_sizes = (ctypes.c_uint8 * 129)(*([1] * 129))
    frame.local_offsets = ctypes.addressof(narrow_offsets)
    frame.local_sizes = ctypes.addressof(narrow_sizes)
    frame.local_slot_count = 129
    locals_array = (ctypes.c_uint32 * 129)()
    locals_array[128] = 0x12345678
    value_stack = (ctypes.c_uint32 * 128)()

    fallback.fn(
        ctypes.byref(context),
        ctypes.cast(value_stack, ctypes.c_void_p),
        ctypes.cast(locals_array, ctypes.c_void_p),
        0,
    )
    assert value_stack[0] == 0x12345678

    for opcode in (LOCAL_SET, LOCAL_TEE):
        trace = compiler.compile_instructions(
            head_pc=0,
            instructions=((I32_CONST, 0xCAFEBABE), (opcode, 128)),
            next_pc=1,
            loops_to=None,
            byte_length=2,
            local_layout=LocalLayout((I32,) * 129),
        )
        assert trace is not None
        assert_local_api_call(trace, 1 if opcode == LOCAL_SET else 2)
        locals_array[128] = 0
        value_stack[0] = 0
        trace.fn(
            ctypes.byref(context),
            ctypes.cast(value_stack, ctypes.c_void_p),
            ctypes.cast(locals_array, ctypes.c_void_p),
            0,
        )
        assert locals_array[128] == 0xCAFEBABE
        if opcode == LOCAL_TEE:
            assert trace.result_words == 1
            assert value_stack[0] == 0xCAFEBABE
        else:
            assert trace.result_words == 0

    wide_layout = LocalLayout((I32,) * 128 + (I64,))
    frame.local_offsets = wide_layout.offsets_address
    frame.local_sizes = wide_layout.sizes_address
    frame.local_slot_count = wide_layout.total_words
    wide_locals = (ctypes.c_uint32 * wide_layout.total_words)()
    wide_value = 0x0123456789ABCDEF
    wide_words = (wide_value & 0xFFFF_FFFF, wide_value >> 32)
    wide_locals[128] = wide_words[0]
    wide_locals[129] = wide_words[1]
    value_stack[0] = value_stack[1] = 0
    wide_get = compiler.compile_instructions(
        head_pc=0,
        instructions=((LOCAL_GET, 128),),
        next_pc=1,
        loops_to=None,
        byte_length=1,
        local_layout=wide_layout,
    )
    assert wide_get is not None
    assert_local_api_call(wide_get, 0)
    wide_get.fn(
        ctypes.byref(context),
        ctypes.cast(value_stack, ctypes.c_void_p),
        ctypes.cast(wide_locals, ctypes.c_void_p),
        0,
    )
    assert (value_stack[0], value_stack[1]) == wide_words

    for opcode in (LOCAL_SET, LOCAL_TEE):
        wide_set = compiler.compile_instructions(
            head_pc=0,
            instructions=((I64_CONST, wide_value), (opcode, 128)),
            next_pc=1,
            loops_to=None,
            byte_length=2,
            local_layout=wide_layout,
        )
        assert wide_set is not None
        assert_local_api_call(wide_set, 1 if opcode == LOCAL_SET else 2)
        wide_locals[128] = wide_locals[129] = 0
        value_stack[0] = value_stack[1] = 0
        wide_set.fn(
            ctypes.byref(context),
            ctypes.cast(value_stack, ctypes.c_void_p),
            ctypes.cast(wide_locals, ctypes.c_void_p),
            0,
        )
        assert (wide_locals[128], wide_locals[129]) == wide_words
        assert wide_set.result_words == (2 if opcode == LOCAL_TEE else 0)
        if opcode == LOCAL_TEE:
            assert (value_stack[0], value_stack[1]) == wide_words


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
    for _ in range(max_depth):
        if rng.random() < 0.5:
            program.append((I32_CONST, rng.randrange(-(1 << 31), 1 << 31)))
        else:
            program.append((LOCAL_GET, rng.randrange(4)))
    program.extend((rng.choice(list(_SPILL_OPS)), None) for _ in range(max_depth - 1))
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
    trace = TraceCompiler().compile_instructions(
        head_pc=0,
        instructions=program,
        next_pc=None,
        loops_to=None,
        byte_length=len(program) * 3,
        local_layout=LocalLayout((I32,) * 4),
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
        assert 1 <= trace.stack_words <= max(peak_spill, 1), f"seed {seed}: stack_words"
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
    assert 1 <= trace.stack_words <= depth - 2
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


def test_trace_generated_size_is_variable_and_uses_compact_header():
    """TEST-JITC-23: actual generated length is independent of the 64-byte default."""
    compiler = TraceCompiler()
    sizes = []
    for code in (
        bytes((LOCAL_GET, 0, LOCAL_SET, 0)),
        bytes((LOCAL_GET, 0, I32_CONST, 3, I32_ADD, LOCAL_SET, 0)),
    ):
        head, next_pc, loops_to, depth, span = extract_basic_blocks(code)[0]
        block = BasicBlock(
            head_pc=head, next_pc=next_pc, loops_to=loops_to, frame_depth=depth, byte_span=span
        )
        trace = compile_test_block(compiler, code, block, (I32,))
        assert trace.code_blob is not None
        assert trace.size_bytes == len(trace.code_blob)
        assert trace.code_blob[:16] == trace.header.pack()
        sizes.append(trace.size_bytes)
    assert sizes[1] > sizes[0]
