from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: vSoC Engine & Multitasking Integration
Traceability: runtime_vsoc_test_spec.md
"""

import socket
import struct
import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _TEST_FILE.parent,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from control_flow import extract_basic_blocks
from hal import (
    UartTransport,
)
from interpreter import Interpreter
from logger import LogDictionary, Logger, LogLevel
from runtime_engine import (
    BasicBlock,
    CardState,
    IntegratedHybridEngine,
    JITTrace,
    RuntimeEngine,
    WASMContext,
)
from system import (
    System,
)
from system_containers import (
    FlatMapView,
    StaticVector,
)
from test_support import PcOnlyCompiler, wat_to_wasm
from wasi import WasiHostContext
from wasm_opcodes import I32_CONST
from wasm_reader import parse
from x64_jit import TraceCompiler


def test_hal_task_ipc_communication():
    """HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from hal import ARG_LENGTH, ARG_OFFSET
    from wasi import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        sysv.spawn_hal_task()
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        nwritten = engine.send_ipc_command(
            "fireball://device/uart/0",
            WasiIpcCmd.STREAM_WRITE_SHM,
            FlatMapView([(ARG_LENGTH, 128), (ARG_OFFSET, 0)]),
        )
        assert nwritten == 128
        assert sysv.hal_task.processed_count == 1
        assert sysv.hal_task.last_handled_uri == "fireball://device/uart/0"
        assert sysv.hal_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_SHM
    finally:
        sysv.shutdown()


def _recv_rsp_frame(client: socket.socket, sysv: System, max_steps: int = 32) -> bytes:
    """
    Receives a complete RSP frame '$...#xx' by driving the COOS scheduler
    across multiple yields. Accumulates chunks until the checksum is received.
    """
    buf = b""
    for _ in range(max_steps):
        sysv.scheduler.step()
        try:
            buf += client.recv(1024)
        except (socket.timeout, BlockingIOError):
            pass
        if b"#" in buf and len(buf.split(b"#")[-1]) >= 2:
            return buf
    raise AssertionError(f"incomplete RSP frame within {max_steps} scheduler steps: {buf!r}")


def test_gdbserver_task_coos_cooperative_execution():
    """DBG-01, DBG-GOTCHA-04: GDBServer operates as an independent task on COOS and handles multi-yield RSP packets."""
    from debugger import DebuggerManager

    sysv = System()
    dbg = DebuggerManager()
    ctx = WASMContext()
    ctx.locals = [10, 20]
    task_id, port = sysv.spawn_gdbserver_task(dbg, start_pc=0x10, ctx=ctx)

    try:
        # Connect client to the non-blocking gdbserver task with short polling timeout
        client = socket.create_connection(("127.0.0.1", port), timeout=0.1)
        client.settimeout(0.1)

        # Drive COOS scheduler to accept the connection
        sysv.scheduler.step()

        # Send '?' halt reason query
        client.sendall(b"$?#3f")
        resp = _recv_rsp_frame(client, sysv)
        assert b"+" in resp
        assert b"$S05#b8" in resp

        # Send 'g' read registers (large payload spanning multiple yields / segments)
        client.sendall(b"+$g#67")
        resp = _recv_rsp_frame(client, sysv)
        assert b"+" in resp
        assert b"$" in resp
        assert b"#" in resp

        client.close()
    finally:
        sysv.shutdown()


def test_coop_01_wasm_coroutine_yields_on_quantum():
    """YIELD-01: Long-running WASM task yields every `yield_every` instructions, interleaving with other tasks."""
    wat = """
    (module
      (func $busy_loop (export "busy_loop") (param $x i32) (result i32)
        (block $b
          (loop $l
            (local.set $x (i32.add (local.get $x) (i32.const 1)))
            (br_if $l (i32.lt_s (local.get $x) (i32.const 100)))
          )
        )
        (local.get $x)
      )
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_coop_01")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # Step in quanta of 10 instructions
    call_state = interp.start(mod.export_func_index("busy_loop"), [0])
    step_count = 0
    while not call_state.finished:
        call_state = interp.step(call_state, quantum=10)
        step_count += 1
    result = call_state.results

    assert step_count >= 10, f"Expected multiple quantum steps, got {step_count}"
    assert result == [100]


def test_idle_01_jit_batch_compilation_on_idle():
    """IDLE-01: Compile queue is drained and compiled in LIFO order when scheduler fires idle_hook."""
    compiled_log = []

    def mock_compiler(pc: int) -> JITTrace:
        compiled_log.append(pc)
        return JITTrace(head_pc=pc, native_fn=lambda: pc, size_bytes=64)

    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(mock_compiler))
    engine.bitmap.touch(0x100)
    engine.bitmap.touch(0x100)  # HOT
    engine.bitmap.touch(0x200)
    engine.bitmap.touch(0x200)  # HOT
    engine.compile_queue = StaticVector.of(
        [0x100, 0x200], capacity=engine.compile_queue_capacity
    )  # Enqueued
    # COOS idle_hook fires with budget 2
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_log == [0x200, 0x100], "LIFO compilation order required"
    assert engine.bitmap.get_state(0x100) == CardState.COMPILED
    assert engine.bitmap.get_state(0x200) == CardState.COMPILED
    assert engine.cache.active.has_trace(0x100)
    assert engine.cache.active.has_trace(0x200)


def test_idle_02_logging_flush_on_idle():
    """IDLE-02: Deferred logs in RingBuffer are flushed to UART transport upon scheduler idle."""
    transport = UartTransport()
    dictionary = LogDictionary()
    dictionary.register(0x01, "event payload=%d")
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO)
    # Log events during active execution
    status1 = logger.log_event(LogLevel.INFO, 0x01, 42)
    status2 = logger.log_event(LogLevel.INFO, 0x01, 99)
    assert status1 == "QUEUED"
    assert status2 == "QUEUED"
    assert transport.bytes_written == 0, "No UART I/O allowed on hot path"
    # Scheduler reaches IDLE -> fires idle hook
    flushed = logger.flush()
    assert flushed == 2
    wire_output = transport.drain().decode("utf-8")
    assert "event payload=42" in wire_output
    assert "event payload=99" in wire_output


def test_tier_01_interpreter_to_jit_cooperative_flow():
    """TIER-01: End-to-end integration of cooperative WASM execution on COOS with idle JIT compilation and log flush."""
    sysv = System()
    sysv.dictionary.register(0x10, "wasm iteration=%d")
    executed_steps = []

    def wasm_task():
        # Emulate a WASM task executing in slices
        for i in range(5):
            sysv.runtime_engine.record_block_head(0x1000)
            sysv.logger.log_event(LogLevel.INFO, 0x10, i)
            executed_steps.append(f"task_step_{i}")
            yield  # Cooperative yield

    def monitor_task():
        for i in range(5):
            executed_steps.append(f"monitor_step_{i}")
            yield  # Cooperative yield

    sysv.scheduler.spawn("wasm_worker", wasm_task())
    sysv.scheduler.spawn("monitor", monitor_task())
    # Run COOS scheduler to completion
    sysv.scheduler.run_to_completion()
    # Verify interleaved cooperative execution
    assert "task_step_0" in executed_steps
    assert "monitor_step_0" in executed_steps
    # Verify deferred logs were flushed by idle_hook
    wire = sysv.transport.drain().decode("utf-8")
    assert "wasm iteration=0" in wire
    assert "wasm iteration=4" in wire


def test_tier_02_interpreter_to_jit_trace_transition():
    """TIER-02: Loop executes via Interpreter first -> promotes to HOT -> idle_hook compiles trace -> executes as JIT."""
    wat = """
    (module
      (func (export "fac") (param i32) (result i32)
        (local i32)
        i32.const 1
        local.set 1
        (loop $loop
          local.get 1
          local.get 0
          i32.mul
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
    engine = IntegratedHybridEngine(yield_threshold=3)
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[1].head_pc

    # Compute factorial(5) with 5 iterations: locals=[5, 0]
    ctx = WASMContext(locals_values=[5, 0])
    pc = engine.run_step(mod.blocks[0].head_pc, ctx)  # preamble: local[1] = 1, enters loop

    # Step 1: First iteration runs in Interpreter
    pc = engine.run_step(pc, ctx)
    assert engine.interp_blocks == 2
    assert engine.jit_traces == 0
    assert engine.bitmap.get_state(loop_pc) == CardState.EXECUTED
    # Step 2: Second iteration runs in Interpreter -> Card becomes HOT
    pc = engine.run_step(pc, ctx)
    assert engine.interp_blocks == 3
    assert engine.jit_traces == 0
    assert engine.bitmap.get_state(loop_pc) == CardState.HOT
    # Step 3: Third iteration triggers yield -> on_yield queues HOT card to compile_queue
    pc = engine.run_step(pc, ctx)
    assert loop_pc in engine.compile_queue
    # Simulate COOS scheduler idle_hook: batch compiles queued trace into Active cache
    compiled = engine.idle_hook()
    assert compiled == 1
    assert engine.bitmap.get_state(loop_pc) == CardState.COMPILED
    assert engine.cache.active.has_trace(loop_pc)
    # Step 4 & 5: Remaining iterations execute via fast native JIT trace!
    while pc is not None:
        pc = engine.run_step(pc, ctx)

    # Verification:
    # Result is 5! = 120
    assert ctx.locals[1] == 120
    # Verified that both Interpreter AND JIT traces executed in the same task run
    assert engine.interp_blocks >= 3
    assert engine.jit_traces >= 2
    assert engine.compilations == 1


def test_tier_03_trace_chaining_and_interpreter_fallback():
    """TIER-03: Traces chain directly into resident successors, and fall back to Interpreter when chain ends."""
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b1
          (block $b2
            local.get 0
            i32.const 10
            i32.add
            local.set 0
            br $b2
          )
          local.get 0
          i32.const 20
          i32.add
          local.set 0
          br $b1
        )
        local.get 0
        i32.const 30
        i32.add
        local.set 0
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = IntegratedHybridEngine(yield_threshold=10)
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]
    block_c = mod.blocks[2]
    # Compile block B first, then block A (so A can chain directly into resident B)
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
    # Assert trace A chained directly into trace B
    assert trace_a.chain_next == block_b.head_pc
    # Run execution:
    ctx = WASMContext(locals_values=[100])
    pc = block_a.head_pc
    # Step 1: Run block A (JIT) -> returns block B head via direct chain
    pc = engine.run_step(pc, ctx)
    assert pc == block_b.head_pc
    assert engine.jit_traces == 1
    assert ctx.locals[0] == 110
    # Step 2: Run block B (JIT) -> chain_next is None -> falls back to interpreter at block C
    pc = engine.run_step(pc, ctx)
    assert pc == block_c.head_pc
    assert engine.jit_traces == 2
    assert ctx.locals[0] == 130
    # Step 3: Run block C (Interpreter) -> completes execution smoothly!
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert engine.interp_blocks >= 1
    assert ctx.locals[0] == 160


# ===========================================================================
# Guest-Side WASI & Host-Call Execution (Interpreter & x64 JIT)
# ===========================================================================


def test_guest_wasi_01_interpreter_fd_write():
    """GUEST-WASI-01: WASM guest invoking wasi_snapshot_preview1.fd_write in Interpreter outputs to host UART."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
      (func (export "main") (result i32)
        (call $fd_write (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 32))
      )
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_01")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        # Set up guest memory:
        # offset 0: iov { buf: 16, len: 12 }
        # offset 16: "hello guest\n"
        msg = b"hello guest\n"
        struct.pack_into("<II", ctx.guest_memory, 0, 16, len(msg))
        ctx.guest_memory[16 : 16 + len(msg)] = msg
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        res = interp.call(mod.export_func_index("main"), [])
        assert res == [0], f"Expected WASI SUCCESS (0), got {res}"
        assert sysv.transport.drain() == msg
        nwritten = struct.unpack_from("<I", ctx.guest_memory, 32)[0]
        assert nwritten == len(msg)
    finally:
        sysv.shutdown()


def test_guest_wasi_02_interpreter_clock_and_random():
    """GUEST-WASI-02: WASM guest invoking clock_time_get and random_get stores valid data in guest memory."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "clock_time_get" (func $clock (param i32 i32 i32) (result i32)))
      (import "wasi_snapshot_preview1" "random_get" (func $rand (param i32 i32) (result i32)))
      (func (export "main") (result i32)
        (drop (call $clock (i32.const 0) (i32.const 0) (i32.const 16)))
        (call $rand (i32.const 32) (i32.const 16))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_02")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        res = interp.call(mod.export_func_index("main"), [])
        assert res == [0]
        t = struct.unpack_from("<Q", ctx.guest_memory, 16)[0]
        assert t > 0
        rand_data = bytes(ctx.guest_memory[32:48])
        assert len(rand_data) == 16
        assert rand_data != bytes(16)
    finally:
        sysv.shutdown()


def test_guest_wasi_03_interpreter_proc_exit():
    """GUEST-WASI-03: WASM guest invoking proc_exit(99) halts the host system with exit code."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
      (func (export "main")
        (call $exit (i32.const 99))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_03")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        assert sysv.halted is False
        interp.call(mod.export_func_index("main"), [])
        assert sysv.halted is True
        assert sysv.exit_code == 99
    finally:
        sysv.shutdown()


def test_debugger_manager_gdb_rsp_integration():
    """DBG-01..15: Verifies Debug Manager GDB RSP protocol, breakpoints, registers and JIT flush."""
    from debugger import DebuggerManager, GDBRspProtocol

    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(64)
    ctx = WASMContext(locals_values=[10, 20], memory=mem)
    # 1. Query stop signal
    res, _ = rsp.handle_packet("?", 0x100, ctx, {})
    assert res == "$S05#b8"
    # 2. Virtual registers read/write
    res_g, _ = rsp.handle_packet("g", 0x100, ctx, {})
    assert len(res_g[1 : res_g.index("#")]) == 160
    # 3. Memory write & JIT flush ({Debugger_Jit_Flush}) -- real WASM bytecode
    # (`i32.const 42`) run through the same extract_basic_blocks + compile_block
    # path production JIT compilation uses; the exact head_pc doesn't matter here,
    # only that inserting a trace and then flushing it round-trips.
    flush_code = bytes([I32_CONST, 42])
    fc_head_pc, fc_next_pc, fc_loops_to, fc_frame_depth, fc_byte_span = extract_basic_blocks(
        flush_code
    )[0]
    flush_block = BasicBlock(
        head_pc=fc_head_pc,
        next_pc=fc_next_pc,
        loops_to=fc_loops_to,
        frame_depth=fc_frame_depth,
        byte_span=fc_byte_span,
    )
    trace = engine.compiler.compile_block(flush_code, flush_block)
    engine.cache.insert(trace)
    assert engine.cache.active.has_trace(fc_head_pc)
    res_m, _ = rsp.handle_packet("M0,4:aabbccdd", 0x100, ctx, {})
    assert res_m.startswith("$OK#")
    assert bytes(mem[0:4]) == bytes.fromhex("aabbccdd")
    assert not engine.cache.active.has_trace(fc_head_pc)  # Flushed!
    # 4. Breakpoint & Stepping -- two real basic blocks split by a `block`/`end`,
    # loaded through a real Module so run_block_interpret's op-stream derivation
    # (from raw bytecode) has a function to decode against.
    step_wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 1
          i32.add
          local.set 0
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
    mod = engine.load_wasm(wat_to_wasm(step_wat))
    block1, block2 = mod.blocks[0], mod.blocks[1]
    blocks = {block1.head_pc: block1, block2.head_pc: block2}
    ctx.locals[0] = 10
    rsp.handle_packet(f"Z0,{block2.head_pc:x},0", block1.head_pc, ctx, blocks)
    res_c, stop_pc = rsp.handle_packet("c", block1.head_pc, ctx, blocks)
    assert res_c.startswith("$S05#")
    assert stop_pc == block2.head_pc
    assert ctx.locals[0] == 11
    # Remove BP and finish
    rsp.handle_packet(f"z0,{block2.head_pc:x},0", block2.head_pc, ctx, blocks)
    res_c2, _ = rsp.handle_packet("c", block2.head_pc, ctx, blocks)
    assert res_c2.startswith("$W00#")
    assert ctx.locals[0] == 22


def test_interpreter_debugger_handler_table_switch_and_hooks():
    """INTP-60..65: Verifies Interpreter DebuggerLabelTableSwitch, JIT bypass, PC sampling and assertions."""
    from debugger import DebuggerManager

    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 1
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
    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    mod = engine.load_wasm(wasm_bytes)
    block1 = mod.blocks[0]
    block2 = mod.blocks[1]
    # 1. Normal mode (INTP-60: zero overhead, normal handler table)
    assert engine.handler_table == "normal"
    assert engine.debugger is None
    ctx_normal = WASMContext(locals_values=[5])
    next_pc = engine.run_step(block1.head_pc, ctx_normal)
    assert next_pc == block2.head_pc
    assert ctx_normal.locals[0] == 6
    # 2. Attach debugger (INTP-61: switches to debug handler table)
    dbg.attach()
    assert engine.handler_table == "debug"
    assert engine.debugger is dbg
    # 3. Breakpoint hit (INTP-62: halts before execution)
    dbg.add_breakpoint(block2.head_pc)
    ctx_debug = WASMContext(locals_values=[10], memory=bytearray([0x55, 0xAA]))
    dbg.add_memory_assertion(0, 0x55, "valid magic")
    dbg.add_memory_assertion(1, 0x00, "invalid magic")  # Will fail
    # Step block1 (stops at block2 due to BP)
    next_pc = engine.run_step(block1.head_pc, ctx_debug)
    assert next_pc == block2.head_pc
    assert dbg.halted is True
    assert dbg.stop_signal == 5
    assert ctx_debug.locals[0] == 11
    # 4. Profiler & Assertions (INTP-63, INTP-64)
    assert dbg.pc_sample_counts[block1.head_pc] == 1
    assert len(dbg.assertion_violations) == 1
    # 5. JIT Bypass under debug mode (INTP-65: JIT trace exists but interpreter debug table runs)
    trace = engine.compiler.compile_trace(
        block1.head_pc, engine.resolve_trace_block(block1.head_pc)
    )
    engine.cache.insert(trace)
    assert engine.cache.active.has_trace(block1.head_pc)
    # Run step at block1 under debug mode -> interp_blocks increments, NOT jit_traces
    interp_before = engine.interp_blocks
    jit_before = engine.jit_traces
    engine.run_step(block1.head_pc, ctx_debug)
    assert engine.interp_blocks == interp_before + 1
    assert engine.jit_traces == jit_before  # JIT bypassed!
    # Detach
    dbg.detach()
    assert engine.handler_table == "normal"


def test_wasm_loader_and_radix_binary_tree_view_indexes():
    """LOAD-01..47: Verifies WASM Loader zero-copy indexing, verification, and RadixBinaryTreeView file offset & hash symbol indexes."""
    from loader import WasmLoader, WasmVerifyError
    from test_loader import _build_test_wasm_binary

    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("test_module", wasm_bytes)
    # 1. Zero-copy & Hash + RadixBinaryTreeView export lookup (LOAD-13)
    assert [e.name for e in view.exports_dict] == ["alpha", "beta", "zeta"]
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("unknown") is None
    # 2. Transactional rollback on invalid WASM
    watermark = loader.allocator.offset
    try:
        loader.prepare("bad", _build_test_wasm_binary(magic=b"\x7fELF"))
        assert False
    except WasmVerifyError:
        pass
    assert loader.allocator.offset == watermark
    # 3. RadixBinaryTreeView file offset reverse-lookup (LOAD-40..44)
    assert len(view.entity_registry) > 0
    func_start, func_size = view.code_offsets[0]
    entity_fn = view.lookup_by_file_offset(func_start)
    assert entity_fn is not None
    assert entity_fn.kind == "FUNCTION"
    assert entity_fn.name_or_idx == 0
    # Middle of function
    entity_fn_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_fn_mid is not None
    assert entity_fn_mid.kind == "FUNCTION"
    # Global lookup
    glob_entry = view.globals[0]
    entity_glob = view.lookup_by_file_offset(glob_entry.init_expr_offset)
    assert entity_glob is not None
    assert entity_glob.kind == "GLOBAL"
    # Out-of-bounds offset
    assert view.lookup_by_file_offset(len(wasm_bytes) + 1000) is None


# ===========================================================================
# Test Runner
# ===========================================================================

ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} comprehensive pysim invariant tests passed.")
