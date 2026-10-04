"""Runtime観測イベントの固定辞書ロガー接続を検証する。"""

from __future__ import annotations

from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[3]

from tier2_runtime.observability.events import RuntimeEvent, RuntimeEventBatch, RuntimeEventKind
from tier2_runtime.observability.logger import LogDictionary, Logger, LogLevel, decode_log_records
from tier3_plugins.logger.logger import RuntimeEventLogger


class _Sink:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: memoryview) -> int:
        self.data.extend(data)
        return len(data)


def test_runtime_events_are_queued_as_fixed_dictionary_records() -> None:
    sink = _Sink()
    logger = Logger(sink, LogDictionary(), min_level=LogLevel.DEBUG, capacity=4)
    plugin = RuntimeEventLogger(logger)
    plugin.on_runtime_batch(
        RuntimeEventBatch(
            1,
            (
                RuntimeEvent(RuntimeEventKind.FUNCTION_ENTER, 1, 1, 7, 0x20, 3, 1),
                RuntimeEvent(RuntimeEventKind.JIT_EXIT, 1, 1, 9, 0x12345678, 0x90ABCDEF, 2),
            ),
            0,
            1_000_000_000,
            1,
        )
    )
    plugin.on_runtime_batch(
        RuntimeEventBatch(
            1,
            (RuntimeEvent(RuntimeEventKind.FUNCTION_EXIT, 1, 1, 7, 0x24, 19, 1),),
            0,
            1_000_000_000,
            1,
        )
    )

    assert len(logger.ring) == 3
    entry = logger.ring.buf[logger.ring.head]
    assert entry is not None
    assert entry.dict_offset == 0x0400 + int(RuntimeEventKind.FUNCTION_ENTER)
    assert logger.flush() == 3
    assert bytes(sink.data) == bytes.fromhex(
        "00 02 04 00 02 00 00 00 07 00 00 00 20 00 00 00 03 00 00 00 "
        "00 05 04 00 05 00 00 00 09 00 00 00 78 56 34 12 ef cd ab 90 "
        "00 03 04 00 03 00 00 00 07 00 00 00 24 00 00 00 13 00 00 00"
    )
    assert tuple(decode_log_records(bytes(sink.data), logger.dictionary)) == (
        "[DEBUG] RUNTIME: event=2 function=7 pc=0x00000020 tick=3",
        "[DEBUG] RUNTIME: event=5 function=9 pc=0x12345678 tick=2427178479",
        "[DEBUG] RUNTIME: event=3 function=7 pc=0x00000024 tick=19",
    )
    assert logger.ring.is_empty()


if __name__ == "__main__":
    test_runtime_events_are_queued_as_fixed_dictionary_records()
    print("[PASS] test_runtime_events_are_queued_as_fixed_dictionary_records")
