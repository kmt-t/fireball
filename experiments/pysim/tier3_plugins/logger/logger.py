"""Runtime 観測イベントを既存の固定辞書ロガーへ接続するプラグイン。"""

from __future__ import annotations

from runtime_events import RuntimeEvent
from tier2_runtime.logger import RUNTIME_EVENT_LOG_BASE, Logger, LogLevel


class RuntimeEventLogger:
    """イベントを固定IDと4個の整数引数へ変換して遅延ログへ積む。"""

    __slots__ = ("logger",)

    def __init__(self, logger: Logger):
        self.logger = logger

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        self.logger.log_event(
            LogLevel.DEBUG,
            RUNTIME_EVENT_LOG_BASE + int(event.kind),
            int(event.kind),
            event.function_id,
            event.guest_pc,
            event.tick,
        )
