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

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import re
from dataclasses import dataclass
from enum import IntEnum

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


_DISALLOWED_SPECIFIERS = ("%s", "%p", "%c")

from system_containers import FlatMapView, StaticFlatMap


class LogDictionary:
    """
    ROM-resident, build-time-only format string table (system_logging.md 4.2)
        backed by FlatMapView vocabulary.
    """

    def __init__(self, capacity: int = 128):
        self._map: StaticFlatMap[int, str] = StaticFlatMap(capacity)

    def register(self, offset: int, fmt: str) -> None:
        for bad in _DISALLOWED_SPECIFIERS:
            if bad in fmt:
                raise ValueError(
                    f"dictionary entry 0x{offset:X} uses '{bad}', which cannot be "
                    "backed by a u32 argument without reading it as a pointer"
                )

        self._map.insert(offset, fmt)

    def view(self) -> FlatMapView[int, str]:
        return self._map.view()

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
        fmt = self._map.find(offset)
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


class RingBuffer:
    """Fixed-capacity, overwrite-oldest-on-full (system_logging.md 4.1 "FINALIZED: Overwrite")."""

    def __init__(self, capacity: int = 16):
        assert capacity & (capacity - 1) == 0, "capacity must be a power of two"
        self._buf: list[LogEntry | None] = [None] * capacity
        self._mask = capacity - 1
        self._head = 0
        self._tail = 0
        self._count = 0
        self.overwrite_count = 0

    def push(self, entry: LogEntry) -> bool:
        overwritten = self._count == len(self._buf)
        if overwritten:
            self._tail = (self._tail + 1) & self._mask
            self._count -= 1
            self.overwrite_count += 1

        self._buf[self._head] = entry
        self._head = (self._head + 1) & self._mask
        self._count += 1
        return overwritten

    def pop(self) -> LogEntry | None:
        if self._count == 0:
            return None
        entry = self._buf[self._tail]
        self._buf[self._tail] = None
        self._tail = (self._tail + 1) & self._mask
        self._count -= 1
        return entry

    def is_empty(self) -> bool:
        return self._count == 0


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
        self.ring = RingBuffer(capacity)
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
