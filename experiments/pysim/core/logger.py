"""
experiments/pysim/logger.py
Two independently-addressable output paths sharing one physical transport,
per docs/components/tier1_core/system_logging.md and the interface_wit.md
5.5 fix added this session:
- Logger: {DictionaryBasedIPC} structured logging. Can only ever emit a
  message whose *format string* was registered before this process started.
  There is no code path here that accepts an arbitrary runtime string --
  log_event()'s signature has no str parameter at all.
- ConsoleOutput: {WASI_ConsoleRawOutput}. Backs wasi:cli/stdout/stderr and
  carries whatever bytes the guest computed at runtime, bypassing the
  dictionary and the ring buffer entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Sequence

from system_containers import RingBuffer

if TYPE_CHECKING:
    from hal import UartTransport

# Matches a printf-style numeric conversion (%d, %08X, %u, ...) but not a
# literal "%%". Deliberately excludes %s/%p/%c: LogDictionary.register()
# rejects those outright, see the FINDING below.
_SPECIFIER_RE = re.compile(r"%(?:%|[-+0# ]*\d*(?:\.\d+)?[diouxX])")


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4


import bisect

_DISALLOWED_SPECIFIERS = ("%s", "%p", "%c")

from system_containers import FlatMapView

# Standard Diagnostic Log Event IDs (system_logging.md §4.2.1)
LOG_EVT_COOS_HANDOFF_LIMIT = 0x0101
LOG_EVT_COOS_TASK_CAPACITY = 0x0102
LOG_EVT_COOS_DUPLICATE_TASK = 0x0103
LOG_EVT_COOS_IRQ_OVERFLOW = 0x0104

LOG_EVT_IPC_RBAC_DENIED = 0x0201
LOG_EVT_IPC_UNKNOWN_URI = 0x0202
LOG_EVT_IPC_MSG_TOO_LARGE = 0x0203
LOG_EVT_IPC_INVALID_OWNERSHIP = 0x0204
LOG_EVT_IPC_CHANNEL_COLLISION = 0x0205

STANDARD_DIAGNOSTIC_EVENTS: list[tuple[int, str]] = [
    (LOG_EVT_COOS_HANDOFF_LIMIT, "COOS: handoff limit reached (task=%d, count=%d)"),
    (LOG_EVT_COOS_TASK_CAPACITY, "COOS: task capacity exceeded (max=%d, attempted=%d)"),
    (LOG_EVT_COOS_DUPLICATE_TASK, "COOS: duplicate task id rejected (task=%d)"),
    (LOG_EVT_COOS_IRQ_OVERFLOW, "COOS: irq queue overflow dropped (irq=%d, dropped_total=%d)"),
    (LOG_EVT_IPC_RBAC_DENIED, "IPC: rbac denied (sender_role=%d, target_role=%d)"),
    (LOG_EVT_IPC_UNKNOWN_URI, "IPC: unknown uri routing failed (uri_handle=%d)"),
    (LOG_EVT_IPC_MSG_TOO_LARGE, "IPC: message too large (kv_count=%d, max=%d)"),
    (LOG_EVT_IPC_INVALID_OWNERSHIP, "IPC: invalid ownership state (current_state=%d, op=%d)"),
    (LOG_EVT_IPC_CHANNEL_COLLISION, "IPC: channel waiter collision (channel=%d, dir=%d)"),
]


class LogDictionary:
    """
    ROM-resident, build-time-only format string table (system_logging.md 4.2).
    Storage ownership is separated: LogDictionary borrows entries storage
    and presents format strings via non-owning FlatMapView (AoS).
    """

    def __init__(
        self,
        storage: list[tuple[int, str]] | None = None,
        capacity: int = 128,
        include_diagnostic_events: bool = True,
    ):
        if storage is not None:
            self.storage = storage
        elif include_diagnostic_events:
            self.storage = sorted(STANDARD_DIAGNOSTIC_EVENTS, key=lambda x: x[0])
        else:
            self.storage = []
        self._view: FlatMapView[int, str] = FlatMapView(self.storage)
        self.payload: FlatMapView[int, str] = self._view

    def register(self, offset: int, fmt: str) -> None:
        for bad in _DISALLOWED_SPECIFIERS:
            if bad in fmt:
                raise ValueError(
                    f"dictionary entry 0x{offset:X} uses '{bad}', which cannot be "
                    "backed by a u32 argument without reading it as a pointer"
                )

        idx = bisect.bisect_left(self.storage, offset, key=lambda e: e[0])
        if idx < len(self.storage) and self.storage[idx][0] == offset:
            self.storage[idx] = (offset, fmt)
        else:
            self.storage.insert(idx, (offset, fmt))
        self._view = FlatMapView(self.storage)
        self.payload = self._view

    def view(self) -> FlatMapView[int, str]:
        return self._view

    @property
    def entries(self) -> Sequence[tuple[int, str]]:
        return self.storage

    def format(self, offset: int, args: tuple[int, int, int, int]) -> str:
        """
        FINDING: system_logging.md 4.2 says a format string may reference
                "最大4個" (up to 4) u32 args -- i.e. using fewer than 4 is normal and
                expected (most messages need 1-2). A real C `vsnprintf` silently
                ignores unused variadic arguments, but Python's `%` operator raises
                TypeError if the tuple is longer than the specifier count. A naive
                port of this component would crash on every log_event() call whose
                format string uses fewer than 4 specifiers -- i.e. almost all of
                them. This slices `args` down to the specifier count actually
                present so behavior matches C's variadic semantics instead of
                Python's stricter one.
        """
        fmt = self._view.find(offset)
        if fmt is None:
            return f"<UNKNOWN_DICT_OFFSET_0x{offset:X}>"
        n = sum(1 for m in _SPECIFIER_RE.finditer(fmt) if m.group() != "%%")
        return fmt % args[:n]


@dataclass
class LogEntry:
    level: LogLevel
    dict_offset: int
    args: tuple[int, int, int, int]
    tick: int


class Logger:
    """{BufferedLogging}: buffer now, flush during COOS idle_hook."""

    def __init__(
        self,
        transport: UartTransport,
        dictionary: LogDictionary,
        min_level: LogLevel = LogLevel.INFO,
        capacity: int = 16,
    ):
        self.transport = transport
        self.dictionary = dictionary
        self.min_level = min_level
        self.ring: RingBuffer[LogEntry] = RingBuffer(capacity)
        self._tick = 0

    def log_event(
        self,
        level: LogLevel,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> str:

        self._tick += 1
        if level < self.min_level:
            return "FILTERED"
        overwritten = self.ring.push(
            LogEntry(level, dict_offset, (arg0, arg1, arg2, arg3), self._tick)
        )
        return "OVERWRITTEN" if overwritten else "QUEUED"

    def flush(self) -> int:
        flushed = 0
        while not self.ring.is_empty():
            entry = self.ring.pop()
            assert entry is not None
            msg = self.dictionary.format(entry.dict_offset, entry.args)
            line = f"[{entry.level.name}][tick:{entry.tick}] {msg}\n"
            self.transport.write(line.encode("utf-8"))
            flushed += 1
        return flushed


class ConsoleOutput:
    """{WASI_ConsoleRawOutput}: raw bytes, no dictionary, no ring buffer."""

    def __init__(self, transport: UartTransport):
        self.transport = transport

    def write(self, data: bytes) -> int:
        return self.transport.write(data)
