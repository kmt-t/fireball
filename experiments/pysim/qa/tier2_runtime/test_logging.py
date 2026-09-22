from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: System Logging & Ring Buffer
Traceability: runtime_logging_test_spec.md
"""

import sys
from pathlib import Path

# Setup paths
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
    _PYSIM_DIR / "tier3_executer",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_executer" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from helpers import expect_assertion, make_interpreter, wat_to_wasm
from tier3_executer.interpreter.interpreter import TRAP_LOG_EVENTS, TrapCode
from interrupt_event import InterruptEvent
from ipc_router import (
    IPCMessage,
    Role,
)
from tier2_runtime.logger import STANDARD_DIAGNOSTIC_EVENTS, ConsoleOutput, LogDictionary, Logger, LogLevel
from tier3_platform.drivers.hal.stream import DedicatedLogSink, StreamTransport
from system import (
    System,
)
from system_containers import MutableFlatMapStorage
from wasm_reader import parse


def test_log_01_dictionary_rejects_pointer_specifiers():
    d = LogDictionary()
    d.register(0x01, "ok: %d %d")
    for bad in ("bad: %s", "bad: %p", "bad: %c"):
        with expect_assertion():
            d.register(0x02, bad)


def test_log_02_logger_ring_buffer_overwrites():
    t = StreamTransport()
    try:
        d = LogDictionary()
        d.register(0x01, "event #%d")
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=4)
        for i in range(6):
            logger.log_event(LogLevel.INFO, 0x01, i)

        assert logger.ring.overwrite_count == 2
        flushed = logger.flush()
        assert flushed == 4
        wire = t.drain_output().decode()
        assert "event #2" in wire and "event #5" in wire
    finally:
        t.close()


def test_log_03_dictionary_storage_ownership_separation():
    """TEST-LOG-03: LogDictionary borrows entries storage without owning/duplicating it."""
    storage = MutableFlatMapStorage[int, str](capacity=4)
    assert storage.insert(0x01, "event #%d")
    assert storage.insert(0x02, "value %d %d")
    d = LogDictionary(storage=storage)

    # Ownership separation assertion
    assert d.storage is storage
    assert d.payload.entries is storage
    assert d.view() is d.payload
    assert d.entries is storage
    assert d.format(0x01, (42, 0, 0, 0)) == "event #42"
    assert d.format(0x02, (10, 20, 0, 0)) == "value 10 20"
    transport = StreamTransport()
    try:
        assert ConsoleOutput(transport).write(b"raw") == 3
        assert transport.drain_output() == b"raw"
    finally:
        transport.close()


def test_log_04_coos_and_ipc_diagnostic_logging():
    """TEST-LOG-04: COOS and IPC emit strict diagnostic log events upon anomalies/boundary conditions."""
    logger_sink = DedicatedLogSink()
    sysv = System(logger_sink=logger_sink)
    try:
        # 1. COOS Duplicate Task ID -> 0x0103
        def dummy_coro():
            return
            yield

        with expect_assertion():
            sysv.scheduler.spawn("dup_task", dummy_coro(), task_id=99)
            sysv.scheduler.spawn("dup_task_2", dummy_coro(), task_id=99)

        # 2. COOS IRQ Queue Overflow -> 0x0104
        for irq_idx in range(20):
            sysv.scheduler.notify_interrupt(InterruptEvent(irq_idx, 0, 0, 0, 0))

        # 3. IPC Unknown URI -> 0x0202
        def bad_uri_task():
            sysv.ipc.lookup("fireball://unknown/service")
            return
            yield

        sysv.scheduler.spawn("bad_uri_task", bad_uri_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 4. IPC RBAC Denied -> 0x0201
        def rbac_denied_task():
            # RUNTIME sending to DEBUGGER is DENIED
            sysv.ipc.lookup("fireball://dbg/manager/0")
            return
            yield

        sysv.scheduler.spawn("rbac_denied_task", rbac_denied_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 5. IPC Message Too Large -> 0x0203
        def too_large_task():
            too_large_msg = IPCMessage.from_entries(
                [(i, i) for i in range(1, 10)],  # 9 pairs > 8
                memory_manager=sysv.memory_manager,
            )
            _, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
            assert ch is not None
            yield from sysv.ipc.send(ch, too_large_msg)

        sysv.scheduler.spawn("too_large_task", too_large_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # Flush logger to UART (in addition to idle hooks)
        sysv.logger.flush()
        wire = logger_sink.drain_output().decode()

        # Verify all diagnostic strings were formatted and transmitted
        assert "COOS: duplicate task id rejected" in wire
        assert "COOS: irq queue overflow dropped" in wire
        assert "IPC: unknown uri routing failed" in wire
        assert "IPC: rbac denied" in wire
        assert "IPC: message too large" in wire
    finally:
        sysv.shutdown()


def test_log_05_gotcha_03_interrupt_checked_only_at_batch_boundary():
    """GOTCHA-LOG-03: flush() checks interrupt_pending only after a batch
    completes, never mid-batch, since a started transfer cannot be preempted."""
    t = StreamTransport()
    try:
        d = LogDictionary()
        d.register(0x01, "event #%d")
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=8)
        for i in range(4):
            logger.log_event(LogLevel.INFO, 0x01, i)

        call_count = 0

        def mock_interrupt() -> bool:
            nonlocal call_count
            call_count += 1
            return True  # already pending once the first batch completes

        flushed = logger.flush(batch_size=2, interrupt_pending=mock_interrupt)
        assert flushed == 2, "only the first batch (2 entries) should be transmitted"
        assert logger.ring.count == 2, "the second batch's entries remain buffered"
        assert call_count == 1, "interrupt_pending must be checked once per batch, not per entry"
    finally:
        t.close()


def test_log_12_interpreter_trap_diagnostic_logging():
    """TEST-LOG-12 / GOTCHA-LOG-04: a guest trap is logged via the dictionary,
    with the trapping unified_pc captured before frame teardown."""
    wat = """
    (module
      (func $div_s (export "div_s") (param $a i32) (param $b i32) (result i32)
        (i32.div_s (local.get $a) (local.get $b))
      )
    )
    """
    mod = parse(wat_to_wasm(wat))
    t = StreamTransport()
    try:
        logger = Logger(t, LogDictionary(), min_level=LogLevel.DEBUG, capacity=8)
        interp = make_interpreter(mod, logger=logger)
        func_index = mod.export_func_index("div_s")
        call_state = interp.start(func_index, [10, 0])
        while not call_state.finished:
            call_state = interp.step(call_state)
        assert call_state.trap is not None
        assert call_state.trap.code == TrapCode.INTEGER_DIVIDE_BY_ZERO

        flushed = logger.flush()
        assert flushed == 1
        wire = t.drain_output().decode()
        assert "TRAP: integer divide by zero (pc=0x" in wire
    finally:
        t.close()


def test_log_13_trap_log_dictionary_sync_with_interpreter():
    """TEST-LOG-13: interpreter.TRAP_LOG_EVENTS and logger.STANDARD_DIAGNOSTIC_EVENTS
    agree on every trap event id and format string (no registration drift,
    verification-antipatterns.md pattern E: unbacked/diverging numbers)."""
    logger_events_by_id = dict(STANDARD_DIAGNOSTIC_EVENTS)
    assert len(TRAP_LOG_EVENTS) == len(TrapCode)
    for offset, fmt in TRAP_LOG_EVENTS:
        assert offset in logger_events_by_id, f"trap event 0x{offset:X} missing from logger.py"
        assert logger_events_by_id[offset] == fmt, f"trap event 0x{offset:X} format text diverged"


if __name__ == "__main__":
    test_log_01_dictionary_rejects_pointer_specifiers()
    test_log_02_logger_ring_buffer_overwrites()
    test_log_03_dictionary_storage_ownership_separation()
    test_log_04_coos_and_ipc_diagnostic_logging()
    test_log_05_gotcha_03_interrupt_checked_only_at_batch_boundary()
    test_log_12_interpreter_trap_diagnostic_logging()
    test_log_13_trap_log_dictionary_sync_with_interpreter()
    print("[PASS] All 7 System Logging & Ring Buffer tests passed.")
