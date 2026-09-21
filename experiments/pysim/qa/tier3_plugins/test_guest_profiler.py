"""ゲストプロファイラのコールグラフ・時間集計を検証する。"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[2]
for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_plugins",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from guest_profiler import GuestProfiler
from runtime_events import RuntimeEvent, RuntimeEventKind


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
    profiler.on_runtime_event(RuntimeEvent(RuntimeEventKind.TRAP, 1, 1, 7, 0, 14, 7))

    assert profiler.open_frame_count == 0
    assert profiler.stats_for(7).estimated
    assert profiler.estimated_events > 0


ALL_TESTS = tuple(
    value
    for name, value in globals().items()
    if name.startswith("test_") and callable(value)
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
