from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: vSoC Engine & Multitasking Integration
Traceability: runtime_vsoc_test_spec.md
"""

import socket
import struct
from pathlib import Path

import pytest

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


# Keep the product Tier 3 package ahead of tests/tier3_plugins when importing
# runtime_engine's qualified Tier 3 modules.

from ipc_router import FB_URI_HAL_STDOUT, Role
from qa.private.debugger_support import DebugTestView, make_debug_execution
from qa.private.runtime_test_driver import RuntimeEngineDebugDriver
from qa.shared.fixtures.platform_drivers import create_reference_platform_drivers
from qa.shared.helpers import (
    expect_assertion,
    make_native_interpreter,
    wat_to_wasm,
)
from qa.shared.helpers import make_interpreter as Interpreter
from qa.shared.jit_cache import CardState
from qa.shared.runtime_support import (
    compile_module_block,
    make_runtime_engine,
)
from qa.shared.x64_jit import TraceCompiler
from scheduler import ChannelAction, TaskState
from system import (
    System,
)
from system_containers import (
    ReadOnlyFlatMapView,
    StaticVector,
)
from tier2_runtime.observability.logger import (
    LogDictionary,
    LogLevel,
    LogResult,
)
from tier2_runtime.runtime.engine import RuntimeDriveMode, RuntimeEngine
from tier2_runtime.vsoc.virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
    VirqFaultCode,
    VirqNode,
)
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse
from tier3_platform.drivers.printk import PrintkBuffer
from tier3_platform.drivers.wasi.context import WasiHostContext


def _make_virq_module() -> Module:
    return parse(
        memoryview(
            wat_to_wasm(
                "(module "
                "(func (param i32 i32 i32 i32 i32) (result i32) i32.const 0) "
                "(func (param i32 i32 i32 i32 i32) (result i32) i32.const 0) "
                "(func (param i32 i32 i32 i32 i32) (result i32) i32.const 0) "
                "(func (param i32) (result i32) i32.const 0))"
            )
        )
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
    calls = StaticVector[tuple[int, InterruptEvent]](capacity=8)

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
                InterruptEvent(
                    vector_id,
                    source_id,
                    cause_code,
                    payload0,
                    payload1,
                ),
            )
        )
        return int(VirqDispatchResult.PASS_THROUGH)

    dispatcher = VirqDispatcher(_make_virq_module(), invoke)
    dispatcher.register_dispatcher(int(VirqNode.ROOT), 0)
    dispatcher.register_dispatcher(int(VirqNode.DEVICE), 1)
    dispatcher.register_dispatcher(VirqNode.device(0), 2)
    dispatcher.commit_pending_registrations()

    result = dispatcher.dispatch_interrupt_event(_virq_event(0x0100, source_id=0))

    assert result.outcome == VirqDispatchResult.PASS_THROUGH
    assert tuple(calls) == tuple((index, _virq_event(0x0100, 0)) for index in (0, 1, 2))
    assert dispatcher.last_path == (int(VirqNode.ROOT), int(VirqNode.DEVICE), 5)


@pytest.mark.parametrize("terminal_level", (0, 1, 2), ids=("root", "category", "device"))
@pytest.mark.parametrize("outcome", (VirqDispatchResult.HANDLED, VirqDispatchResult.REJECT))
def test_virq_54_handled_and_reject_are_terminal(
    terminal_level: int, outcome: VirqDispatchResult
) -> None:
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

    modes.extend((1, 1, 1))
    modes[terminal_level] = int(outcome)
    result = dispatcher.dispatch_interrupt_event(_virq_event(0x0100))
    expected_calls = ((0,), (0, 1), (0, 1, 2))[terminal_level]
    expected_path = ((0,), (0, 1), (0, 1, 5))[terminal_level]
    expected_fault = (
        (
            VirqFaultCode.ROOT_REJECT,
            VirqFaultCode.CATEGORY_REJECT,
            VirqFaultCode.DEVICE_REJECT,
        )[terminal_level]
        if outcome == VirqDispatchResult.REJECT
        else None
    )
    assert result.outcome == outcome
    assert result.error == expected_fault
    assert tuple(calls) == expected_calls
    assert dispatcher.last_path == expected_path
    assert tuple(dispatcher.faults) == (() if expected_fault is None else (expected_fault,))


def test_virq_55_does_not_enter_wasi_polling_path():
    """TEST-VSOC-55: real HAL poll and native vIRQ leave each other's state unchanged."""
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.wasi.context import Wasi03pEngine

    system = System()
    try:
        system.start_runtime_task(name="virq_poll_guest")
        timer_uri = "fireball://hal/timer/0"
        system.start_hal_driver(DummyDriver(system.pool, stream_enabled=False), timer_uri)
        polling = Wasi03pEngine(system)
        handle = polling.clock_subscribe(timer_uri, 0)
        module = system.runtime_engine.load_wasm(
            wat_to_wasm(
                """(module (memory 1)
              (func (param i32 i32 i32 i32 i32) (result i32)
                i32.const 0 local.get 0 i32.store
                i32.const 4 local.get 1 i32.store
                i32.const 8 local.get 2 i32.store
                i32.const 12 local.get 3 i32.store
                i32.const 16 local.get 4 i32.store
                i32.const 0))"""
            )
        )
        memory = bytearray(65536)
        interpreter = make_native_interpreter(module, memory=memory)
        state = interpreter.start(0, (0, 0, 0, 0, 0))
        while not state.finished:
            state = system.runtime_engine.run(interpreter, state).call_state
        assert state.results == [0]
        assert system.host_calls.virq_register(int(VirqNode.ROOT), 0) == 0
        system.runtime_engine.commit_virq_registrations()
        dispatcher = system.runtime_engine._virq
        assert dispatcher is not None
        active = dispatcher.active_functions
        pending = tuple(dispatcher._pending_functions)
        assert polling.poll_check(timer_uri, handle) is True
        assert dispatcher.active_functions == active
        assert tuple(dispatcher._pending_functions) == pending
        hal_task = system.hal_task_for(timer_uri)
        assert hal_task is not None
        processed = hal_task.processed_count
        event = _virq_event(0x0100, 0)
        result = system.runtime_engine.dispatch_interrupt_event(event)
        assert result.outcome == VirqDispatchResult.HANDLED
        assert struct.unpack_from("<5I", memory) == (0x0100, 0, 7, 11, 13)
        assert hal_task.processed_count == processed
        before = bytes(memory)
        assert polling.poll_check(timer_uri, handle) is True
        assert hal_task.processed_count == processed + 1
        assert bytes(memory) == before
        assert dispatcher.active_functions == active
        assert tuple(dispatcher._pending_functions) == pending
    finally:
        system.shutdown()


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
    interpreter = make_native_interpreter(module)
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
        system.runtime_engine.call(
            make_native_interpreter(module), module.export_func_index("entry"), ()
        )


def _counter_module() -> Module:
    return parse(
        wat_to_wasm("""(module (memory 1)
      (func (export "count") (param $limit i32) (result i32) (local $index i32)
        (loop $loop
          local.get $index i32.const 1 i32.add local.set $index
          i32.const 0 local.get $index i32.store
          local.get $index local.get $limit i32.lt_u br_if $loop)
        local.get $index))""")
    )


def test_system_guest_interpreter_returns_to_coos_and_resumes():
    """TEST-VSOC-10: native LOOP thresholds return to COOS and resume one call."""
    system = System()
    system.runtime_engine = RuntimeEngine(yield_threshold=3, drive_mode=RuntimeDriveMode.COOS)
    memory = bytearray(65536)
    module = _counter_module()
    interpreter = make_native_interpreter(
        module, memory=memory, bump_allocator=system.runtime_engine.bump_allocator
    )
    observed: list[tuple[int, TaskState]] = []

    def monitor_task():
        for _ in range(3):
            guest = system.scheduler.get_task(guest_task_id)
            assert guest is not None
            observed.append((struct.unpack_from("<I", memory)[0], guest.state))
            yield (ChannelAction.YIELD, None)

    try:
        guest_task_id = system.scheduler.spawn(
            "guest",
            system.run_guest(interpreter, 0, (10,)),
            role=Role.RUNTIME,
        )
        system.scheduler.spawn("monitor", monitor_task())
        system.scheduler.run_until_idle()
        guest = system.scheduler.get_task(guest_task_id)
        assert guest is not None and guest.state == TaskState.TERMINATED
        assert guest.result == [10]
        assert struct.unpack_from("<I", memory)[0] == 10
        assert observed == [(3, TaskState.READY), (6, TaskState.READY), (9, TaskState.READY)]
    finally:
        system.shutdown()


def test_virq_unregisters_dispatcher_at_coos_boundary():
    """TEST-VSOC-52: raw host registration/removal publish at real interrupt handoffs."""
    system = System()
    module = system.runtime_engine.load_wasm(
        wat_to_wasm("""(module
      (func (param i32 i32 i32 i32 i32) (result i32) i32.const 0)
      (func (export "entry")))""")
    )
    interpreter = make_native_interpreter(module)
    event = _virq_event(0x2000)
    outcomes: list[VirqDispatchResult] = []

    def guest():
        yield from system.run_guest(interpreter, 1, ())
        assert system.host_calls.virq_register(int(VirqNode.ROOT), 0) == 0
        outcomes.append(system.runtime_engine.dispatch_interrupt_event(event).outcome)
        system.scheduler.wait_for_interrupt(event.vector_id)
        yield (ChannelAction.BLOCK, None)
        first = system.dispatch_current_interrupt()
        assert first is not None
        outcomes.append(first.outcome)
        assert system.host_calls.virq_unregister(int(VirqNode.ROOT)) == 0
        outcomes.append(system.runtime_engine.dispatch_interrupt_event(event).outcome)
        system.scheduler.wait_for_interrupt(event.vector_id)
        yield (ChannelAction.BLOCK, None)
        second = system.dispatch_current_interrupt()
        assert second is not None
        outcomes.append(second.outcome)

    def source():
        assert system.scheduler.notify_interrupt(event)
        yield (ChannelAction.YIELD, None)
        for _ in range(8):
            if len(outcomes) == 3:
                break
            yield (ChannelAction.YIELD, None)
        assert len(outcomes) == 3, "guest must reach its second interrupt wait"
        assert system.scheduler.notify_interrupt(event)
        yield (ChannelAction.YIELD, None)

    try:
        guest_id = system.scheduler.spawn("virq_guest", guest(), role=Role.RUNTIME)
        system.scheduler.spawn("source", source())
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(guest_id)
        assert task is not None and task.state == TaskState.TERMINATED
        assert outcomes == [
            VirqDispatchResult.PASS_THROUGH,
            VirqDispatchResult.HANDLED,
            VirqDispatchResult.HANDLED,
            VirqDispatchResult.PASS_THROUGH,
        ]
        assert task.pending_interrupt_event is None
        assert system.scheduler.dropped_irqs == 0
    finally:
        system.shutdown()


def test_hal_task_ipc_communication():
    """TEST-HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from tier2_runtime.hal.dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET
    from tier3_platform.drivers.hal.dummy import DummyDriver
    from tier3_platform.drivers.wasi.context import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_ipc_guest")
        buffer_handle = sysv.pool.buffer(0)
        assert sysv.pool.map_for_io(buffer_handle.buffer_id).name == "MAPPED"
        sysv.pool.view(buffer_handle, 0, 128)[:] = b"x" * 128
        sysv.scheduler.current_task = runtime_task
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
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
        except socket.timeout, BlockingIOError:
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
    ctx = DebugTestView()
    ctx.locals[:2] = (10, 20)
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
        expected_regs = (
            b"".join(struct.pack("<I", value) for value in (0x10, 0, 0, 0, 10, 20, *([0] * 14)))
            .hex()
            .encode()
        )
        assert resp[resp.index(b"$") + 1 : resp.index(b"#")] == expected_regs

        client.close()
    finally:
        sysv.shutdown()


@pytest.mark.parametrize("threshold", (1, 3, 7))
def test_coop_01_wasm_coroutine_yields_on_loop_threshold(threshold: int):
    """TEST-VSOC-10: LOOP backedge counts, not instruction counts, determine handoffs."""
    system = System()
    system.runtime_engine = RuntimeEngine(
        yield_threshold=threshold,
        drive_mode=RuntimeDriveMode.COOS,
    )
    module = _counter_module()
    memory = bytearray(65536)
    interpreter = make_native_interpreter(
        module, memory=memory, bump_allocator=system.runtime_engine.bump_allocator
    )
    values: list[int] = []

    def monitor():
        while True:
            task = system.scheduler.get_task(guest_id)
            assert task is not None
            if task.state == TaskState.TERMINATED:
                return
            assert task.state == TaskState.READY
            values.append(struct.unpack_from("<I", memory)[0])
            yield (ChannelAction.YIELD, None)

    try:
        guest_id = system.scheduler.spawn("guest", system.run_guest(interpreter, 0, (10,)))
        system.scheduler.spawn("monitor", monitor())
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(guest_id)
        assert task is not None and task.result == [10]
        assert values == list(range(threshold, 10, threshold))
    finally:
        system.shutdown()


def test_idle_01_jit_batch_compilation_on_idle():
    """TEST-IDLE-01: Compile queue is drained and compiled in LIFO order when scheduler fires idle_hook."""
    compiler = TraceCompiler()
    engine = make_runtime_engine(
        jit_compiler=compiler,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    wat = """(module
      (func (export "f0") (result i32) i32.const 10 i32.const 20 i32.add)
      (func (export "f1") (result i32) i32.const 30 i32.const 40 i32.add)
      (func (export "f2") (result i32) i32.const 50 i32.const 60 i32.add))"""
    module = parse(wat_to_wasm(wat))
    engine.register_module_blocks(module)
    pcs = [block.head_pc for block in module.blocks]
    assert len(pcs) == 3
    for pc in pcs[:2]:
        engine.jit_runtime.record_block_head(pc)
        engine.jit_runtime.record_block_head(pc)
    engine.jit_runtime.on_yield()
    assert engine.jit_runtime.has_pending_compilation()
    assert all(engine.jit_runtime.card_state(pc) == CardState.HOT for pc in pcs[:2])
    system = System()
    system.runtime_engine = engine
    try:
        system.scheduler.run_until_idle()
    finally:
        system.shutdown()
    assert not engine.jit_runtime.has_pending_compilation()
    assert engine.jit_runtime.compilation_pcs == (pcs[1], pcs[0]), "LIFO compilation order required"
    assert engine.jit_runtime.bitmap.get_state(pcs[0]) == CardState.COMPILED
    assert engine.jit_runtime.bitmap.get_state(pcs[1]) == CardState.COMPILED
    assert engine.jit_runtime.cache.find_trace(pcs[0]) is not None
    assert engine.jit_runtime.cache.find_trace(pcs[1]) is not None


def test_idle_02_logging_flush_on_idle():
    """TEST-LOG-06: System's scheduler idle hook flushes every deferred log in order."""
    sink = PrintkBuffer()
    system = System(
        printk_sink=sink, log_dictionary=LogDictionary(entries=((1, "event payload=%d"),))
    )
    try:
        assert system.logger.log_event(LogLevel.INFO, 1, 42) == LogResult.SUCCESS
        assert system.logger.log_event(LogLevel.INFO, 1, 99) == LogResult.SUCCESS
        assert sink.bytes_written == 0
        system.scheduler.run_until_idle()
        assert sink.drain_output().decode("utf-8").splitlines() == [
            "[INFO] event payload=42",
            "[INFO] event payload=99",
        ]
        assert system.logger.ring.count == 0
    finally:
        system.shutdown()


def test_tier_01_interpreter_to_jit_cooperative_flow():
    """Real native/JIT guest execution cooperates with COOS and flushes logs on idle."""
    sink = PrintkBuffer()
    system = System(
        printk_sink=sink, log_dictionary=LogDictionary(entries=((0x10, "wasm iteration=%d"),))
    )
    system.runtime_engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=2,
        card_shift=2,
        drive_mode=RuntimeDriveMode.COOS,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    module = parse(
        wat_to_wasm("""(module
      (import "test" "observe" (func $observe (param i32)))
      (func (export "count") (param $limit i32) (result i32) (local $index i32)
        (loop $loop
          local.get $index i32.const 1 i32.add local.set $index
          local.get $index call $observe
          local.get $index local.get $limit i32.lt_u br_if $loop)
        local.get $index))""")
    )
    reported: list[int] = []

    def report(value: int) -> int:
        reported.append(value)
        return 0

    interpreter = make_native_interpreter(
        module,
        host_functions=StaticVector.of((report,)),
        bump_allocator=system.runtime_engine.bump_allocator,
    )
    observed: list[int] = []

    def monitor():
        while True:
            task = system.scheduler.get_task(guest_id)
            assert task is not None
            if task.state == TaskState.TERMINATED:
                return
            assert task.state == TaskState.READY
            value = reported[-1]
            observed.append(value)
            assert system.logger.log_event(LogLevel.INFO, 0x10, value) == LogResult.SUCCESS
            assert sink.bytes_written == 0
            yield (ChannelAction.YIELD, None)

    try:
        guest_id = system.scheduler.spawn(
            "guest", system.run_guest(interpreter, module.export_func_index("count"), (12,))
        )
        system.scheduler.spawn("monitor", monitor())
        system.scheduler.run_until_idle()
        guest = system.scheduler.get_task(guest_id)
        assert guest is not None and guest.result == [12]
        # Scheduler handoffs occur after every two LOOP backedges.
        assert observed == [2, 4, 6, 8, 10]
        assert reported == list(range(1, 13))
        assert sink.drain_output().decode("utf-8").splitlines() == [
            f"[INFO] wasm iteration={value}" for value in (2, 4, 6, 8, 10)
        ]
        assert system.logger.ring.count == 0
        assert system.runtime_engine.stat_interp_steps > 0
        assert system.runtime_engine.stat_jit_invocations > 0
        manager = system.runtime_engine.jit_runtime
        assert manager is not None
        loop_pc = next(block.head_pc for block in module.blocks if block.loops_to is not None)
        assert (manager.cache.find_trace(loop_pc) is not None) or (
            manager.cache.find_trace(loop_pc) is not None
        )
    finally:
        system.shutdown()


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
    results = engine.call(make_native_interpreter(mod), 0, [5])
    assert results[0] == 120
    assert engine.stat_interp_steps >= 3
    assert engine.stat_jit_invocations >= 2
    assert engine.jit_runtime.bitmap.get_state(loop_pc) == CardState.COMPILED
    assert engine.jit_runtime.cache.find_trace(loop_pc) is not None


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
    trace_a = compile_module_block(engine.jit_runtime.jit_compiler, mod, block_a)
    engine.jit_runtime.cache.insert(trace_a)
    # Assert trace A chained directly into trace B
    assert trace_a.chain_next == block_b.head_pc
    results = engine.call(make_native_interpreter(mod), 0, [100])
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
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        from tier3_platform.drivers.hal.dummy import DummyDriver

        ctx = WasiHostContext(sysv)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
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
    """GDB smoke: stop query, register values, memory write and breakpoint resume."""
    from tier3_plugins.debugger.debugger import DebuggerManager, GDBRspProtocol

    engine = RuntimeEngineDebugDriver()
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(64)
    ctx = DebugTestView(memory=mem)
    ctx.locals[:2] = (10, 20)
    # 1. Query stop signal
    res, _ = rsp.handle_packet("?", 0x100, ctx, {})
    assert res == "$S05#b8"
    # 2. Virtual registers read/write
    res_g, _ = rsp.handle_packet("g", 0x100, ctx, {})
    assert (
        res_g[1 : res_g.index("#")]
        == b"".join(
            struct.pack("<I", value) for value in (0x100, 0, 0, 0, 10, 20, *([0] * 14))
        ).hex()
    )
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
    execution = make_debug_execution(mod, 0, (10,), memory=mem)
    dbg.detach()
    dbg = DebuggerManager(engine=execution)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    ctx = execution.context
    rsp.handle_packet(f"Z0,{block2.head_pc:x},0", block1.head_pc, ctx, blocks)
    res_c, stop_pc = rsp.handle_packet("c", block1.head_pc, ctx, blocks)
    assert res_c.startswith("$S05#")
    assert stop_pc == block2.head_pc
    assert ctx.locals[0] == 11
    # Remove BP and finish
    rsp.handle_packet(f"z0,{block2.head_pc:x},0", block2.head_pc, ctx, blocks)
    res_c2, _ = rsp.handle_packet("c", block2.head_pc, ctx, blocks)
    assert res_c2.startswith("$W00#")
    assert execution.call.results == [22]


def test_interpreter_debugger_attachment_and_hooks():
    """Test-driver integration: attach, breakpoint, sampling, assertions and no JIT selection."""
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
    # 1. Normal interpreter composition (normal interpreter-only selection)
    assert engine.handler_table == "interpreter"
    assert engine.debugger is None
    ctx_normal = DebugTestView()
    ctx_normal.locals[0] = 5
    next_pc = engine.run_step(block1.head_pc, ctx_normal)
    assert next_pc == block2.head_pc
    assert ctx_normal.locals[0] == 6
    # 2. Attach debugger (keeps the interpreter execution path)
    dbg.attach()
    assert engine.handler_table == "interpreter"
    assert engine.debugger is dbg
    # 3. Breakpoint hit (halts before execution)
    dbg.add_breakpoint(block2.head_pc)
    ctx_debug = DebugTestView(memory=bytearray([0x55, 0xAA]))
    ctx_debug.locals[0] = 10
    dbg.add_memory_assertion(0, 0x55, "valid magic")
    dbg.add_memory_assertion(1, 0x00, "invalid magic")  # Will fail
    # Step block1 (stops at block2 due to BP)
    next_pc = engine.run_step(block1.head_pc, ctx_debug)
    assert next_pc == block2.head_pc
    assert dbg.halted is True
    assert dbg.stop_signal == 5
    assert ctx_debug.locals[0] == 11
    # 4. Profiler & Assertions (sampling and assertions)
    assert dbg.pc_sample_counts[block1.head_pc] == 1
    assert len(dbg.assertion_violations) == 1
    # 5. Attached execution remains interpreter-only (JIT remains absent)
    assert engine.jit_runtime is None
    interp_before = engine.interp_blocks
    jit_before = engine.jit_traces
    engine.run_step(block1.head_pc, ctx_debug)
    assert engine.interp_blocks == interp_before + 1
    assert engine.jit_traces == jit_before
    # Detach
    dbg.detach()
    assert engine.handler_table == "interpreter"


# ===========================================================================
# Test Runner
# ===========================================================================

from hypothesis import example, given
from hypothesis import strategies as st


@given(source=st.integers(0, 192), destination=st.integers(0, 192), count=st.integers(0, 64))
@example(source=0, destination=128, count=64)
@example(source=0, destination=1, count=64)
@example(source=1, destination=0, count=64)
@example(source=64, destination=64, count=64)
@example(source=192, destination=0, count=0)
@example(source=65535, destination=128, count=1)
@example(source=128, destination=65535, count=1)
@example(source=65536, destination=65536, count=0)
@example(source=0, destination=16384, count=32768)
def test_linear_memory_copy_uses_cpu_memmove(source: int, destination: int, count: int) -> None:
    """TEST-VSOC-60/61: 大小・重複・同一・ゼロ長のlinear copyでDMAを呼ばない。"""
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter

    module = parse(
        wat_to_wasm("""(module (memory 1)
      (func (export "copy") (param i32 i32 i32)
        local.get 0 local.get 1 local.get 2 memory.copy))""")
    )
    system = System()
    try:
        memory = bytearray(range(256)) + bytearray(65536 - 256)
        expected = bytearray(memory)
        expected[destination : destination + count] = bytes(memory[source : source + count])
        calls: list[tuple[int, int, int]] = []

        def transfer(src: int, dst: int, length: int) -> int:
            calls.append((src, dst, length))
            return 0

        bindings = InterpreterBindings.with_memory_and_functions(
            memory,
            StaticVector(capacity=0),
            vdma_transfer=transfer,
        )
        interpreter = NativeInterpreter(
            module, bindings, bump_allocator=system.runtime_engine.bump_allocator
        )
        task_id = system.scheduler.spawn(
            "linear_copy_guest",
            system.run_guest(
                interpreter, module.export_func_index("copy"), (destination, source, count)
            ),
        )
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
        assert memory == expected
        assert calls == []
    finally:
        system.shutdown()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
