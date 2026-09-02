"""
docs/components/tier1_core/concepts/logging_concept.py
Reference Concept Implementation: Fireball Logger Component
- Dictionary-based IPC logging ({DictionaryBasedIPC}): ROM-based format string dictionary
- Fixed-capacity circular ring buffer ({BufferedLogging}): Overwrite-oldest on full
- Deferred DMA output & COOS Idle Hook integration ({GLOBAL_IdleDetection})
- Memory isolation & zero dynamic allocation ({MemoryIsolation}, {META_ConfigurableSystem})
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4


@dataclass(frozen=True)
class LogEntry:
    level: LogLevel
    dict_offset: int
    arg0: int = 0
    arg1: int = 0
    arg2: int = 0
    arg3: int = 0
    timestamp_tick: int = 0


import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from flat_view_concept import FlatMapView


class LogDictionary:
    """Simulates ROM-resident static format string dictionary (DictionaryBasedIPC).
    Storage ownership is separated: borrows entries storage and performs lookup
    via non-owning FlatMapView (AoS).
    """

    def __init__(self, storage: list[tuple[int, str]] | None = None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = []

        self.payload: FlatMapView = FlatMapView(self.storage)

    def format(self, offset: int, arg0: int, arg1: int, arg2: int, arg3: int) -> str:
        fmt = self.payload.find(offset)
        if fmt is None:
            fmt = f"UNKNOWN_FORMAT_OFFSET_{offset}: %d %d %d %d"
        try:
            return fmt % (arg0, arg1, arg2, arg3)
        except TypeError:
            return fmt % (arg0, arg1, arg2, arg3)[: fmt.count("%")]


class LogRingBuffer:
    """
    Fixed-size ring buffer with overwrite-oldest policy (BufferedLogging).
        Zero dynamic memory allocation; uses pre-allocated static storage.
    """

    def __init__(self, capacity: int = 8):
        assert (capacity & (capacity - 1)) == 0 and capacity > 0, "Capacity must be power of 2"
        self.capacity = capacity
        self.mask = capacity - 1
        self.buffer: list[LogEntry | None] = [None] * capacity
        self.head = 0  # Write index
        self.tail = 0  # Read index
        self.count = 0
        self.overwrite_count = 0

    def push(self, entry: LogEntry) -> bool:
        """Enqueues entry. If buffer is full, overwrites oldest entry (tail advanced)."""
        overwritten = False
        if self.count == self.capacity:
            self.tail = (self.tail + 1) & self.mask
            self.count -= 1
            self.overwrite_count += 1
            overwritten = True

        self.buffer[self.head] = entry
        self.head = (self.head + 1) & self.mask
        self.count += 1
        return overwritten

    def pop(self) -> LogEntry | None:
        """Dequeues oldest entry."""
        if self.count == 0:
            return None
        entry = self.buffer[self.tail]
        self.buffer[self.tail] = None
        self.tail = (self.tail + 1) & self.mask
        self.count -= 1
        return entry

    def is_empty(self) -> bool:
        return self.count == 0

    def is_full(self) -> bool:
        return self.count == self.capacity


class MockHALTransport:
    """Simulates physical output transport (UART / DMA / ITM)."""

    def __init__(self):
        self.output_log: list[str] = []
        self.is_busy = False
        self.dma_active = False

    def transmit(self, formatted_message: str) -> bool:
        if self.is_busy:
            return False
        self.output_log.append(formatted_message)
        return True

    def start_dma(self, formatted_messages: list[str]) -> bool:
        if self.is_busy or self.dma_active:
            return False
        self.dma_active = True
        self.output_log.extend(formatted_messages)
        self.dma_active = False
        return True


class Logger:
    """Fireball Logger Component (Tier 1 Primary Component)."""

    def __init__(
        self,
        transport: MockHALTransport,
        dictionary: LogDictionary,
        min_level: LogLevel = LogLevel.INFO,
        buffer_capacity: int = 16,
    ):
        self.transport = transport
        self.dictionary = dictionary
        self.min_level = min_level
        self.ring_buffer = LogRingBuffer(capacity=buffer_capacity)
        self.tick_counter = 0

    def set_min_level(self, level: LogLevel) -> None:
        self.min_level = level

    def log_event(
        self,
        level: LogLevel,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> str:
        """Logs an event via dictionary offset and up to 4 integer arguments."""
        self.tick_counter += 1
        if level < self.min_level:
            return "FILTERED"
        entry = LogEntry(
            level=level,
            dict_offset=dict_offset,
            arg0=arg0,
            arg1=arg1,
            arg2=arg2,
            arg3=arg3,
            timestamp_tick=self.tick_counter,
        )
        overwritten = self.ring_buffer.push(entry)
        return "OVERWRITTEN" if overwritten else "QUEUED"

    def handle_ipc_message(self, message_payload: dict[str, int]) -> dict[str, str]:
        """Handles fireball://logging/system/0 IPC requests."""
        level = LogLevel(message_payload.get("level", int(LogLevel.INFO)))
        dict_offset = int(message_payload.get("dict_offset", 0))
        arg0 = int(message_payload.get("arg0", 0))
        arg1 = int(message_payload.get("arg1", 0))
        arg2 = int(message_payload.get("arg2", 0))
        arg3 = int(message_payload.get("arg3", 0))
        status = self.log_event(level, dict_offset, arg0, arg1, arg2, arg3)
        return {"status": "SUCCESS", "detail": status}

    def flush(
        self, max_batch: int = 32, interrupt_pending: Callable[[], bool] | None = None
    ) -> int:
        """Flushes buffered logs to HAL transport during COOS idle_hook."""
        flushed_count = 0
        batch: list[str] = []
        while not self.ring_buffer.is_empty() and flushed_count < max_batch:
            if interrupt_pending and interrupt_pending():
                break
            entry = self.ring_buffer.pop()
            if entry is None:
                break
            msg = self.dictionary.format(
                entry.dict_offset, entry.arg0, entry.arg1, entry.arg2, entry.arg3
            )
            formatted = f"[{entry.level.name}][tick:{entry.timestamp_tick}] {msg}"
            batch.append(formatted)
            flushed_count += 1

        if batch:
            self.transport.start_dma(batch)
        return flushed_count


# ==============================================================================
# Unit & Integration Tests
# ==============================================================================


def test_logger_dictionary_formatting():
    dictionary = LogDictionary(
        [
            (0x01, "System booted in %d ms (RAM free: %d bytes)"),
            (0x02, "Task %d created with priority %d"),
            (0x03, "IPC channel '%d' transfer error code: 0x%08X"),
        ]
    )
    msg = dictionary.format(0x01, 42, 21504, 0, 0)
    assert msg == "System booted in 42 ms (RAM free: 21504 bytes)"


def test_logger_buffering_and_idle_flush():
    dictionary = LogDictionary(
        [
            (0x10, "Task %d yield count: %d"),
            (0x20, "vMMIO read access to addr: 0x%08X (val: 0x%08X)"),
        ]
    )
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO, buffer_capacity=4)
    assert logger.log_event(LogLevel.INFO, 0x10, 1, 100) == "QUEUED"
    assert logger.log_event(LogLevel.WARN, 0x20, 0x80000000, 0x1234) == "QUEUED"
    assert len(transport.output_log) == 0
    flushed = logger.flush()
    assert flushed == 2
    assert len(transport.output_log) == 2
    assert "[INFO][tick:1] Task 1 yield count: 100" in transport.output_log[0]
    assert (
        "[WARN][tick:2] vMMIO read access to addr: 0x80000000 (val: 0x00001234)"
        in transport.output_log[1]
    )


def test_logger_overwrite_on_buffer_full():
    dictionary = LogDictionary(
        [
            (0x01, "Event #%d"),
        ]
    )
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.DEBUG, buffer_capacity=4)
    for i in range(1, 5):
        assert logger.log_event(LogLevel.INFO, 0x01, i) == "QUEUED"
    assert logger.ring_buffer.is_full()
    assert logger.log_event(LogLevel.INFO, 0x01, 5) == "OVERWRITTEN"
    assert logger.log_event(LogLevel.INFO, 0x01, 6) == "OVERWRITTEN"
    assert logger.ring_buffer.overwrite_count == 2
    flushed = logger.flush()
    assert flushed == 4
    assert "Event #3" in transport.output_log[0]
    assert "Event #4" in transport.output_log[1]
    assert "Event #5" in transport.output_log[2]
    assert "Event #6" in transport.output_log[3]


def test_logger_level_filtering():
    dictionary = LogDictionary([(0x01, "Log message")])
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.WARN, buffer_capacity=8)
    assert logger.log_event(LogLevel.DEBUG, 0x01) == "FILTERED"
    assert logger.log_event(LogLevel.INFO, 0x01) == "FILTERED"
    assert logger.log_event(LogLevel.WARN, 0x01) == "QUEUED"
    assert logger.log_event(LogLevel.ERROR, 0x01) == "QUEUED"
    flushed = logger.flush()
    assert flushed == 2
    assert len(transport.output_log) == 2


def test_logger_ipc_message_handling():
    dictionary = LogDictionary([(0x50, "Guest VM %d trap occurred (cause: %d)")])
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO, buffer_capacity=8)
    resp = logger.handle_ipc_message(
        {
            "level": int(LogLevel.ERROR),
            "dict_offset": 0x50,
            "arg0": 1,
            "arg1": 3,
        }
    )
    assert resp["status"] == "SUCCESS"
    assert resp["detail"] == "QUEUED"
    logger.flush()
    assert len(transport.output_log) == 1
    assert "Guest VM 1 trap occurred (cause: 3)" in transport.output_log[0]


def test_logger_flush_interruption():
    dictionary = LogDictionary([(0x01, "Message %d")])
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO, buffer_capacity=8)
    for i in range(4):
        logger.log_event(LogLevel.INFO, 0x01, i)

    pop_count = 0

    def mock_interrupt():
        nonlocal pop_count
        pop_count += 1
        return pop_count > 2

    flushed = logger.flush(interrupt_pending=mock_interrupt)
    assert flushed == 2
    assert logger.ring_buffer.count == 2


def test_logger_storage_ownership_separation():
    # Storage is owned externally in ROM / static buffer
    storage = [(0x10, "External format: %d"), (0x20, "Status code: %d")]
    dictionary = LogDictionary(storage)

    # Ownership assertion: LogDictionary does not own or clone storage
    assert dictionary.storage is storage
    assert dictionary.payload.entries is storage

    transport = MockHALTransport()
    logger = Logger(transport, dictionary)
    logger.log_event(LogLevel.INFO, 0x10, 100)
    logger.flush()
    assert "External format: 100" in transport.output_log[0]


if __name__ == "__main__":
    test_logger_dictionary_formatting()
    test_logger_buffering_and_idle_flush()
    test_logger_overwrite_on_buffer_full()
    test_logger_level_filtering()
    test_logger_ipc_message_handling()
    test_logger_flush_interruption()
    test_logger_storage_ownership_separation()
    print("[PASS] All Logger concept tests passed successfully.")
