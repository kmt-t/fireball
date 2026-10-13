"""Guest profiler event-to-log conversion and host aggregation tests."""

from __future__ import annotations

from tier2_runtime.observability.events import (
    RuntimeEvent,
    RuntimeEventBatch,
    RuntimeEventKind,
)
from tier2_runtime.observability.logger import LogDictionary, Logger, LogLevel
from tier3_plugins.profiler.aggregate import aggregate_profile, decode_profile_records
from tier3_plugins.profiler.guest_profiler import GuestProfiler


class _Sink:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: memoryview) -> int:
        self.data.extend(data)
        return len(data)


def _event(kind: RuntimeEventKind, function_id: int, call_id: int, tick: int) -> RuntimeEvent:
    return RuntimeEvent(kind, 1, 2, function_id, 3, tick, call_id)


def _encode_profile(event_id: int, function_id: int, call_id: int, tick: int) -> bytes:
    header = bytes((int(LogLevel.DEBUG), event_id & 0xFF, (event_id >> 8) & 0xFF, 0))
    return header + b"".join(value.to_bytes(4, "little") for value in (function_id, call_id, tick))


def _profile(events: tuple[RuntimeEvent, ...]) -> tuple[tuple[int, int, int, int], ...]:
    sink = _Sink()
    dictionary = LogDictionary()
    logger = Logger(sink, dictionary, min_level=LogLevel.DEBUG)
    profiler = GuestProfiler(logger)
    batch = RuntimeEventBatch(1, events, 99, 1_000_000_000, 1)
    profiler.on_runtime_batch(batch)
    assert tuple(batch.records) == events
    logger.flush()
    records = decode_profile_records(bytes(sink.data), dictionary)
    return tuple((row.event_id, row.function_id, row.call_id, row.tick) for row in records)


def test_function_boundaries_map_to_profile_log_ids_and_arguments() -> None:
    records = _profile(
        (
            _event(RuntimeEventKind.FUNCTION_ENTER, 17, 23, 31),
            _event(RuntimeEventKind.FUNCTION_EXIT, 17, 23, 47),
        )
    )
    assert records == ((0x0501, 17, 23, 31), (0x0502, 17, 23, 47))


def test_non_function_events_are_ignored() -> None:
    records = _profile(
        (
            _event(RuntimeEventKind.TRAP, 1, 1, 2),
            _event(RuntimeEventKind.DEBUG_STOP, 1, 1, 3),
            _event(RuntimeEventKind.JIT_ENTER, 1, 1, 4),
        )
    )
    assert records == ()


def test_batch_record_order_is_preserved() -> None:
    records = _profile(
        (
            _event(RuntimeEventKind.FUNCTION_ENTER, 1, 1, 10),
            _event(RuntimeEventKind.FUNCTION_ENTER, 2, 2, 11),
            _event(RuntimeEventKind.FUNCTION_EXIT, 2, 2, 12),
            _event(RuntimeEventKind.FUNCTION_EXIT, 1, 1, 13),
        )
    )
    assert tuple(row[0] for row in records) == (0x0501, 0x0501, 0x0502, 0x0502)
    assert tuple(row[1] for row in records) == (1, 2, 2, 1)


def test_host_aggregation_handles_recursion_edges_and_inclusive_self_time() -> None:
    data = b"".join(
        (
            _encode_profile(0x0501, 1, 10, 0xFFFF_FFF0),
            _encode_profile(0x0501, 2, 11, 0xFFFF_FFF8),
            _encode_profile(0x0502, 2, 11, 2),
            _encode_profile(0x0501, 1, 12, 3),
            _encode_profile(0x0502, 1, 12, 5),
            _encode_profile(0x0502, 1, 10, 10),
        )
    )
    records = decode_profile_records(data, LogDictionary())
    report = aggregate_profile(records)
    assert tuple(
        (row.function_id, row.call_count, row.inclusive_ticks, row.self_ticks)
        for row in report.functions
    ) == ((1, 2, 28, 16), (2, 1, 10, 10))
    assert tuple(
        (row.parent_function_id, row.function_id, row.call_count) for row in report.edges
    ) == ((1, 1, 1), (1, 2, 1))
    assert report.unmatched_exits == 0
    assert "may include intervals missing" in report.loss_caveat


def test_unmatched_exit_is_counted_without_popping_open_frame() -> None:
    sink = _Sink()
    dictionary = LogDictionary()
    logger = Logger(sink, dictionary, min_level=LogLevel.DEBUG)
    profiler = GuestProfiler(logger)
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 1, 7, 20))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 2, 7, 22))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 1, 7, 25))
    logger.flush()
    report = aggregate_profile(decode_profile_records(bytes(sink.data), dictionary))
    assert report.unmatched_exits == 1
    assert tuple((row.function_id, row.inclusive_ticks) for row in report.functions) == ((1, 5),)


def test_recursive_call_ids_disambiguate_same_function_frames() -> None:
    records = _profile(
        (
            _event(RuntimeEventKind.FUNCTION_ENTER, 9, 100, 3),
            _event(RuntimeEventKind.FUNCTION_ENTER, 9, 101, 4),
            _event(RuntimeEventKind.FUNCTION_EXIT, 9, 101, 5),
            _event(RuntimeEventKind.FUNCTION_EXIT, 9, 100, 8),
        )
    )
    assert tuple(row[2] for row in records) == (100, 101, 101, 100)


def test_tick_difference_wraps_at_32_bits() -> None:
    sink = _Sink()
    dictionary = LogDictionary()
    logger = Logger(sink, dictionary, min_level=LogLevel.DEBUG)
    profiler = GuestProfiler(logger)
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 5, 2, 0xFFFF_FFFE))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 5, 2, 3))
    logger.flush()
    report = aggregate_profile(decode_profile_records(bytes(sink.data), dictionary))
    summary = report.functions[0]
    assert (summary.inclusive_ticks, summary.self_ticks) == (5, 5)
