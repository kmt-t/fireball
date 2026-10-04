"""
experiments/pysim/tier2_runtime/observability/logger.py
Fireball System Logging Engine mirroring docs/components/tier2_runtime/runtime_logging.md:
- GOTCHA-LOG-01: Format strings are registered statically in LogDictionary. Log API accepts
  only scalar u32 arguments, completely eliminating runtime string pointers and Use-After-Free.
- GOTCHA-LOG-02: Bounded ring buffer safely overwrites oldest entries on full, maintaining
  system non-blocking invariant and preventing log-induced deadlocks.
- GOTCHA-LOG-03: Log flush groups entries into batches and checks interrupt_pending only at
  batch boundaries, never mid-batch, since a started transfer cannot be preempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Callable, Sequence

from config import FB_CONF_LOG_DICT_MAX_ENTRIES
from system_containers import ReadOnlyFlatMapStorage, ReadOnlyFlatMapView, StaticVector
from tier1_core.printk import (
    PRINTK_DICTIONARY,
    PRINTK_HEADER_SIZE,
    PRINTK_MAX_RECORD_SIZE,
    _is_one_of,
    format_argument_count,
)
from tier2_runtime.observability.logging_interface import LoggerPort, LogLevel, LogResult

if TYPE_CHECKING:
    from tier1_core.printk import PrintkWriter

LOG_HEADER_SIZE = PRINTK_HEADER_SIZE
LOG_MAX_RECORD_SIZE = PRINTK_MAX_RECORD_SIZE
_MAX_DICTIONARY_ID = 0x00FF_FFFF
_UINT32_MAX = 0xFFFF_FFFF


# Tier 2 (interpreter.py) owns TrapCode and computes these same offsets as
# LOG_EVT_TRAP_BASE + TrapCode value (interpreter.TRAP_LOG_EVENTS). The IDs and
# format strings are mirrored here to avoid importing the interpreter.
LOG_EVT_TRAP_BASE = 0x0300
LOG_EVT_TRAP_LOCAL_STACK_CAPACITY = 0x0301
LOG_EVT_TRAP_CALL_FRAME_CAPACITY = 0x0302
LOG_EVT_TRAP_CALL_STACK_CAPACITY = 0x0303
LOG_EVT_TRAP_OPERAND_STACK_CAPACITY = 0x0304
LOG_EVT_TRAP_NO_HOST_HANDLER = 0x0305
LOG_EVT_TRAP_TABLE_INDEX_OUT_OF_BOUNDS = 0x0306
LOG_EVT_TRAP_TABLE_SLOT_UNINITIALIZED = 0x0307
LOG_EVT_TRAP_INDIRECT_CALL_TYPE_MISMATCH = 0x0308
LOG_EVT_TRAP_UNREACHABLE = 0x0309
LOG_EVT_TRAP_CONTROL_FRAME_CAPACITY = 0x030A
LOG_EVT_TRAP_VMMIO_NOT_CONFIGURED = 0x030B
LOG_EVT_TRAP_VMMIO_ACCESS = 0x030C
LOG_EVT_TRAP_MEMORY_OUT_OF_BOUNDS = 0x030D
LOG_EVT_TRAP_MEMORY_SECTION_MISSING = 0x030E
LOG_EVT_TRAP_INTEGER_DIVIDE_BY_ZERO = 0x030F
LOG_EVT_TRAP_INTEGER_OVERFLOW = 0x0310
LOG_EVT_TRAP_INVALID_CONVERSION = 0x0311

STANDARD_DIAGNOSTIC_EVENTS: tuple[tuple[int, str], ...] = (
    (LOG_EVT_TRAP_LOCAL_STACK_CAPACITY, "TRAP: local stack capacity exceeded (pc=0x%08X)"),
    (LOG_EVT_TRAP_CALL_FRAME_CAPACITY, "TRAP: call frame capacity exceeded (pc=0x%08X)"),
    (LOG_EVT_TRAP_CALL_STACK_CAPACITY, "TRAP: call stack capacity exceeded (pc=0x%08X)"),
    (LOG_EVT_TRAP_OPERAND_STACK_CAPACITY, "TRAP: operand stack capacity exceeded (pc=0x%08X)"),
    (LOG_EVT_TRAP_NO_HOST_HANDLER, "TRAP: import has no bound host handler (pc=0x%08X, func=%d)"),
    (
        LOG_EVT_TRAP_TABLE_INDEX_OUT_OF_BOUNDS,
        "TRAP: table index out of bounds (pc=0x%08X, slot=%d)",
    ),
    (LOG_EVT_TRAP_TABLE_SLOT_UNINITIALIZED, "TRAP: table slot uninitialized (pc=0x%08X, slot=%d)"),
    (
        LOG_EVT_TRAP_INDIRECT_CALL_TYPE_MISMATCH,
        "TRAP: indirect call type mismatch (pc=0x%08X, slot=%d)",
    ),
    (LOG_EVT_TRAP_UNREACHABLE, "TRAP: unreachable instruction executed (pc=0x%08X)"),
    (LOG_EVT_TRAP_CONTROL_FRAME_CAPACITY, "TRAP: control frame capacity exceeded (pc=0x%08X)"),
    (
        LOG_EVT_TRAP_VMMIO_NOT_CONFIGURED,
        "TRAP: vMMIO region not configured (pc=0x%08X, addr=0x%08X)",
    ),
    (LOG_EVT_TRAP_VMMIO_ACCESS, "TRAP: vMMIO access rejected (pc=0x%08X, status=%d)"),
    (
        LOG_EVT_TRAP_MEMORY_OUT_OF_BOUNDS,
        "TRAP: linear memory access out of bounds (pc=0x%08X, addr=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_MEMORY_SECTION_MISSING,
        "TRAP: memory access without a declared memory section (pc=0x%08X)",
    ),
    (LOG_EVT_TRAP_INTEGER_DIVIDE_BY_ZERO, "TRAP: integer divide by zero (pc=0x%08X)"),
    (LOG_EVT_TRAP_INTEGER_OVERFLOW, "TRAP: integer division overflow (pc=0x%08X)"),
    (LOG_EVT_TRAP_INVALID_CONVERSION, "TRAP: invalid float-to-integer conversion (pc=0x%08X)"),
)

RUNTIME_EVENT_LOG_BASE = 0x0400
RUNTIME_EVENT_DICTIONARY: tuple[tuple[int, str], ...] = (
    (0x0401, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0402, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0403, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0404, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0405, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0406, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0407, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0408, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x0409, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x040A, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
    (0x040B, "RUNTIME: event=%d function=%d pc=0x%08X tick=%d"),
)


class LogDictionary:
    """
    ROM-resident, build-time-only format string table (runtime_logging.md 4.2).
    Storage ownership is separated: LogDictionary borrows entries storage
    and presents format strings via a non-owning ReadOnlyFlatMapView (AoS).
    """

    __slots__ = ("_argument_counts", "_view", "payload", "storage")

    def __init__(
        self,
        storage: ReadOnlyFlatMapStorage[int, str] | None = None,
        entries: tuple[tuple[int, str], ...] = (),
        include_diagnostic_events: bool = True,
    ):
        assert storage is None or not entries
        if storage is None:
            extra_count = (
                len(PRINTK_DICTIONARY)
                + len(STANDARD_DIAGNOSTIC_EVENTS)
                + len(RUNTIME_EVENT_DICTIONARY)
            )
            if not include_diagnostic_events:
                extra_count = 0
            entry_count = len(entries) + extra_count
            assert entry_count <= FB_CONF_LOG_DICT_MAX_ENTRIES
            built_entries: StaticVector[tuple[int, str]] = StaticVector(capacity=entry_count)
            for entry in entries:
                built_entries.append(entry)
            if include_diagnostic_events:
                for entry in PRINTK_DICTIONARY:
                    built_entries.append(entry)
                for entry in STANDARD_DIAGNOSTIC_EVENTS:
                    built_entries.append(entry)
                for entry in RUNTIME_EVENT_DICTIONARY:
                    built_entries.append(entry)
            built_entries.sort(key=lambda entry: entry[0])
            previous_id: int | None = None
            for event_id, _fmt in built_entries:
                assert 0 <= event_id <= _MAX_DICTIONARY_ID
                assert previous_id is None or previous_id < event_id
                previous_id = event_id
            self.storage = ReadOnlyFlatMapStorage.from_sorted_static_entries(built_entries)
        else:
            self.storage = storage
            assert len(storage.entries) <= FB_CONF_LOG_DICT_MAX_ENTRIES
            for event_id, _fmt in storage.entries:
                assert 0 <= event_id <= _MAX_DICTIONARY_ID
        # Build-time metadata: one byte per existing dictionary entry, with no ID copy.
        self._argument_counts = bytes(format_argument_count(fmt) for _, fmt in self.storage.entries)
        self._view: ReadOnlyFlatMapView[int, str] = self.storage.view()
        self.payload: ReadOnlyFlatMapView[int, str] = self._view

    def view(self) -> ReadOnlyFlatMapView[int, str]:
        return self._view

    @property
    def entries(self) -> Sequence[tuple[int, str]]:
        return self.storage.entries

    def argument_count(self, offset: int) -> int:
        """Look up immutable build-time metadata without reading format strings."""
        index = self._view.find_index(offset)
        assert index >= 0, f"unregistered dictionary ID 0x{offset:X}"
        return self._argument_counts[index]

    def format(self, offset: int, args: Sequence[int]) -> str:
        """Host-side expansion for one fixed dictionary ID and at most four values."""
        fmt = self._view.find(offset)
        assert fmt is not None, f"unregistered dictionary ID 0x{offset:X}"
        format_argument_count(fmt)
        formatted = ""
        literal_start = 0
        index = 0
        argument_index = 0
        while index < len(fmt):
            if fmt[index] != "%":
                index += 1
                continue
            formatted += fmt[literal_start:index]
            index += 1
            if fmt[index] == "%":
                formatted += "%"
                index += 1
                literal_start = index
                continue
            conversion_start = index - 1
            while index < len(fmt) and _is_one_of(fmt[index], "-+0 #"):
                index += 1
            while index < len(fmt) and fmt[index].isdigit():
                index += 1
            if index < len(fmt) and fmt[index] == ".":
                index += 1
                while index < len(fmt) and fmt[index].isdigit():
                    index += 1
            assert argument_index < len(args)
            formatted += fmt[conversion_start : index + 1] % args[argument_index]
            argument_index += 1
            index += 1
            literal_start = index
        return formatted + fmt[literal_start:]


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


class LogRingBuffer:
    """Preallocated fixed-capacity ring that reuses scalar log records."""

    __slots__ = ("buf", "capacity", "count", "dropped", "head")

    def __init__(self, capacity: int):
        assert capacity > 0
        self.capacity = capacity
        self.buf: StaticVector[LogEntry] = StaticVector(capacity=capacity)
        for _ in range(capacity):
            self.buf.append(LogEntry())
        self.count = 0
        self.dropped = 0
        self.head = 0

    @property
    def overwrite_count(self) -> int:
        return self.dropped

    def __len__(self) -> int:
        return self.count

    def is_empty(self) -> bool:
        return self.count == 0

    def push(
        self,
        level: LogLevel,
        dict_offset: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
    ) -> LogResult:
        overwritten = self.count == self.capacity
        if overwritten:
            index = self.head
            self.head = (self.head + 1) % self.capacity
            self.dropped += 1
        else:
            index = (self.head + self.count) % self.capacity
            self.count += 1
        self.buf[index].store(level, dict_offset, arg0, arg1, arg2, arg3)
        return LogResult.OVERWRITTEN if overwritten else LogResult.SUCCESS

    def peek(self) -> LogEntry | None:
        return None if self.count == 0 else self.buf[self.head]

    def discard_oldest(self) -> None:
        assert self.count > 0
        self.head = (self.head + 1) % self.capacity
        self.count -= 1


class Logger(LoggerPort):
    """{BufferedLogging}: buffer now, flush during COOS idle_hook."""

    __slots__ = ("_wire_buffer", "_wire_view", "dictionary", "min_level", "ring", "transport")

    def __init__(
        self,
        transport: PrintkWriter,
        dictionary: LogDictionary,
        min_level: LogLevel = LogLevel.INFO,
        capacity: int = 16,
    ):
        self.transport = transport
        self.dictionary = dictionary
        self.min_level = min_level
        self.ring = LogRingBuffer(capacity)
        self._wire_buffer = bytearray(LOG_MAX_RECORD_SIZE)
        self._wire_view = memoryview(self._wire_buffer)

    def log_event(
        self,
        level: IntEnum,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> LogResult:
        assert 0 <= int(level) <= int(LogLevel.FATAL)
        level = LogLevel(int(level))
        assert 0 <= dict_offset <= _MAX_DICTIONARY_ID
        self.dictionary.argument_count(dict_offset)
        assert 0 <= arg0 <= _UINT32_MAX
        assert 0 <= arg1 <= _UINT32_MAX
        assert 0 <= arg2 <= _UINT32_MAX
        assert 0 <= arg3 <= _UINT32_MAX
        if level < self.min_level:
            return LogResult.FILTERED
        return self.ring.push(level, dict_offset, arg0, arg1, arg2, arg3)

    def _encode_entry(self, entry: LogEntry) -> int:
        argument_count = self.dictionary.argument_count(entry.dict_offset)
        wire = self._wire_buffer
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
        return LOG_HEADER_SIZE + 4 * argument_count

    def _write_u32(self, offset: int, value: int) -> None:
        wire = self._wire_buffer
        wire[offset] = value & 0xFF
        wire[offset + 1] = (value >> 8) & 0xFF
        wire[offset + 2] = (value >> 16) & 0xFF
        wire[offset + 3] = (value >> 24) & 0xFF

    def flush(
        self, batch_size: int = 32, interrupt_pending: Callable[[], bool] | None = None
    ) -> int:
        """GOTCHA-LOG-03: entries are grouped into batches of up to `batch_size`.
        A started transfer cannot be preempted, so `interrupt_pending` (if given)
        is checked only after each batch completes, never mid-batch."""
        assert batch_size > 0
        flushed = 0
        while not self.ring.is_empty():
            batch_count = 0
            while not self.ring.is_empty() and batch_count < batch_size:
                entry = self.ring.peek()
                assert entry is not None
                record_size = self._encode_entry(entry)
                written = self.transport.write(self._wire_view[:record_size])
                assert written == record_size
                self.ring.discard_oldest()
                flushed += 1
                batch_count += 1
            if interrupt_pending is not None and interrupt_pending():
                break
        return flushed


def decode_log_records(data: bytes, dictionary: LogDictionary) -> StaticVector[str]:
    """Decode dictionary-sized records; unknown IDs cannot establish a boundary."""
    messages: StaticVector[str] = StaticVector(capacity=len(data) // LOG_HEADER_SIZE)
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= LOG_HEADER_SIZE, "truncated log header"
        record = memoryview(data)[offset:]
        assert 0 <= record[0] <= int(LogLevel.FATAL), "invalid log level"
        level = LogLevel(record[0])
        dict_offset = record[1] | (record[2] << 8) | (record[3] << 16)
        argument_count = dictionary.argument_count(dict_offset)
        record_size = LOG_HEADER_SIZE + 4 * argument_count
        assert len(record) >= record_size, "truncated log arguments"
        args: StaticVector[int] = StaticVector(capacity=argument_count)
        for arg_offset in range(LOG_HEADER_SIZE, record_size, 4):
            args.append(
                record[arg_offset]
                | (record[arg_offset + 1] << 8)
                | (record[arg_offset + 2] << 16)
                | (record[arg_offset + 3] << 24)
            )
        messages.append(f"[{level.name}] {dictionary.format(dict_offset, args)}")
        offset += record_size
    return messages


class ConsoleOutput:
    """Console raw-byte output path (interface_wit.md "console-output"): no dictionary, no ring buffer."""

    __slots__ = ("transport",)

    def __init__(self, transport: PrintkWriter):
        self.transport = transport

    def write(self, data: memoryview) -> int:
        return self.transport.write(data)
