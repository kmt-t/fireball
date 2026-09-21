"""Guest function call-graph and execution-time profiler plugin."""

from __future__ import annotations

from dataclasses import dataclass

from runtime_events import RuntimeEvent, RuntimeEventFlags, RuntimeEventKind
from system_containers import MutableFlatMapStorage, StaticVector


@dataclass(slots=True)
class ProfileStats:
    """固定容量統計表に格納する関数単位の集計値。"""

    function_id: int
    call_count: int = 0
    inclusive_ticks: int = 0
    self_ticks: int = 0
    estimated: bool = False


@dataclass(slots=True)
class _OpenFrame:
    function_id: int
    parent_function_id: int
    enter_tick: int
    child_ticks: int = 0
    estimated: bool = False


class GuestProfiler:
    """Runtime 観測イベントだけを読み取り、コールグラフと時間を集計する。"""

    __slots__ = (
        "_edge_counts",
        "_frames",
        "_function_stats",
        "estimated_events",
        "lost_events",
        "unmatched_events",
    )

    def __init__(
        self,
        function_capacity: int = 64,
        edge_capacity: int = 128,
        stack_capacity: int = 64,
    ):
        self._function_stats: MutableFlatMapStorage[int, ProfileStats] = MutableFlatMapStorage(
            capacity=function_capacity
        )
        self._edge_counts: MutableFlatMapStorage[int, int] = MutableFlatMapStorage(
            capacity=edge_capacity
        )
        self._frames: StaticVector[_OpenFrame] = StaticVector(capacity=stack_capacity)
        self.estimated_events = 0
        self.lost_events = 0
        self.unmatched_events = 0

    @staticmethod
    def _edge_key(parent_function_id: int, function_id: int) -> int:
        return ((parent_function_id & 0xFFFF_FFFF) << 32) | (function_id & 0xFFFF_FFFF)

    def _function(self, function_id: int) -> ProfileStats:
        stats = self._function_stats.view().find(function_id)
        if stats is None:
            stats = ProfileStats(function_id=function_id)
            assert self._function_stats.insert(function_id, stats)
        return stats

    def _mark_estimated(self) -> None:
        self.estimated_events += 1
        if self._frames:
            self._frames[-1].estimated = True

    def _enter(self, event: RuntimeEvent) -> None:
        parent = self._frames[-1].function_id if self._frames else -1
        frame = _OpenFrame(parent, parent, event.tick)
        frame.function_id = event.function_id
        frame.parent_function_id = parent
        if not self._frames.push_back(frame):
            self.lost_events += 1
            self._mark_estimated()
            return

        stats = self._function(event.function_id)
        stats.call_count += 1
        if parent >= 0:
            edge_key = self._edge_key(parent, event.function_id)
            edge_count = self._edge_counts.view().find(edge_key)
            assert self._edge_counts.insert(edge_key, 1 if edge_count is None else edge_count + 1)

    def _exit(self, event: RuntimeEvent) -> None:
        if not self._frames or self._frames[-1].function_id != event.function_id:
            self.unmatched_events += 1
            self._mark_estimated()
            return

        frame = self._frames.pop_at()
        inclusive = max(0, event.tick - frame.enter_tick)
        self_ticks = max(0, inclusive - frame.child_ticks)
        stats = self._function(event.function_id)
        stats.inclusive_ticks += inclusive
        stats.self_ticks += self_ticks
        stats.estimated = stats.estimated or frame.estimated
        if event.flags & RuntimeEventFlags.ESTIMATED:
            stats.estimated = True
        if self._frames:
            self._frames[-1].child_ticks += inclusive

    def _finish_open_frames(self, event: RuntimeEvent) -> None:
        while self._frames:
            frame = self._frames.pop_at()
            stats = self._function(frame.function_id)
            stats.estimated = True
            stats.inclusive_ticks += max(0, event.tick - frame.enter_tick)
            self.estimated_events += 1

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        """イベントを一件だけ固定長状態へ反映する。"""

        if event.flags & (RuntimeEventFlags.DROPPED | RuntimeEventFlags.ESTIMATED):
            self._mark_estimated()
        if event.kind == RuntimeEventKind.FUNCTION_ENTER:
            self._enter(event)
        elif event.kind == RuntimeEventKind.FUNCTION_EXIT:
            self._exit(event)
        elif event.kind == RuntimeEventKind.TRAP or event.kind == RuntimeEventKind.DEBUG_STOP:
            self._finish_open_frames(event)

    def stats_for(self, function_id: int) -> ProfileStats:
        """関数統計を取得する。未観測関数の参照は契約違反とする。"""

        stats = self._function_stats.view().find(function_id)
        assert stats is not None
        return stats

    def edge_calls(self, parent_function_id: int, function_id: int) -> int:
        """親子関数辺の呼出回数を取得する。"""

        value = self._edge_counts.view().find(self._edge_key(parent_function_id, function_id))
        return 0 if value is None else value

    @property
    def open_frame_count(self) -> int:
        return len(self._frames)
