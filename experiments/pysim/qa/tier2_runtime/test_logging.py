from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: System Logging & Ring Buffer
Traceability: runtime_logging_test_spec.md
"""

from collections.abc import Sequence

import pytest
from interrupt_event import InterruptEvent
from ipc_router import (
    IPCMessage,
    IPCStatus,
    Role,
)
from qa.shared.helpers import (
    expect_assertion,
    make_interpreter,
    make_native_interpreter,
    wat_to_wasm,
)
from scheduler import FB_CONF_INTERRUPT_QUEUE_SIZE
from system import (
    System,
)
from system_containers import ReadOnlyFlatMapStorage
from tier2_runtime.interpreter.interpreter import TRAP_LOG_EVENTS, TrapCode
from tier2_runtime.observability.logger import (
    STANDARD_DIAGNOSTIC_EVENTS,
    LogDictionary,
    Logger,
    LogLevel,
    LogResult,
    decode_log_records,
)
from tier2_runtime.wasm.reader import parse
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.printk import PrintkBuffer


def _event_record(value: int) -> bytes:
    """独立した固定レコード期待値。INFO、辞書ID 1、arg0だけを指定する。"""
    return b"\x01\x01\x00\x00" + value.to_bytes(4, "little") + bytes(12)


class _HostFormatSpy(LogDictionary):
    def __init__(self) -> None:
        super().__init__(entries=((0xABCDEF, "%d %x %u %d"),), include_diagnostic_events=False)
        self.format_calls = 0

    def format(self, offset: int, args: Sequence[int]) -> str:
        self.format_calls += 1
        return super().format(offset, args)


def test_log_01_fixed_record_preserves_every_field_without_host_formatting():
    """TEST-LOG-01: u24 IDと4個のu32を既知の20バイト列で照合する。"""
    t = StreamTransport()
    try:
        d = _HostFormatSpy()
        logger = Logger(t, d, capacity=1)
        assert (
            logger.log_event(LogLevel.WARN, 0xABCDEF, 0x12345678, 0x89ABCDEF, 0xFFFFFFFF, 0)
            == LogResult.SUCCESS
        )
        assert t.drain_output() == b""
        assert logger.ring.count == 1
        assert logger.flush() == 1
        assert t.drain_output() == bytes.fromhex(
            "02 ef cd ab 78 56 34 12 ef cd ab 89 ff ff ff ff 00 00 00 00"
        )
        assert logger.ring.is_empty()
        assert d.format_calls == 0
    finally:
        t.close()


def test_log_02_host_decoder_expands_known_id_and_marks_unknown_id():
    """TEST-LOG-02: loggerのエンコーダーを使わない入力でホスト展開を検査する。"""
    d = LogDictionary(entries=((0x123456, "value=%d hex=%08X"),), include_diagnostic_events=False)
    records = bytes.fromhex(
        "01 56 34 12 2a 00 00 00 ef cd ab 89 00 00 00 00 00 00 00 00"
        "03 ef cd ab 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert tuple(decode_log_records(records, d)) == (
        "[INFO] value=42 hex=89ABCDEF",
        "[ERROR] <UNKNOWN_DICT_OFFSET_0xABCDEF>",
    )


def test_log_03_dictionary_rejects_pointer_specifiers():
    """TEST-LOG-03 / GOTCHA-LOG-01: 数値以外の指定子を生成時に拒否する。"""
    LogDictionary(entries=((0x01, "ok: %d %d"),), include_diagnostic_events=False)
    for event_id, bad in enumerate(("bad: %s", "bad: %p", "bad: %c", "bad: %08s"), 2):
        with expect_assertion():
            LogDictionary(entries=((event_id, bad),), include_diagnostic_events=False)
    for malformed in ("bad: %*d", "bad: %1$d", "bad: %f", "bad: %d %d %d %d %d"):
        with expect_assertion():
            LogDictionary(entries=((0x10, malformed),), include_diagnostic_events=False)


def test_log_04_logger_overwrites_oldest_and_preserves_complete_fifo_order():
    """TEST-LOG-04 / GOTCHA-LOG-02: 最古2件を除外し、残る全4件を順に送る。"""
    t = StreamTransport()
    try:
        d = LogDictionary(entries=((0x01, "event #%d"),))
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=4)
        for i in range(6):
            expected = LogResult.SUCCESS if i < 4 else LogResult.OVERWRITTEN
            assert logger.log_event(LogLevel.INFO, 0x01, i) == expected
            assert logger.ring.count == min(i + 1, 4)
            assert logger.ring.overwrite_count == max(i - 3, 0)

        assert logger.ring.overwrite_count == 2
        flushed = logger.flush()
        assert flushed == 4
        wire = t.drain_output()
        assert wire == b"".join(_event_record(i) for i in (2, 3, 4, 5))
        assert tuple(decode_log_records(wire, d)) == (
            "[INFO] event #2",
            "[INFO] event #3",
            "[INFO] event #4",
            "[INFO] event #5",
        )
        assert logger.ring.is_empty()
        assert logger.log_event(LogLevel.INFO, 0x01, 6) == LogResult.SUCCESS
        assert logger.ring.overwrite_count == 2
        assert logger.flush() == 1
        assert t.drain_output() == _event_record(6)
    finally:
        t.close()


def test_log_05_below_minimum_level_has_no_buffer_or_transport_effect():
    """TEST-LOG-05: WARN未満だけを捨て、境界以上を順序どおり保存する。"""
    t = StreamTransport()
    try:
        logger = Logger(t, LogDictionary(), min_level=LogLevel.WARN, capacity=3)
        for level in (LogLevel.DEBUG, LogLevel.INFO):
            assert logger.log_event(level, 1, 99) == LogResult.FILTERED
            assert logger.ring.count == 0
            assert logger.ring.overwrite_count == 0
        assert t.drain_output() == b""
        for level in (LogLevel.WARN, LogLevel.ERROR, LogLevel.FATAL):
            assert logger.log_event(level, 1, int(level)) == LogResult.SUCCESS
        assert logger.flush() == 3
        assert t.drain_output() == b"".join(
            bytes((int(level), 1, 0, 0)) + int(level).to_bytes(4, "little") + bytes(12)
            for level in (LogLevel.WARN, LogLevel.ERROR, LogLevel.FATAL)
        )
        assert logger.ring.is_empty()
    finally:
        t.close()


def test_log_06_flush_transmits_all_entries_then_empty_flush_has_no_effect():
    """TEST-LOG-06: idle相当のflushは全件を送り、空になった後は再送しない。"""
    t = StreamTransport()
    try:
        logger = Logger(t, LogDictionary(), capacity=3)
        for value in (9, 7, 8):
            assert logger.log_event(LogLevel.INFO, 1, value) == LogResult.SUCCESS
        assert logger.flush(batch_size=2) == 3
        assert t.drain_output() == b"".join(_event_record(value) for value in (9, 7, 8))
        assert logger.ring.count == 0
        assert logger.flush() == 0
        assert t.drain_output() == b""
    finally:
        t.close()


def test_log_08_ring_reuses_preallocated_records_after_overwrite_and_flush():
    """TEST-LOG-08: 固定レコードの同一性を検査する。全割当ての証明ではない。"""
    t = StreamTransport()
    try:
        logger = Logger(t, LogDictionary(), capacity=2)
        slots = tuple(logger.ring.buf)
        for value in range(7):
            logger.log_event(LogLevel.INFO, 1, value)
            if value == 3:
                logger.flush()
            assert len(logger.ring.buf) == 2
            assert all(
                actual is original for actual, original in zip(logger.ring.buf, slots, strict=True)
            )
    finally:
        t.close()


def test_log_11_dictionary_storage_ownership_separation():
    """TEST-LOG-11: 外部辞書ストレージを複製せず借用する。"""
    storage = ReadOnlyFlatMapStorage.create(((0x01, "event #%d"), (0x02, "value %d %d")))
    d = LogDictionary(storage=storage)

    # Ownership separation assertion
    assert d.storage is storage
    assert d.payload.entries is storage.entries
    assert d.view() is d.payload
    assert d.entries is storage.entries
    assert d.format(0x01, (42, 0, 0, 0)) == "event #42"
    assert d.format(0x02, (10, 20, 0, 0)) == "value 10 20"


def test_printk_14_coos_and_ipc_diagnostics_preserve_event_ids_and_arguments():
    """TEST-LOG-14: T1診断がLoggerを通らずprintkへ記録されることを検査する。"""
    printk_sink = PrintkBuffer()
    sysv = System(printk_sink=printk_sink)
    try:
        # 1. COOS Duplicate Task ID -> 0x0103
        def dummy_coro():
            return
            yield

        with expect_assertion():
            sysv.scheduler.spawn("dup_task", dummy_coro(), task_id=99)
            sysv.scheduler.spawn("dup_task_2", dummy_coro(), task_id=99)

        # 2. COOS IRQ Queue Overflow -> 0x0104
        for irq_idx in range(FB_CONF_INTERRUPT_QUEUE_SIZE + 4):
            assert sysv.scheduler.notify_interrupt(InterruptEvent(irq_idx, 0, 0, 0, 0)) == (
                irq_idx < FB_CONF_INTERRUPT_QUEUE_SIZE
            )

        # 3. IPC Unknown URI -> 0x0202
        def bad_uri_task():
            status, channel = sysv.ipc.lookup("fireball://unknown/service")
            assert status == IPCStatus.ERR_NOT_FOUND
            assert channel is None
            return
            yield

        sysv.scheduler.spawn("bad_uri_task", bad_uri_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 4. IPC RBAC Denied -> 0x0201
        def rbac_denied_task():
            # RUNTIME sending to DEBUGGER is DENIED
            status, channel = sysv.ipc.lookup("fireball://dbg/manager/0")
            assert status == IPCStatus.ERR_PERMISSION_DENIED
            assert channel is None
            return
            yield

        sysv.scheduler.spawn("rbac_denied_task", rbac_denied_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 5. IPC Message Too Large -> 0x0203
        def too_large_task():
            too_large_msg = IPCMessage.from_entries(
                [(i, i) for i in range(1, 9)],
                memory_manager=sysv.memory_manager,
            )
            # Malformed input at the send boundary; internal construction
            # itself must assert before writing an oversized batch.
            block = too_large_msg.block
            assert block is not None
            block.write_u64(0, 9)
            _, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
            assert ch is not None
            status, response = yield from sysv.ipc.send(ch, too_large_msg)
            assert status == IPCStatus.ERR_MSG_TOO_LARGE
            assert response is None

        sysv.scheduler.spawn("too_large_task", too_large_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # Tier 1 printk events are synchronous; draining also includes buffered logger records.
        sysv.logger.flush()
        wire = printk_sink.drain_output()
        assert len(wire) == 8 * 20
        records = tuple(
            (
                wire[offset],
                int.from_bytes(wire[offset + 1 : offset + 4], "little"),
                tuple(
                    int.from_bytes(wire[start : start + 4], "little")
                    for start in range(offset + 4, offset + 20, 4)
                ),
            )
            for offset in range(0, len(wire), 20)
        )
        assert records == (
            (3, 0x0103, (99, 0, 0, 0)),
            *((2, 0x0104, (FB_CONF_INTERRUPT_QUEUE_SIZE + i, i + 1, 0, 0)) for i in range(4)),
            (2, 0x0202, (0, 0, 0, 0)),
            (2, 0x0201, (0, 8, 0, 0)),
            (3, 0x0203, (9, 8, 0, 0)),
        )
    finally:
        sysv.shutdown()


def test_log_07_interrupt_preserves_unsent_second_batch_in_fifo_order():
    """TEST-LOG-07 / GOTCHA-LOG-03: flush() checks interrupt_pending only after a batch
    completes, never mid-batch, since a started transfer cannot be preempted."""
    t = StreamTransport()
    try:
        d = LogDictionary(entries=((0x01, "event #%d"),))
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=8)
        for i in range(4):
            assert logger.log_event(LogLevel.INFO, 0x01, i) == LogResult.SUCCESS

        call_count = 0

        def mock_interrupt() -> bool:
            nonlocal call_count
            call_count += 1
            assert logger.ring.count == 2
            assert logger.ring.peek() is not None
            assert logger.ring.peek().arg0 == 2
            return True  # already pending once the first batch completes

        flushed = logger.flush(batch_size=2, interrupt_pending=mock_interrupt)
        assert flushed == 2, "only the first batch (2 entries) should be transmitted"
        assert logger.ring.count == 2, "the second batch's entries remain buffered"
        assert call_count == 1, "interrupt_pending must be checked once per batch, not per entry"
        assert t.drain_output() == _event_record(0) + _event_record(1)
        assert logger.flush(batch_size=2) == 2
        assert t.drain_output() == _event_record(2) + _event_record(3)
        assert logger.ring.is_empty()
    finally:
        t.close()


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
@pytest.mark.parametrize("entry", ("div_s", "caller"), ids=("direct", "callee"))
def test_log_12_interpreter_trap_diagnostic_logging(native: bool, entry: str) -> None:
    """TEST-LOG-12 / GOTCHA-LOG-04: a guest trap is logged via the dictionary,
    with the trapping unified_pc captured before frame teardown."""
    wat = """
    (module
      (func $unused)
      (func $div_s (export "div_s") (param $a i32) (param $b i32) (result i32)
        (i32.div_s (local.get $a) (local.get $b))
      )
      (func (export "caller") (param i32 i32) (result i32)
        local.get 0 local.get 1 call $div_s)
    )
    """
    mod = parse(wat_to_wasm(wat))
    t = StreamTransport()
    try:
        dictionary = LogDictionary()
        logger = Logger(t, dictionary, min_level=LogLevel.DEBUG, capacity=8)
        factory = make_native_interpreter if native else make_interpreter
        interp = factory(mod, logger=logger)
        func_index = mod.export_func_index("div_s")
        assert func_index == 1
        assert bytes(mod.code_for(func_index)) == bytes.fromhex("20 00 20 01 6d 0b")
        call_state = interp.start(mod.export_func_index(entry), [10, 0])
        while not call_state.finished:
            call_state = interp.step(call_state)
        assert call_state.trap is not None
        assert call_state.trap.code == TrapCode.INTEGER_DIVIDE_BY_ZERO

        flushed = logger.flush()
        assert flushed == 1
        wire = t.drain_output()
        # Code-section payload-relative div_s instruction address is 0x0000000A.
        assert wire == bytes.fromhex("03 0f 03 00 0a 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
        assert tuple(decode_log_records(wire, dictionary)) == (
            "[ERROR] TRAP: integer divide by zero (pc=0x0000000A)",
        )
        assert logger.ring.is_empty()
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
    raise SystemExit(pytest.main([__file__, "-q"]))
