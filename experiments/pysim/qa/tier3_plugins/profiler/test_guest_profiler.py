"""ゲストプロファイラのコールグラフ・時間集計を検証する。"""

from __future__ import annotations

from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[3]

from runtime_events import RuntimeEvent, RuntimeEventFlags, RuntimeEventKind
from tier3_plugins.profiler.guest_profiler import GuestProfiler


def _event(kind: RuntimeEventKind, function_id: int, tick: int) -> RuntimeEvent:
    return RuntimeEvent(kind, 1, 1, function_id, 0, tick, function_id)


def test_call_graph_and_time_accounting() -> None:
    profiler = GuestProfiler()
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 1, 0))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 2, 2))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 2, 5))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_EXIT, 1, 8))

    parent = profiler.stats_for(1)
    child = profiler.stats_for(2)
    assert parent.call_count == 1
    assert parent.inclusive_ticks == 8
    assert parent.self_ticks == 5
    assert child.inclusive_ticks == 3
    assert child.self_ticks == 3
    assert profiler.edge_calls(1, 2) == 1
    assert profiler.open_frame_count == 0


def test_trap_closes_open_frames_as_estimated() -> None:
    profiler = GuestProfiler()
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 7, 10))
    profiler.on_runtime_event(_event(RuntimeEventKind.FUNCTION_ENTER, 8, 12))
    profiler.on_runtime_event(RuntimeEvent(RuntimeEventKind.TRAP, 1, 1, 8, 0, 17, 8))

    assert profiler.open_frame_count == 0
    assert profiler.stats_for(7).estimated
    assert profiler.stats_for(7).inclusive_ticks == 7
    assert profiler.stats_for(7).self_ticks == 2
    assert profiler.stats_for(8).inclusive_ticks == 5
    assert profiler.stats_for(8).self_ticks == 5
    assert profiler.estimated_events > 0


def test_dropped_event_and_fixed_table_overflow_are_reported() -> None:
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


ALL_TESTS = tuple(
    value for name, value in globals().items() if name.startswith("test_") and callable(value)
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
