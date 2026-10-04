"""Guest function call-graph and execution-time profiler plugin."""

from __future__ import annotations

from dataclasses import dataclass

from system_containers import MutableFlatMapStorage, StaticVector
from tier2_runtime.observability.events import (
    RuntimeEvent,
    RuntimeEventBatch,
    RuntimeEventFlags,
    RuntimeEventKind,
)


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
        "_last_dropped_count",
        "_loss_pending",
        "_trap_pending",
        "estimated_events",
        "lost_events",
        "overflowed_frames",
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
        self._last_dropped_count = 0
        self._loss_pending = False
        self._trap_pending = False
        self.estimated_events = 0
        self.lost_events = 0
        self.overflowed_frames = 0
        self.unmatched_events = 0

    @staticmethod
    def _edge_key(parent_function_id: int, function_id: int) -> int:
        return ((parent_function_id & 0xFFFF_FFFF) << 32) | (function_id & 0xFFFF_FFFF)

    def _function(self, function_id: int) -> ProfileStats | None:
        stats = self._function_stats.view().find(function_id)
        if stats is None:
            stats = ProfileStats(function_id=function_id)
            if not self._function_stats.insert(function_id, stats):
                self.lost_events += 1
                self._mark_estimated()
                return None
        return stats

    def _mark_estimated(self) -> None:
        self.estimated_events += 1
        for index in range(len(self._frames)):
            frame = self._frames[index]
            frame.estimated = True
            stats = self._function_stats.view().find(frame.function_id)
            if stats is not None:
                stats.estimated = True

    def _enter(self, event: RuntimeEvent) -> None:
        parent = self._frames[-1].function_id if self._frames else -1
        frame = _OpenFrame(
            event.function_id,
            parent,
            event.tick,
            estimated=self._loss_pending,
        )
        if not self._frames.push_back(frame):
            self.overflowed_frames += 1
            self.lost_events += 1
            self._mark_estimated()
            return

        stats = self._function(event.function_id)
        if stats is None:
            frame.estimated = True
        else:
            stats.call_count += 1
        if parent >= 0:
            edge_key = self._edge_key(parent, event.function_id)
            edge_count = self._edge_counts.view().find(edge_key)
            if not self._edge_counts.insert(edge_key, 1 if edge_count is None else edge_count + 1):
                self.lost_events += 1
                self._mark_estimated()
                frame.estimated = True

    def _exit(self, event: RuntimeEvent) -> None:
        if self.overflowed_frames:
            self.overflowed_frames -= 1
            self._mark_estimated()
            return
        if not self._frames or self._frames[-1].function_id != event.function_id:
            self.unmatched_events += 1
            self._mark_estimated()
            return

        frame = self._frames.pop_at()
        if (
            self._loss_pending
            or self._trap_pending
            or event.flags & (RuntimeEventFlags.ESTIMATED | RuntimeEventFlags.ABORTED)
        ):
            frame.estimated = True
        inclusive = max(0, event.tick - frame.enter_tick)
        self_ticks = max(0, inclusive - frame.child_ticks)
        stats = self._function(event.function_id)
        if stats is not None:
            stats.inclusive_ticks += inclusive
            stats.self_ticks += self_ticks
            stats.estimated = stats.estimated or frame.estimated
            if event.flags & RuntimeEventFlags.ESTIMATED:
                stats.estimated = True
        if self._frames:
            self._frames[-1].child_ticks += inclusive
        else:
            self._loss_pending = False
            self._trap_pending = False

    def _finish_open_frames(self, event: RuntimeEvent) -> None:
        self.overflowed_frames = 0
        self._loss_pending = False
        self._trap_pending = False
        while self._frames:
            frame = self._frames.pop_at()
            stats = self._function(frame.function_id)
            inclusive = max(0, event.tick - frame.enter_tick)
            self_ticks = max(0, inclusive - frame.child_ticks)
            if stats is not None:
                stats.estimated = True
                stats.inclusive_ticks += inclusive
                stats.self_ticks += self_ticks
            if self._frames:
                self._frames[-1].child_ticks += inclusive
            self.estimated_events += 1

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        """イベントを一件だけ固定長状態へ反映する。"""

        if event.flags & RuntimeEventFlags.DROPPED:
            self.lost_events += 1
            self._loss_pending = True
            self._mark_estimated()
            return
        if event.flags & RuntimeEventFlags.ESTIMATED:
            self._mark_estimated()
        if event.kind == RuntimeEventKind.FUNCTION_ENTER:
            self._enter(event)
        elif event.kind == RuntimeEventKind.FUNCTION_EXIT:
            self._exit(event)
        elif event.kind == RuntimeEventKind.TRAP:
            self._trap_pending = True
            self._mark_estimated()
        elif event.kind == RuntimeEventKind.DEBUG_STOP:
            self._finish_open_frames(event)

    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None:
        """Consume one Python-side batch, including its cumulative loss count."""

        if batch.dropped_count >= self._last_dropped_count:
            dropped = batch.dropped_count - self._last_dropped_count
            if dropped:
                self.lost_events += dropped
                self._loss_pending = True
                self._mark_estimated()
        self._last_dropped_count = batch.dropped_count
        for event in batch.records:
            self.on_runtime_event(event)

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

    @property
    def overflowed_frame_count(self) -> int:
        return self.overflowed_frames
