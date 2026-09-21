"""Runtime観測イベントの固定辞書ロガー接続を検証する。"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[3]
for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_plugins",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tier2_runtime.logger import LogDictionary, Logger, LogLevel
from tier3_plugins.logger.logger import RuntimeEventLogger
from runtime_events import RuntimeEvent, RuntimeEventKind


class _Sink:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)


def test_runtime_events_are_queued_as_fixed_dictionary_records() -> None:
    sink = _Sink()
    logger = Logger(sink, LogDictionary(), min_level=LogLevel.DEBUG, capacity=4)
    plugin = RuntimeEventLogger(logger)
    plugin.on_runtime_event(RuntimeEvent(RuntimeEventKind.FUNCTION_ENTER, 1, 1, 7, 0x20, 3, 1))

    assert len(logger.ring) == 1
    entry = logger.ring.buf[logger.ring.head]
    assert entry is not None
    assert entry.dict_offset == 0x0400 + int(RuntimeEventKind.FUNCTION_ENTER)
    assert logger.flush() == 1
    assert b"function=7" in bytes(sink.data)


if __name__ == "__main__":
    test_runtime_events_are_queued_as_fixed_dictionary_records()
    print("[PASS] test_runtime_events_are_queued_as_fixed_dictionary_records")
