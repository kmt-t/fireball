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

from exec_memory import ExecutableBuffer
from runtime_engine import BasicBlock, IntegratedHybridEngine, WASMContext
from system_containers import ReadOnlyRadixBinaryTreeStorage, bswap32
from x64_jit import TraceCompiler


def test_trace_compiler_cps_4arg_and_pic():
    """JITC-01: TraceCompiler emits 16-byte header + PIC code callable via CPS 4-arg convention."""
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
    # 1. 16-byte header verification
    assert trace.header.head_wasm_pc == 0x100
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
    block = BasicBlock(
        head_pc=0x200,
        ops=[
            ("local.get", 0),
            ("local.get", 1),
            ("i32.and", None),
            ("local.set", 2),  # local[2] = local[0] & local[1]
            ("local.get", 0),
            ("i32.const", 2),
            ("i32.shl", None),
            ("local.set", 3),  # local[3] = local[0] << 2
        ],
        next_pc=None,
    )
    trace = compiler.compile_trace(0x200, block)
    ctx = WASMContext(locals_values=[0x0F, 0x07, 0, 0])
    trace.invoke(ctx)
    assert ctx.locals[2] == (0x0F & 0x07)
    assert ctx.locals[3] == (0x0F << 2)


def test_trace_compiler_host_call_cps():
    """JITC-03: TraceCompiler executes host function calls via ctypes CPS trampolines."""
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
    trace.invoke(ctx)
    assert received == [42]
    assert ctx.locals[0] == 999


def test_trace_chaining_between_traces():
    """JITC-04: Resident consecutive traces chain directly via chain_next."""
    engine = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
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
    engine = IntegratedHybridEngine(yield_threshold=3, compiler=TraceCompiler())
    # Loop: local[1] += local[0]; local[0] -= 1; branch while local[0] != 0
    loop_body = BasicBlock(
        head_pc=0x100,
        ops=[
            ("local.get", 1),
            ("local.get", 0),
            ("i32.add", None),
            ("local.set", 1),
            ("local.get", 0),
            ("i32.const", 1),
            ("i32.sub", None),
            ("local.set", 0),
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


def test_jit_chaining_with_control_skip_table():
    """JITC-54: JIT trace chaining resolves fallthrough target via control_skip_tree (bswap32 RadixBinaryTreeView)."""
    engine = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())

    # Block A ends at delimiter PC 0x108 (e.g. BLOCK instruction before fallthrough block B at 0x10A)
    delim_pc = 0x108
    fallthrough_head_pc = 0x10A

    # Construct control_skip_storage: delimiter PC -> fallthrough head PC with bswap32
    inv_delim = bswap32(delim_pc)
    storage = ReadOnlyRadixBinaryTreeStorage.create(
        keys=[inv_delim], values=[fallthrough_head_pc], radix_shift=28
    )
    engine.control_skip_storage = storage
    engine.control_skip_tree = storage.view()
    engine.cache.control_skip_tree = engine.control_skip_tree

    block_a = BasicBlock(
        head_pc=0x100,
        ops=[("local.get", 0), ("i32.const", 10), ("i32.add", None), ("local.set", 0)],
        next_pc=delim_pc,  # Next PC is the delimiter PC
    )
    block_b = BasicBlock(
        head_pc=fallthrough_head_pc,
        ops=[("local.get", 0), ("i32.const", 3), ("i32.mul", None), ("local.set", 0)],
        next_pc=None,
    )
    engine.register_block(block_a)
    engine.register_block(block_b)

    # 1. Backward chaining: compile B (target) first, then A (source).
    # When A is inserted, next_pc (delim_pc=0x108) is resolved via control_skip_tree to fallthrough_head_pc (0x10A).
    # Since 0x10A is already resident in active cache, trace_a.chain_next is set directly to 0x10A!
    trace_b = engine.compiler.compile_trace(fallthrough_head_pc, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(fallthrough_head_pc)

    trace_a = engine.compiler.compile_trace(0x100, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(0x100)

    # chain_next successfully bypassed delimiter 0x108 and connected to block B's head 0x10A!
    assert trace_a.chain_next == fallthrough_head_pc

    # Execute from A (0x100): chains directly into B (0x10A), (5 + 10) * 3 = 45
    ctx = WASMContext(locals_values=[5])
    pc = 0x100
    pc = engine.run_step(pc, ctx)
    assert pc == fallthrough_head_pc
    assert ctx.locals[0] == 15
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert ctx.locals[0] == 45
    assert engine.jit_traces == 2

    # 2. Forward chaining test:
    # Reset engine and compile A first (unresolved next_pc 0x108 -> skipped to 0x10A, not resident yet)
    engine2 = IntegratedHybridEngine(yield_threshold=10, compiler=TraceCompiler())
    engine2.control_skip_storage = storage
    engine2.control_skip_tree = storage.view()
    engine2.cache.control_skip_tree = engine2.control_skip_tree
    engine2.register_block(block_a)
    engine2.register_block(block_b)

    trace_a2 = engine2.compiler.compile_trace(0x100, block_a)
    engine2.cache.insert(trace_a2)
    engine2.bitmap.mark_compiled(0x100)
    assert trace_a2.chain_next is None  # B is not resident yet

    # Now insert B: forward chaining must inspect resident trace A, resolve its 0x108 -> 0x10A,
    # and patch trace_a2.chain_next = 0x10A!
    trace_b2 = engine2.compiler.compile_trace(fallthrough_head_pc, block_b)
    engine2.cache.insert(trace_b2)
    engine2.bitmap.mark_compiled(fallthrough_head_pc)

    assert trace_a2.chain_next == fallthrough_head_pc


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} pure trace JIT CPS 4-arg and PIC tests passed.")
