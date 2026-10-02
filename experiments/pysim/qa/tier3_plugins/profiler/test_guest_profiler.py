"""ゲストプロファイラのコールグラフ・時間集計を検証する。"""

from __future__ import annotations

from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[3]

import pytest
from runtime_events import (
    RUNTIME_EVENT_CLOCK_FREQUENCY_HZ,
    RUNTIME_EVENT_CLOCK_MONOTONIC_NS,
    RuntimeEvent,
    RuntimeEventBatch,
    RuntimeEventFlags,
    RuntimeEventKind,
)
from tier3_plugins.profiler.guest_profiler import GuestProfiler


def _event(kind: RuntimeEventKind, function_id: int, tick: int) -> RuntimeEvent:
    return RuntimeEvent(kind, 1, 1, function_id, 0, tick, function_id)


def test_call_graph_and_time_accounting() -> None:
    """TEST-PROF-01/02: nested batch gives one edge and exact inclusive/self time."""
    profiler = GuestProfiler()
    batch = _batch(
        (
            _event(RuntimeEventKind.FUNCTION_ENTER, 1, 0),
            _event(RuntimeEventKind.FUNCTION_ENTER, 2, 2),
            _event(RuntimeEventKind.FUNCTION_EXIT, 2, 5),
            _event(RuntimeEventKind.FUNCTION_EXIT, 1, 8),
        )
    )
    before = tuple(batch.records)
    profiler.on_runtime_batch(batch)
    assert tuple(batch.records) == before

    parent = profiler.stats_for(1)
    child = profiler.stats_for(2)
    assert parent.call_count == 1
    assert parent.inclusive_ticks == 8
    assert parent.self_ticks == 5
    assert child.inclusive_ticks == 3
    assert child.self_ticks == 3
    assert profiler.edge_calls(1, 2) == 1
    assert profiler.open_frame_count == 0


def test_trap_waits_for_ordered_exits_as_estimated() -> None:
    """TEST-PROF-03: trap leaves frames open until their matching exit events."""
    profiler = GuestProfiler()
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 7, 10))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 8, 12))
    profiler.on_runtime_event(RuntimeEvent(RuntimeEventKind.TRAP, 1, 1, 8, 0, 17, 8))
    assert profiler.open_frame_count == 2
    assert profiler.stats_for(7).inclusive_ticks == 0
    assert profiler.stats_for(8).inclusive_ticks == 0
    profiler.on_runtime_event(
        RuntimeEvent(
            RuntimeEventKind.FUNCTION_EXIT,
            1,
            1,
            8,
            0,
            17,
            8,
            RuntimeEventFlags.ESTIMATED | RuntimeEventFlags.ABORTED,
        )
    )
    assert profiler.open_frame_count == 1
    assert profiler.stats_for(8).inclusive_ticks == 5
    assert profiler.stats_for(7).inclusive_ticks == 0
    profiler.on_runtime_event(
        RuntimeEvent(
            RuntimeEventKind.FUNCTION_EXIT,
            1,
            1,
            7,
            0,
            17,
            7,
            RuntimeEventFlags.ESTIMATED | RuntimeEventFlags.ABORTED,
        )
    )

    assert profiler.open_frame_count == 0
    assert profiler.stats_for(7).estimated
    assert profiler.stats_for(7).inclusive_ticks == 7
    assert profiler.stats_for(7).self_ticks == 2
    assert profiler.stats_for(8).inclusive_ticks == 5
    assert profiler.stats_for(8).self_ticks == 5
    assert profiler.estimated_events > 0


def test_dropped_event_and_fixed_table_overflow_are_reported() -> None:
    """TEST-PROF-05: loss marks bounded table state without closing open frames."""
    profiler = GuestProfiler(function_capacity=1, edge_capacity=0, stack_capacity=2)
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 1, 0))
    dropped = RuntimeEvent(
        RuntimeEventKind.FUNCTION_EXIT,
        1,
        1,
        1,
        0,
        2,
        1,
        flags=RuntimeEventFlags.DROPPED,
    )
    profiler.on_runtime_event(dropped)
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 2, 3))

    assert profiler.lost_events >= 2
    assert profiler.estimated_events > 0
    assert profiler.stats_for(1).estimated
    assert profiler.open_frame_count == 2


def test_recursive_stack_overflow_does_not_close_parent_frame_early() -> None:
    """TEST-PROF-05: an untracked recursive exit must leave the tracked parent open."""
    profiler = GuestProfiler(stack_capacity=1)
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 9, 1))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 9, 2))

    assert profiler.open_frame_count == 1
    assert profiler.overflowed_frame_count == 1

    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 9, 3))
    assert profiler.open_frame_count == 1
    assert profiler.overflowed_frame_count == 0

    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 9, 5))
    assert profiler.open_frame_count == 0
    assert profiler.stats_for(9).call_count == 1
    assert profiler.stats_for(9).inclusive_ticks == 4
    assert profiler.stats_for(9).estimated


def _batch(events: tuple[RuntimeEvent, ...], dropped_count: int = 0) -> RuntimeEventBatch:
    return RuntimeEventBatch(
        1,
        events,
        dropped_count,
        RUNTIME_EVENT_CLOCK_FREQUENCY_HZ,
        RUNTIME_EVENT_CLOCK_MONOTONIC_NS,
    )


def test_cumulative_batch_loss_is_counted_once() -> None:
    """TEST-PROF-04: cumulative 5,5,8 adds only 5,0,3 across real batches."""
    profiler = GuestProfiler()
    enter = _batch((_event(RuntimeEventKind.FUNCTION_ENTER, 1, 10),))
    profiler.on_runtime_batch(enter)
    for count, expected in ((5, 5), (5, 5), (8, 8)):
        batch = _batch((), count)
        profiler.on_runtime_batch(batch)
        assert profiler.lost_events == expected
        assert profiler.open_frame_count == 1
        assert profiler.stats_for(1).inclusive_ticks == 0
        assert batch.dropped_count == count and batch.records == ()
    profiler.on_runtime_batch(_batch((_event(RuntimeEventKind.FUNCTION_EXIT, 1, 17),), 8))
    stats = profiler.stats_for(1)
    assert (stats.call_count, stats.inclusive_ticks, stats.self_ticks, stats.estimated) == (
        1,
        7,
        7,
        True,
    )
    assert profiler.open_frame_count == 0
    assert profiler.lost_events == 8


def test_debug_stop_closes_nested_frames_at_stop_tick() -> None:
    """TEST-PROF-06: stop closes nested frames as estimated; its input stays immutable."""
    profiler = GuestProfiler()
    events = (
        _event(RuntimeEventKind.FUNCTION_ENTER, 7, 10),
        _event(RuntimeEventKind.FUNCTION_ENTER, 8, 12),
        _event(RuntimeEventKind.DEBUG_STOP, 8, 17),
    )
    batch = _batch(events)
    profiler.on_runtime_batch(batch)
    assert tuple(batch.records) == events
    assert profiler.open_frame_count == 0
    assert profiler.stats_for(7).inclusive_ticks == 7
    assert profiler.stats_for(7).self_ticks == 2
    assert profiler.stats_for(7).estimated
    assert profiler.stats_for(8).inclusive_ticks == 5
    assert profiler.stats_for(8).self_ticks == 5
    assert profiler.stats_for(8).estimated
    assert profiler.edge_calls(7, 8) == 1


@pytest.mark.parametrize("capacity", ("function", "edge"))
def test_fixed_table_overflow_preserves_tracked_parent(capacity: str) -> None:
    """TEST-PROF-05: each fixed table can fill without corrupting parent timing."""
    profiler = GuestProfiler(
        function_capacity=1 if capacity == "function" else 2,
        edge_capacity=0 if capacity == "edge" else 2,
    )
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 1, 0))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 2, 2))
    assert profiler.lost_events == 1
    assert profiler.open_frame_count == 2
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 2, 5))
    assert profiler.open_frame_count == 1
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 1, 8))
    assert profiler.open_frame_count == 0
    parent = profiler.stats_for(1)
    assert (parent.call_count, parent.inclusive_ticks, parent.self_ticks, parent.estimated) == (
        1,
        8,
        5,
        True,
    )


def test_interpreter_and_jit_events_share_one_function_identity() -> None:
    """TEST-PROF-07 partial: both execution kinds aggregate under one function ID."""
    profiler = GuestProfiler(function_capacity=1)
    events = tuple(
        RuntimeEvent(kind, 1, 1, 9, 0, tick, call_id, flags)
        for call_id, flags, enter, exit in (
            (1, RuntimeEventFlags.INTERPRETER, 10, 12),
            (2, RuntimeEventFlags.JIT, 20, 24),
        )
        for kind, tick in (
            (RuntimeEventKind.FUNCTION_ENTER, enter),
            (RuntimeEventKind.FUNCTION_EXIT, exit),
        )
    )
    profiler.on_runtime_batch(_batch(events))
    stats = profiler.stats_for(9)
    assert (stats.call_count, stats.inclusive_ticks, stats.self_ticks) == (2, 6, 6)
    assert profiler.lost_events == 0 and profiler.open_frame_count == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
