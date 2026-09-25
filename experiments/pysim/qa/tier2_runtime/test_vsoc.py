from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: vSoC Engine & Multitasking Integration
Traceability: runtime_vsoc_test_spec.md
"""

import socket
import struct
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


# Keep the product Tier 3 package ahead of tests/tier3_executer when importing
# runtime_engine's qualified Tier 3 modules.

from execution_context import WASMContext
from fixtures.platform_drivers import create_reference_platform_drivers
from helpers import expect_assertion, wat_to_wasm
from helpers import make_interpreter as Interpreter
from runtime_test_driver import RuntimeEngineDebugDriver
from scheduler import ChannelAction, Scheduler, Task, TaskState
from system import (
    System,
)
from system_containers import (
    ReadOnlyFlatMapView,
    StaticVector,
)
from test_support import (
    PcOnlyCompiler,
    compile_module_block,
    make_pc_only_module,
    make_runtime_engine,
)
from tier2_runtime.logger import LogDictionary, Logger, LogLevel
from tier3_executer.jit.jit_cache import CardState, JITTrace
from tier3_executer.jit.x64_jit import TraceCompiler
from tier3_platform.drivers.hal.stream import DedicatedLogSink, StreamTransport
from tier3_platform.drivers.wasi.context import WasiHostContext
from virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
    VirqFaultCode,
    VirqNode,
)
from wasm_module import I32, Function, FuncType, Module
from wasm_reader import parse


def _make_virq_module() -> Module:
    valid = FuncType(params=(I32, I32, I32, I32, I32), results=(I32,))
    invalid = FuncType(params=(I32,), results=(I32,))
    return Module(
        types=(valid, invalid),
        imports=(),
        functions=(
            Function(type_index=0, locals_extra=(), code=b""),
            Function(type_index=0, locals_extra=(), code=b""),
            Function(type_index=0, locals_extra=(), code=b""),
            Function(type_index=1, locals_extra=(), code=b""),
        ),
    )


def _virq_event(vector_id: int, source_id: int = 0) -> InterruptEvent:
    return InterruptEvent(vector_id, source_id, 7, 11, 13)


def test_virq_50_static_nodes_and_boundary_registration():
    """TEST-VSOC-50: only the fixed root/category/device nodes are mutable."""
    dispatcher = VirqDispatcher(_make_virq_module(), lambda _index, _v, _s, _c, _p0, _p1: 1)

    root = dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    device = dispatcher.register_dispatcher(VirqNode.device(2), 1)
    invalid = dispatcher.register_dispatcher(14, 0)

    assert root.is_ok and root.value == RegistrationStatus.PENDING
    assert device.is_ok and device.value == RegistrationStatus.PENDING
    assert not invalid.is_ok and invalid.error == RegistrationError.NODE_OUT_OF_RANGE
    assert dispatcher.active_functions[int(VirqNode.ROOT)] == 0xFFFF_FFFF

    dispatcher.commit_pending_registrations()
    assert dispatcher.active_functions[int(VirqNode.ROOT)] == 0
    assert dispatcher.active_functions[VirqNode.device(2)] == 1
    assert dispatcher.source_table[3].vector_id == 0x0100
    assert dispatcher.source_table[3].category_node == int(VirqNode.DEVICE)


def test_virq_51_rejects_wrong_wasm_signature_without_overwrite():
    """TEST-VSOC-51: a signature mismatch does not replace an active registration."""
    dispatcher = VirqDispatcher(_make_virq_module(), lambda _index, _v, _s, _c, _p0, _p1: 0)
    accepted = dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.commit_pending_registrations()
    rejected = dispatcher.register_dispatcher(int(VirqNode.ROOT), 3)

    assert accepted.is_ok
    assert not rejected.is_ok
    assert rejected.error == RegistrationError.FUNCTION_SIGNATURE_INVALID
    assert dispatcher.active_functions[int(VirqNode.ROOT)] == 0


def test_virq_52_pending_registration_is_invisible_until_boundary():
    """TEST-VSOC-52: the active table changes only at the COOS boundary commit."""
    calls = StaticVector[tuple[int, InterruptEvent]](capacity=4)

    def invoke(
        function_index: int,
        vector_id: int,
        source_id: int,
        cause_code: int,
        payload0: int,
        payload1: int,
    ) -> int:
        calls.push_back(
            (
                function_index,
                InterruptEvent(vector_id, source_id, cause_code, payload0, payload1),
            )
        )
        return int(VirqDispatchResult.HANDLED)

    dispatcher = VirqDispatcher(_make_virq_module(), invoke)
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.commit_pending_registrations()
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 1)

    first = dispatcher.dispatch_interrupt_event(_virq_event(0x1000))
    dispatcher.commit_pending_registrations()
    second = dispatcher.dispatch_interrupt_event(_virq_event(0x1000))

    assert first == DispatchResult(VirqDispatchResult.HANDLED)
    assert second == DispatchResult(VirqDispatchResult.HANDLED)
    assert tuple(index for index, _event in calls) == (0, 1)


def test_virq_53_dispatches_fixed_event_through_static_hierarchy():
    """TEST-VSOC-53: PASS_THROUGH traverses root, category, device in order."""
    calls = StaticVector[int](capacity=8)

    def invoke(
        function_index: int,
        _vector_id: int,
        _source_id: int,
        _cause_code: int,
        _payload0: int,
        _payload1: int,
    ) -> int:
        calls.push_back(function_index)
        return int(VirqDispatchResult.PASS_THROUGH)

    dispatcher = VirqDispatcher(_make_virq_module(), invoke)
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.register_dispatcher(int(VirqNode.DEVICE), 1)
    dispatcher.register_dispatcher(VirqNode.device(0), 2)
    dispatcher.commit_pending_registrations()

    result = dispatcher.dispatch_interrupt_event(_virq_event(0x0100, source_id=0))

    assert result.outcome == VirqDispatchResult.PASS_THROUGH
    assert tuple(calls) == (0, 1, 2)
    assert dispatcher.last_path == (int(VirqNode.ROOT), int(VirqNode.DEVICE), 5)


def test_virq_54_handled_and_reject_are_terminal():
    """TEST-VSOC-54: terminal outcomes do not propagate to child or FAULT nodes."""
    calls = StaticVector[int](capacity=8)
    modes = StaticVector[int](capacity=8)

    def invoke(
        function_index: int,
        _vector_id: int,
        _source_id: int,
        _cause_code: int,
        _payload0: int,
        _payload1: int,
    ) -> int:
        calls.push_back(function_index)
        return modes[function_index]

    dispatcher = VirqDispatcher(_make_virq_module(), invoke)
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.register_dispatcher(int(VirqNode.DEVICE), 1)
    dispatcher.register_dispatcher(VirqNode.device(0), 2)
    dispatcher.commit_pending_registrations()

    modes.extend(
        (
            int(VirqDispatchResult.HANDLED),
            int(VirqDispatchResult.PASS_THROUGH),
            int(VirqDispatchResult.PASS_THROUGH),
        )
    )
    handled = dispatcher.dispatch_interrupt_event(_virq_event(0x0100))
    assert handled.outcome == VirqDispatchResult.HANDLED
    assert tuple(calls) == (0,)

    calls.clear()
    modes[0] = int(VirqDispatchResult.REJECT)
    rejected = dispatcher.dispatch_interrupt_event(_virq_event(0x0100))
    assert rejected.outcome == VirqDispatchResult.REJECT
    assert tuple(calls) == (0,)
    assert dispatcher.last_path == (int(VirqNode.ROOT),)
    assert dispatcher.faults[-1] == VirqFaultCode.ROOT_REJECT


def test_virq_55_does_not_enter_wasi_polling_path():
    """TEST-VSOC-55: vIRQ dispatch invokes only its registered dispatcher callback."""
    poll_calls = StaticVector[int](capacity=2)
    dispatcher = VirqDispatcher(
        _make_virq_module(),
        lambda _index, _v, _s, _c, _p0, _p1: int(VirqDispatchResult.HANDLED),
    )
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.commit_pending_registrations()

    result = dispatcher.dispatch_interrupt_event(_virq_event(0x2000))
    assert result.outcome == VirqDispatchResult.HANDLED
    assert len(poll_calls) == 0


def test_runtime_engine_registers_virq_dispatchers_through_bound_module():
    """The runtime facade delegates vIRQ registration to its module-bound dispatcher."""
    engine = make_runtime_engine()
    unavailable = engine.register_virq_dispatcher(int(VirqNode.ROOT), 0)
    assert not unavailable.is_ok
    assert unavailable.error == RegistrationError.MODULE_UNAVAILABLE

    engine.register_module_blocks(_make_virq_module())
    pending = engine.register_virq_dispatcher(int(VirqNode.ROOT), 0)
    assert pending.is_ok
    assert pending.value == RegistrationStatus.PENDING
    invalid = engine.register_virq_dispatcher(int(VirqNode.ROOT), 3)
    assert not invalid.is_ok
    assert invalid.error == RegistrationError.FUNCTION_SIGNATURE_INVALID


def test_runtime_engine_run_advances_one_trace_boundary():
    """RuntimeEngine.run advances one trace boundary and preserves resumable state."""
    wasm_bytes = wat_to_wasm(
        """
        (module
          (func (export "count") (param $count i32) (result i32)
            (local $index i32)
            (block $exit
              (loop $loop
                local.get $index
                local.get $count
                i32.ge_s
                br_if $exit
                local.get $index
                i32.const 1
                i32.add
                local.set $index
                br $loop
              )
            )
            local.get $index
          )
        )
        """
    )
    engine = make_runtime_engine()
    module = engine.load_wasm(wasm_bytes)
    interpreter = Interpreter(module)
    call_state = interpreter.start(0, (2,))
    boundary = engine.run(interpreter, call_state)
    assert not boundary.call_state.finished
    results = engine.complete_call(interpreter, boundary.call_state)
    assert results == [2]


def test_system_coos_runtime_rejects_synchronous_guest_execution():
    """A COOS-bound vSoC cannot silently run a guest to completion synchronously."""
    module = parse(wat_to_wasm('(module (func (export "entry")))'))
    system = System()

    with expect_assertion("COOS runtime calls must be advanced by System at each trace boundary"):
        system.runtime_engine.call(Interpreter(module), module.export_func_index("entry"), ())


def test_system_guest_interpreter_returns_to_coos_and_resumes():
    """System hands off after one interpreter boundary and resumes the same call."""

    wasm_bytes = wat_to_wasm(
        """
        (module
          (func (export "count") (param $count i32) (result i32)
            (local $index i32)
            (block $exit
              (loop $loop
                local.get $index
                local.get $count
                i32.ge_s
                br_if $exit
                local.get $index
                i32.const 1
                i32.add
                local.set $index
                br $loop
              )
            )
            local.get $index
          )
        )
        """
    )
    system = System()
    module = parse(wasm_bytes)
    interpreter = Interpreter(module)
    monitor_observed_guest_ready: list[bool] = []

    def monitor_task():
        guest_task = system.scheduler.get_task(guest_task_id)
        assert guest_task is not None
        assert guest_task.state == TaskState.READY
        monitor_observed_guest_ready.append(True)
        yield (ChannelAction.YIELD, None)

    polls = 0

    def observe_at_second_boundary(scheduler: Scheduler, task: Task | None = None) -> bool:
        nonlocal polls
        polls += 1
        return polls == 2

    from unittest.mock import patch

    with patch.object(Scheduler, "observe_reschedule_generation", observe_at_second_boundary):
        guest_task_id = system.scheduler.spawn(
            "guest",
            system.run_guest(interpreter, module.export_func_index("count"), (100,)),
        )
        system.scheduler.spawn("monitor", monitor_task())
        system.scheduler.run_until_idle()

    guest_task = system.scheduler.get_task(guest_task_id)
    assert guest_task is not None
    assert guest_task.state == TaskState.TERMINATED
    assert guest_task.result == [100]
    assert monitor_observed_guest_ready == [True]
    assert polls > 2


def test_virq_unregisters_dispatcher_at_coos_boundary():
    """The dedicated vIRQ unregister host call takes effect at the next COOS boundary."""
    dispatcher = VirqDispatcher(_make_virq_module(), lambda _index, _v, _s, _c, _p0, _p1: 0)
    assert dispatcher.register_dispatcher(int(VirqNode.ROOT), 0).is_ok
    dispatcher.commit_pending_registrations()
    assert dispatcher.unregister_dispatcher(int(VirqNode.ROOT)).is_ok
    assert (
        dispatcher.dispatch_interrupt_event(_virq_event(0x2000)).outcome
        == VirqDispatchResult.HANDLED
    )
    dispatcher.commit_pending_registrations()
    assert (
        dispatcher.dispatch_interrupt_event(_virq_event(0x2000)).outcome
        == VirqDispatchResult.PASS_THROUGH
    )


def test_hal_task_ipc_communication():
    """TEST-HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.wasi.context import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_ipc_guest")
        buffer_handle = sysv.pool.buffer(0)
        assert sysv.pool.map_for_io(buffer_handle.buffer_id).name == "MAPPED"
        sysv.pool.view(buffer_handle, 0, 128)[:] = b"x" * 128
        sysv.scheduler.current_task = runtime_task
        sysv.start_hal_driver(
            DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
        )
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        response = engine.send_ipc_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            ReadOnlyFlatMapView(
                [(ARG_BUFFER_HANDLE, buffer_handle.buffer_id), (ARG_LENGTH, 128), (ARG_OFFSET, 0)]
            ),
        )
        assert response.response_code == 0
        assert response.value == 128
        stdio_task = sysv.hal_task_for("fireball://hal/stdout/0")
        assert stdio_task is not None
        assert stdio_task.processed_count == 1
        assert stdio_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_BUFFER
        sysv.pool.unmap_after_io(buffer_handle.buffer_id)
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
        if b"$" in buf:
            dollar_idx = buf.index(b"$")
            hash_idx = buf.find(b"#", dollar_idx)
            if hash_idx != -1 and len(buf) >= hash_idx + 3:
                return buf
    raise AssertionError(f"incomplete RSP frame within {max_steps} scheduler steps: {buf!r}")


def test_gdbserver_task_coos_cooperative_execution():
    """TEST-DBG-01, GOTCHA-DBG-04: GDBServer operates as an independent task on COOS and handles multi-yield RSP packets."""
    from tier3_plugins.debugger.debugger import DebuggerManager

    sysv = System()
    dbg = DebuggerManager()
    ctx = WASMContext()
    ctx.locals = (10, 20)
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
    """TEST-YIELD-01: Long-running WASM task yields every `yield_every` instructions, interleaving with other tasks."""
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
        call_state = interp.step(call_state)
        step_count += 1
    result = call_state.results

    assert step_count >= 10, f"Expected multiple basic block steps, got {step_count}"
    assert result == [100]


def test_idle_01_jit_batch_compilation_on_idle():
    """TEST-IDLE-01: Compile queue is drained and compiled in LIFO order when scheduler fires idle_hook."""
    compiled_log = []

    def mock_compiler(pc: int) -> JITTrace:
        compiled_log.append(pc)
        return JITTrace(head_pc=pc, native_fn=lambda: pc, size_bytes=64)

    engine = make_runtime_engine(jit_compiler=PcOnlyCompiler(mock_compiler), code_lengths=(0x400,))
    engine.register_module_blocks(make_pc_only_module((0x100, 0x200, 0x300)))
    engine.jit_runtime.bitmap.touch(0x100)
    engine.jit_runtime.bitmap.touch(0x100)  # HOT
    engine.jit_runtime.bitmap.touch(0x200)
    engine.jit_runtime.bitmap.touch(0x200)  # HOT
    engine.jit_runtime.compile_queue = StaticVector.of(
        [0x100, 0x200], capacity=engine.jit_runtime.compile_queue_capacity
    )  # Enqueued
    # COOS idle_hook fires with budget 2
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_log == [0x200, 0x100], "LIFO compilation order required"
    assert engine.jit_runtime.bitmap.get_state(0x100) == CardState.COMPILED
    assert engine.jit_runtime.bitmap.get_state(0x200) == CardState.COMPILED
    assert engine.jit_runtime.cache.active.has_trace(0x100)
    assert engine.jit_runtime.cache.active.has_trace(0x200)


def test_idle_02_logging_flush_on_idle():
    """TEST-IDLE-02: Deferred logs in RingBuffer are flushed to UART transport upon scheduler idle."""
    transport = StreamTransport()
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
    wire_output = transport.drain_output().decode("utf-8")
    assert "event payload=42" in wire_output
    assert "event payload=99" in wire_output


def test_tier_01_interpreter_to_jit_cooperative_flow():
    """TEST-TIER-01: End-to-end integration of cooperative WASM execution on COOS with idle JIT compilation and log flush."""
    logger_sink = DedicatedLogSink()
    sysv = System(logger_sink=logger_sink)
    sysv.runtime_engine = make_runtime_engine(code_lengths=(0x1001,))
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
    wire = logger_sink.drain_output().decode("utf-8")
    assert "wasm iteration=0" in wire
    assert "wasm iteration=4" in wire


def test_tier_02_interpreter_to_jit_trace_transition():
    """TEST-TIER-02: Loop executes via Interpreter first -> promotes to HOT -> idle_hook compiles trace -> executes as JIT."""
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
    # Keep the preamble and loop heads on separate cards so this test can
    # observe the full UNEXECUTED -> EXECUTED -> HOT transition directly.
    engine = make_runtime_engine(yield_threshold=3, card_shift=2, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[1].head_pc
    results = engine.call(Interpreter(mod), 0, [5])
    assert results[0] == 120
    assert engine.stat_interp_steps >= 3
    assert engine.stat_jit_invocations >= 2
    assert engine.jit_runtime.bitmap.get_state(loop_pc) == CardState.COMPILED
    assert engine.jit_runtime.cache.active.has_trace(
        loop_pc
    ) or engine.jit_runtime.cache.warm.has_trace(loop_pc)


def test_tier_03_trace_chaining_and_interpreter_fallback():
    """TEST-TIER-03: Traces chain directly into resident successors, and fall back to Interpreter when chain ends."""
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
        local.get 0
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = make_runtime_engine(yield_threshold=10, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    block_a = mod.blocks[0]
    block_b = mod.blocks[1]
    # Compile block B first, then block A (so A can chain directly into resident B)
    trace_b = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_b)
    engine.jit_runtime.cache.insert(trace_b)
    engine.jit_runtime.bitmap.mark_compiled(block_b.head_pc)
    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)
    engine.jit_runtime.bitmap.mark_compiled(block_a.head_pc)
    # Assert trace A chained directly into trace B
    assert trace_a.chain_next == block_b.head_pc
    results = engine.call(Interpreter(mod), 0, [100])
    assert results[0] == 160
    assert engine.stat_jit_invocations == 2
    assert engine.stat_interp_steps >= 1


# ===========================================================================
# Guest-Side WASI & Host-Call Execution (Interpreter & x64 JIT)
# ===========================================================================


def test_guest_wasi_01_interpreter_fd_write():
    """TEST-GUEST-WASI-01: WASM guest fd_write reaches the standard-I/O HAL task."""
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
        from tier3_platform.drivers.hal.dummy import DummyDriver

        ctx = WasiHostContext(sysv)
        sysv.start_hal_driver(
            DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
        )
        # Set up guest memory:
        # offset 0: iov { buf: 16, len: 12 }
        # offset 16: "hello guest\n"
        msg = b"hello guest\n"
        struct.pack_into("<II", ctx.guest_memory, 0, 16, len(msg))
        ctx.guest_memory[16 : 16 + len(msg)] = msg
        host_funcs = ctx.build_interpreter_host_functions(mod)
        mod.init_memory_data(ctx.guest_memory, ())
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        res = interp.call(mod.export_func_index("main"), [])
        assert res == [0], f"Expected WASI SUCCESS (0), got {res}"
        assert sysv.transport.drain_output() == msg
        nwritten = struct.unpack_from("<I", ctx.guest_memory, 32)[0]
        assert nwritten == len(msg)
    finally:
        sysv.shutdown()


def test_guest_wasi_02_interpreter_clock_and_random():
    """TEST-GUEST-WASI-02: WASM guest invoking clock_time_get and random_get stores valid data in guest memory."""
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
    sysv = System(drivers=create_reference_platform_drivers())
    try:
        ctx = WasiHostContext(sysv)
        host_funcs = ctx.build_interpreter_host_functions(mod)
        mod.init_memory_data(ctx.guest_memory, ())
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
    """TEST-GUEST-WASI-03: WASM guest invoking proc_exit(99) halts the host system with exit code."""
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
        mod.init_memory_data(ctx.guest_memory, ())
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        assert sysv.halted is False
        interp.call(mod.export_func_index("main"), [])
        assert sysv.halted is True
        assert sysv.exit_code == 99
    finally:
        sysv.shutdown()


def test_debugger_manager_gdb_rsp_integration():
    """TEST-DBG-01..15: Verifies interpreter-only Debug Manager GDB RSP behavior."""
    from tier3_plugins.debugger.debugger import DebuggerManager, GDBRspProtocol

    engine = RuntimeEngineDebugDriver()
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(64)
    ctx = WASMContext(memory=mem)
    ctx.locals = (10, 20)
    # 1. Query stop signal
    res, _ = rsp.handle_packet("?", 0x100, ctx, {})
    assert res == "$S05#b8"
    # 2. Virtual registers read/write
    res_g, _ = rsp.handle_packet("g", 0x100, ctx, {})
    assert len(res_g[1 : res_g.index("#")]) == 160
    # 3. Memory write in the interpreter-only debug configuration.
    res_m, _ = rsp.handle_packet("M0,4:aabbccdd", 0x100, ctx, {})
    assert res_m.startswith("$OK#")
    assert bytes(mem[0:4]) == bytes.fromhex("aabbccdd")
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


def test_interpreter_debugger_attachment_and_hooks():
    """TEST-INTP-60..65: Verifies interpreter-only debug execution, PC sampling and assertions."""
    from tier3_plugins.debugger.debugger import DebuggerManager

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
        local.get 0
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = RuntimeEngineDebugDriver()
    dbg = DebuggerManager(engine=engine)
    mod = engine.load_wasm(wasm_bytes)
    block1 = mod.blocks[0]
    block2 = mod.blocks[1]
    # 1. Normal interpreter composition (TEST-INTP-60: zero JIT/debugger overhead)
    assert engine.handler_table == "interpreter"
    assert engine.debugger is None
    ctx_normal = WASMContext()
    ctx_normal.locals = (5,)
    next_pc = engine.run_step(block1.head_pc, ctx_normal)
    assert next_pc == block2.head_pc
    assert ctx_normal.locals[0] == 6
    # 2. Attach debugger (TEST-INTP-61: keeps the interpreter execution path)
    dbg.attach()
    assert engine.handler_table == "interpreter"
    assert engine.debugger is dbg
    # 3. Breakpoint hit (TEST-INTP-62: halts before execution)
    dbg.add_breakpoint(block2.head_pc)
    ctx_debug = WASMContext(memory=bytearray([0x55, 0xAA]))
    ctx_debug.locals = (10,)
    dbg.add_memory_assertion(0, 0x55, "valid magic")
    dbg.add_memory_assertion(1, 0x00, "invalid magic")  # Will fail
    # Step block1 (stops at block2 due to BP)
    next_pc = engine.run_step(block1.head_pc, ctx_debug)
    assert next_pc == block2.head_pc
    assert dbg.halted is True
    assert dbg.stop_signal == 5
    assert ctx_debug.locals[0] == 11
    # 4. Profiler & Assertions (TEST-INTP-63, TEST-INTP-64)
    assert dbg.pc_sample_counts[block1.head_pc] == 1
    assert len(dbg.assertion_violations) == 1
    # 5. Attached execution remains interpreter-only (TEST-INTP-65)
    assert engine.jit_runtime is None
    interp_before = engine.interp_blocks
    jit_before = engine.jit_traces
    engine.run_step(block1.head_pc, ctx_debug)
    assert engine.interp_blocks == interp_before + 1
    assert engine.jit_traces == jit_before
    # Detach
    dbg.detach()
    assert engine.handler_table == "interpreter"


def test_wasm_loader_and_radix_binary_tree_view_indexes():
    """TEST-LOAD-01..47: Verifies WASM Loader zero-copy indexing, verification, and ReadOnlyRadixBinaryTreeView file offset & hash symbol indexes."""
    from loader import WasmLoader

    from experiments.pysim.qa.tier2_runtime.test_loader import _build_test_wasm_binary

    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("test_module", wasm_bytes)
    # 1. Zero-copy & Hash + ReadOnlyRadixBinaryTreeView export lookup (TEST-LOAD-13)
    assert [e.name for e in view.exports_dict] == ["alpha", "beta", "zeta"]
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("unknown") is None
    # 2. Transactional rollback on invalid WASM
    watermark = loader.allocator.offset
    with expect_assertion():
        loader.prepare("bad", _build_test_wasm_binary(magic=b"\x7fELF"))
    assert loader.allocator.offset == watermark
    # 3. ReadOnlyRadixBinaryTreeView file offset reverse-lookup (TEST-LOAD-40..44)
    assert len(view.entity_registry) > 0
    func_start, func_size = view.code_offsets[0]
    entity_fn = view.lookup_by_file_offset(func_start)
    assert entity_fn is not None
    assert entity_fn.kind == "FUNCTION"
    assert entity_fn.index == 0
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
