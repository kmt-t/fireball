"""
docs/components/tier2_runtime/concepts/logging_concept.py
Reference Concept Implementation: Fireball Logger Component
Implementation Invariants & Gotchas:
- GOTCHA-LOG-01: Log API accepts only scalar u32 arguments and static dictionary offsets,
  completely eliminating runtime string pointers and Use-After-Free hazards.
- GOTCHA-LOG-02: Ring buffer safely overwrites oldest entries when full, preventing
  log-induced deadlocks and preserving system availability.
- GOTCHA-LOG-03: Log flush groups entries into DMA batches and verifies interrupt_pending
  only at batch boundaries (after each batch's dma_complete), never mid-batch, since a
  started DMA transfer cannot be preempted.
"""

import inspect
from base64 import b64encode
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

BACKS = ["components/tier2_runtime/runtime_logging.md"]


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4


@dataclass(slots=True)
class LogEntry:
    level: LogLevel = LogLevel.DEBUG
    dict_offset: int = 0
    arg0: int = 0
    arg1: int = 0
    arg2: int = 0
    arg3: int = 0

    def store(
        self,
        level: LogLevel,
        dict_offset: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
    ) -> None:
        self.level = level
        self.dict_offset = dict_offset
        self.arg0 = arg0
        self.arg1 = arg1
        self.arg2 = arg2
        self.arg3 = arg3


class LogResult(IntEnum):
    SUCCESS = 0
    FILTERED = 1
    OVERWRITTEN = 2


from docs.components.tier1_core.concepts.flat_view_concept import FlatMapView


def _is_one_of(value: str, options: str) -> bool:
    for index in range(len(options)):
        if value == options[index]:
            return True
    return False


def format_argument_count(fmt: str) -> int:
    """Validate the build-time numeric printf subset and count its arguments."""
    argument_count = 0
    index = 0
    while index < len(fmt):
        if fmt[index] != "%":
            index += 1
            continue
        index += 1
        assert index < len(fmt), "format string ends with '%'"
        if fmt[index] == "%":
            index += 1
            continue
        while index < len(fmt) and _is_one_of(fmt[index], "-+0 #"):
            index += 1
        while index < len(fmt) and fmt[index].isdigit():
            index += 1
        if index < len(fmt) and fmt[index] == ".":
            index += 1
            precision_start = index
            while index < len(fmt) and fmt[index].isdigit():
                index += 1
            assert index > precision_start, "printf precision requires digits"
        assert index < len(fmt), "incomplete printf conversion"
        assert _is_one_of(fmt[index], "diouxX"), f"unsupported printf conversion %{fmt[index]}"
        argument_count += 1
        assert argument_count <= 4, "log format may use at most four u32 arguments"
        index += 1
    return argument_count


class LogDictionary:
    """Simulates a ROM-resident static format string dictionary.
    Storage ownership is separated: borrows entries storage and performs lookup
    via non-owning FlatMapView.
    """

    def __init__(self, storage: list[tuple[int, str]] | None = None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = []

        self.argument_counts = bytes(format_argument_count(fmt) for _, fmt in self.storage)
        self.payload: FlatMapView = FlatMapView(self.storage)

    def argument_count(self, offset: int) -> int:
        index = bisect_left(self.storage, offset, key=lambda entry: entry[0])
        assert index < len(self.storage) and self.storage[index][0] == offset
        return self.argument_counts[index]

    def format(self, offset: int, args: tuple[int, ...]) -> str:
        """Host-side expansion only; Logger never calls this on the device path."""
        argument_count = self.argument_count(offset)
        fmt = self.payload.find(offset)
        assert fmt is not None
        assert len(args) >= argument_count
        return fmt % args[:argument_count]


class LogRingBuffer:
    """
    Fixed-size ring buffer with overwrite-oldest policy (BufferedLogging).
        Zero dynamic memory allocation; uses pre-allocated static storage.
    """

    def __init__(self, capacity: int = 8):
        assert (capacity & (capacity - 1)) == 0 and capacity > 0, "Capacity must be power of 2"
        self.capacity = capacity
        self.mask = capacity - 1
        self.buffer = [LogEntry() for _ in range(capacity)]
        self.head = 0  # Read index
        self.tail = 0  # Write index
        self.count = 0
        self.overwrite_count = 0

    def push(
        self,
        level: LogLevel,
        dict_offset: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
    ) -> LogResult:
        """Stores scalars in preallocated record storage, replacing oldest on full."""
        overwritten = self.count == self.capacity
        self.buffer[self.tail].store(level, dict_offset, arg0, arg1, arg2, arg3)
        self.tail = (self.tail + 1) & self.mask
        if overwritten:
            self.head = (self.head + 1) & self.mask
            self.overwrite_count += 1
            return LogResult.OVERWRITTEN
        self.count += 1
        return LogResult.SUCCESS

    def pop(self) -> LogEntry | None:
        """Dequeues oldest entry."""
        if self.count == 0:
            return None
        entry = self.buffer[self.head]
        self.head = (self.head + 1) & self.mask
        self.count -= 1
        return entry

    def peek(self) -> LogEntry | None:
        return None if self.count == 0 else self.buffer[self.head]

    def discard_oldest(self) -> None:
        assert self.count > 0
        self.head = (self.head + 1) & self.mask
        self.count -= 1

    def is_empty(self) -> bool:
        return self.count == 0

    def is_full(self) -> bool:
        return self.count == self.capacity


class MockHALTransport:
    """Simulates physical output transport (UART / DMA / ITM)."""

    def __init__(self):
        self.output_log: list[bytes] = []
        self.is_busy = False
        self.dma_active = False

    def transmit(self, raw_record: memoryview) -> bool:
        if self.is_busy:
            return False
        self.output_log.append(bytes(raw_record))
        return True


class Logger:
    """Fireball Logger Component (Tier 2 Runtime)."""

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
        self.wire_buffer = bytearray(20)
        self.wire_view = memoryview(self.wire_buffer)

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
    ) -> LogResult:
        """Logs an event via dictionary offset and up to 4 integer arguments."""
        assert 0 <= dict_offset <= 0x00FF_FFFF
        self.dictionary.argument_count(dict_offset)
        assert 0 <= arg0 <= 0xFFFF_FFFF
        assert 0 <= arg1 <= 0xFFFF_FFFF
        assert 0 <= arg2 <= 0xFFFF_FFFF
        assert 0 <= arg3 <= 0xFFFF_FFFF
        if level < self.min_level:
            return LogResult.FILTERED
        return self.ring_buffer.push(level, dict_offset, arg0, arg1, arg2, arg3)

    def _encode(self, entry: LogEntry) -> int:
        argument_count = self.dictionary.argument_count(entry.dict_offset)
        wire = self.wire_buffer
        wire[0] = int(entry.level)
        wire[1] = entry.dict_offset & 0xFF
        wire[2] = (entry.dict_offset >> 8) & 0xFF
        wire[3] = (entry.dict_offset >> 16) & 0xFF
        if argument_count > 0:
            self._write_u32(4, entry.arg0)
        if argument_count > 1:
            self._write_u32(8, entry.arg1)
        if argument_count > 2:
            self._write_u32(12, entry.arg2)
        if argument_count > 3:
            self._write_u32(16, entry.arg3)
        return 4 + 4 * argument_count

    def _write_u32(self, offset: int, value: int) -> None:
        wire = self.wire_buffer
        wire[offset] = value & 0xFF
        wire[offset + 1] = (value >> 8) & 0xFF
        wire[offset + 2] = (value >> 16) & 0xFF
        wire[offset + 3] = (value >> 24) & 0xFF

    def flush(
        self, batch_size: int = 32, interrupt_pending: Callable[[], bool] | None = None
    ) -> int:
        """Flushes buffered logs to HAL transport during COOS idle_hook.

        Entries are grouped into DMA batches of up to `batch_size`. A started DMA
        transfer cannot be preempted, so `interrupt_pending` is checked only after
        each batch completes (dma_complete), never while a batch is being collected
        or transmitted (GOTCHA-LOG-03). If interrupt_pending() is true after a batch,
        remaining entries stay buffered and control returns to the scheduler.
        """
        assert batch_size > 0
        total_flushed = 0
        while not self.ring_buffer.is_empty():
            if self.transport.is_busy or self.transport.dma_active:
                break
            batch_count = 0
            while not self.ring_buffer.is_empty() and batch_count < batch_size:
                entry = self.ring_buffer.peek()
                if entry is None:
                    break
                record_size = self._encode(entry)
                if not self.transport.transmit(self.wire_view[:record_size]):
                    break
                self.ring_buffer.discard_oldest()
                total_flushed += 1
                batch_count += 1

            if interrupt_pending and interrupt_pending():
                break

        return total_flushed


def decode_record(data: bytes, dictionary: LogDictionary) -> str:
    """完全な1レコードを物理出力前に復号する。"""
    assert len(data) >= 4
    record = memoryview(data)
    assert 0 <= record[0] <= int(LogLevel.FATAL)
    level = LogLevel(record[0])
    offset = record[1] | (record[2] << 8) | (record[3] << 16)
    record_size = 4 + 4 * dictionary.argument_count(offset)
    assert len(data) == record_size
    args = tuple(
        record[index]
        | (record[index + 1] << 8)
        | (record[index + 2] << 16)
        | (record[index + 3] << 24)
        for index in range(4, record_size, 4)
    )
    return f"[{level.name}] {dictionary.format(offset, args)}"


class PrintkTransport(MockHALTransport):
    """共有辞書を借用し、同期書込み時にUTF-8テキストを出力する。"""

    def __init__(self, dictionary: LogDictionary):
        super().__init__()
        self.dictionary = dictionary

    def transmit(self, raw_record: memoryview) -> bool:
        text = (decode_record(bytes(raw_record), self.dictionary) + "\n").encode("utf-8")
        return super().transmit(memoryview(text))

    def write_base64(self, data: memoryview) -> int:
        """20出力バイトに収まる15入力バイトずつ符号化し、LFを付ける。"""
        assert data.ndim == 1 and data.itemsize == 1 and data.format == "B"
        input_chunk_size = 20 // 4 * 3
        for offset in range(0, len(data), input_chunk_size):
            encoded = b64encode(bytes(data[offset : offset + input_chunk_size]))
            accepted = super().transmit(memoryview(encoded))
            assert accepted, "incomplete printk base64 write"
        accepted = super().transmit(memoryview(b"\n"))
        assert accepted, "incomplete printk base64 newline"
        return len(data)


def decode_transport(transport: MockHALTransport, dictionary: LogDictionary) -> tuple[str, ...]:
    """符号化の単体検証用に保存された内部レコードを復号する。"""
    return tuple(decode_record(data, dictionary) for data in transport.output_log)


# ==============================================================================
# Unit & Integration Tests
# ==============================================================================


def test_logger_dictionary_formatting() -> None:
    dictionary = LogDictionary(
        [
            (0x01, "System booted in %d ms (RAM free: %d bytes)"),
            (0x02, "Task %d created with priority %d"),
            (0x03, "IPC channel '%d' transfer error code: 0x%08X"),
        ]
    )
    msg = dictionary.format(0x01, (42, 23552, 0, 0))
    assert msg == "System booted in 42 ms (RAM free: 23552 bytes)"


def test_logger_buffering_and_idle_flush() -> None:
    dictionary = LogDictionary(
        [
            (0x10, "Task %d yield count: %d"),
            (0x20, "vMMIO read access to addr: 0x%08X (val: 0x%08X)"),
        ]
    )
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO, buffer_capacity=4)
    assert logger.log_event(LogLevel.INFO, 0x10, 1, 100) == LogResult.SUCCESS
    assert logger.log_event(LogLevel.WARN, 0x20, 0x80000000, 0x1234) == LogResult.SUCCESS
    assert len(transport.output_log) == 0
    flushed = logger.flush()
    assert flushed == 2
    assert len(transport.output_log) == 2
    output = decode_transport(transport, dictionary)
    assert output[0] == "[INFO] Task 1 yield count: 100"
    assert "[WARN] vMMIO read access to addr: 0x80000000 (val: 0x00001234)" in output[1]
    assert all(len(record) == 12 for record in transport.output_log)


def test_logger_overwrite_on_buffer_full() -> None:
    dictionary = LogDictionary(
        [
            (0x01, "Event #%d"),
        ]
    )
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.DEBUG, buffer_capacity=4)
    for i in range(1, 5):
        assert logger.log_event(LogLevel.INFO, 0x01, i) == LogResult.SUCCESS
    assert logger.ring_buffer.is_full()
    assert logger.log_event(LogLevel.INFO, 0x01, 5) == LogResult.OVERWRITTEN
    assert logger.log_event(LogLevel.INFO, 0x01, 6) == LogResult.OVERWRITTEN
    assert logger.ring_buffer.overwrite_count == 2
    flushed = logger.flush()
    assert flushed == 4
    output = decode_transport(transport, dictionary)
    assert "Event #3" in output[0]
    assert "Event #4" in output[1]
    assert "Event #5" in output[2]
    assert "Event #6" in output[3]


def test_logger_level_filtering() -> None:
    dictionary = LogDictionary([(0x01, "Log message")])
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.WARN, buffer_capacity=8)
    assert logger.log_event(LogLevel.DEBUG, 0x01) == LogResult.FILTERED
    assert logger.log_event(LogLevel.INFO, 0x01) == LogResult.FILTERED
    assert logger.log_event(LogLevel.WARN, 0x01) == LogResult.SUCCESS
    assert logger.log_event(LogLevel.ERROR, 0x01) == LogResult.SUCCESS
    flushed = logger.flush()
    assert flushed == 2
    assert len(transport.output_log) == 2


def test_logger_flush_interruption() -> None:
    """GOTCHA-LOG-03: interrupt_pending is checked only at batch boundaries
    (after a batch's dma_complete), never mid-batch or per entry."""
    dictionary = LogDictionary([(0x01, "Message %d")])
    transport = MockHALTransport()
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO, buffer_capacity=8)
    for i in range(4):
        logger.log_event(LogLevel.INFO, 0x01, i)

    call_count = 0

    def mock_interrupt() -> bool:
        nonlocal call_count
        call_count += 1
        return True  # interrupt is already pending once the first batch completes

    flushed = logger.flush(batch_size=2, interrupt_pending=mock_interrupt)
    assert flushed == 2, "only the first batch (2 entries) should be transmitted"
    assert logger.ring_buffer.count == 2, "the second batch's entries remain buffered"
    assert call_count == 1, "interrupt_pending must be checked once per batch, not per entry"


def test_logger_storage_ownership_separation() -> None:
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
    assert "External format: 100" in decode_transport(transport, dictionary)[0]


def test_logger_cannot_carry_a_runtime_string_but_console_can() -> None:
    """The internal log API carries only a dictionary offset and four scalars."""
    parameters = inspect.signature(Logger.log_event).parameters
    assert "dict_offset" in parameters
    assert "arg0" in parameters and "arg3" in parameters
    assert "message" not in parameters and "text" not in parameters


def test_printk_decodes_during_flush() -> None:
    dictionary = LogDictionary([(1, "value=%d hex=%08X")])
    transport = PrintkTransport(dictionary)
    logger = Logger(transport, dictionary)
    assert logger.log_event(LogLevel.INFO, 1, 42, 0xAB) == LogResult.SUCCESS
    assert transport.output_log == []
    assert logger.flush() == 1
    assert transport.output_log == [b"[INFO] value=42 hex=000000AB\n"]
    assert logger.ring_buffer.is_empty()


def test_printk_base64_uses_bounded_chunks() -> None:
    transport = PrintkTransport(LogDictionary())
    payload = b"f" * 16
    assert transport.write_base64(memoryview(payload)) == 16
    assert transport.output_log == [b"ZmZmZmZmZmZmZmZmZmZm", b"Zg==", b"\n"]
    transport.output_log.clear()
    assert transport.write_base64(memoryview(b"")) == 0
    assert transport.output_log == [b"\n"]
    transport.is_busy = True
    rejected = False
    try:
        transport.write_base64(memoryview(b"f"))
    except AssertionError:
        rejected = True
    assert rejected, "Base64 output rejection must assert"
    assert transport.output_log == [b"\n"]


if __name__ == "__main__":
    test_printk_base64_uses_bounded_chunks()
    test_printk_decodes_during_flush()
    test_logger_dictionary_formatting()
    test_logger_buffering_and_idle_flush()
    test_logger_overwrite_on_buffer_full()
    test_logger_level_filtering()
    test_logger_flush_interruption()
    test_logger_storage_ownership_separation()
    test_logger_cannot_carry_a_runtime_string_but_console_can()
    print("[PASS] All Logger concept tests passed successfully.")
