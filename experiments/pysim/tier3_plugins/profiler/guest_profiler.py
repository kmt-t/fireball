"""Translate Runtime function boundary events into fixed profile log records."""

from __future__ import annotations

from tier2_runtime.observability.events import RuntimeEvent, RuntimeEventBatch, RuntimeEventKind
from tier2_runtime.observability.logger import PROFILE_LOG_BASE
from tier2_runtime.observability.logging_interface import LoggerPort, LogLevel


class GuestProfiler:
    """Map function enter/exit events to profile log IDs without guest-side aggregation."""

    __slots__ = ("logger",)

    def __init__(self, logger: LoggerPort) -> None:
        self.logger = logger

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        if event.kind == RuntimeEventKind.FUNCTION_ENTER:
            offset = PROFILE_LOG_BASE + 1
        elif event.kind == RuntimeEventKind.FUNCTION_EXIT:
            offset = PROFILE_LOG_BASE + 2
        else:
            return
        self.logger.log_event(
            LogLevel.DEBUG,
            offset,
            event.function_id,
            event.call_id,
            event.tick & 0xFFFF_FFFF,
        )

    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None:
        for event in batch.records:
            self.on_runtime_event(event)
