"""
experiments/pysim/tier1_core/logger.py
Fireball System Logging Engine mirroring docs/components/tier2_runtime/runtime_logging.md:
- GOTCHA-LOG-01: Format strings are registered statically in LogDictionary. Log API accepts
  only scalar u32 arguments, completely eliminating runtime string pointers and Use-After-Free.
- GOTCHA-LOG-02: Bounded ring buffer safely overwrites oldest entries on full, maintaining
  system non-blocking invariant and preventing log-induced deadlocks.
- GOTCHA-LOG-03: Log flush groups entries into batches and checks interrupt_pending only at
  batch boundaries, never mid-batch, since a started transfer cannot be preempted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Callable, Sequence

from system_containers import MutableFlatMapStorage, ReadOnlyFlatMapView, RingBuffer

if TYPE_CHECKING:
    from hal_dispatch import StreamSink

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

# Standard Diagnostic Log Event IDs (runtime_logging.md §4.2.1)
LOG_EVT_COOS_HANDOFF_LIMIT = 0x0101
LOG_EVT_COOS_TASK_CAPACITY = 0x0102
LOG_EVT_COOS_DUPLICATE_TASK = 0x0103
LOG_EVT_COOS_IRQ_OVERFLOW = 0x0104

LOG_EVT_IPC_RBAC_DENIED = 0x0201
LOG_EVT_IPC_UNKNOWN_URI = 0x0202
LOG_EVT_IPC_MSG_TOO_LARGE = 0x0203
LOG_EVT_IPC_INVALID_OWNERSHIP = 0x0204
LOG_EVT_IPC_CHANNEL_COLLISION = 0x0205

# Tier 2 (interpreter.py) owns TrapCode and computes these same offsets as
# LOG_EVT_TRAP_BASE + TrapCode value (interpreter.TRAP_LOG_EVENTS). The IDs and
# format strings are mirrored here rather than imported, matching the COOS/IPC
# duplication above: interpreter.py already imports this module for Logger, so
# an import in the other direction would be circular.
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
    (LOG_EVT_COOS_HANDOFF_LIMIT, "COOS: handoff limit reached (task=%d, count=%d)"),
    (LOG_EVT_COOS_TASK_CAPACITY, "COOS: task capacity exceeded (max=%d, attempted=%d)"),
    (LOG_EVT_COOS_DUPLICATE_TASK, "COOS: duplicate task id rejected (task=%d)"),
    (LOG_EVT_COOS_IRQ_OVERFLOW, "COOS: irq queue overflow dropped (irq=%d, dropped_total=%d)"),
    (LOG_EVT_IPC_RBAC_DENIED, "IPC: rbac denied (sender_role=%d, target_role=%d)"),
    (LOG_EVT_IPC_UNKNOWN_URI, "IPC: unknown uri routing failed (uri_handle=%d)"),
    (LOG_EVT_IPC_MSG_TOO_LARGE, "IPC: message too large (kv_count=%d, max=%d)"),
    (LOG_EVT_IPC_INVALID_OWNERSHIP, "IPC: invalid ownership state (current_state=%d, op=%d)"),
    (LOG_EVT_IPC_CHANNEL_COLLISION, "IPC: channel waiter collision (channel=%d, dir=%d)"),
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


class LogDictionary:
    """
    ROM-resident, build-time-only format string table (runtime_logging.md 4.2).
    Storage ownership is separated: LogDictionary borrows entries storage
    and presents format strings via a non-owning ReadOnlyFlatMapView (AoS).
    """

    def __init__(
        self,
        storage: MutableFlatMapStorage[int, str] | None = None,
        capacity: int = 128,
        include_diagnostic_events: bool = True,
    ):
        self.storage = storage if storage is not None else MutableFlatMapStorage(capacity=capacity)
        if include_diagnostic_events and storage is None:
            for event_id, fmt in STANDARD_DIAGNOSTIC_EVENTS:
                assert self.storage.insert(event_id, fmt)
        self._view: ReadOnlyFlatMapView[int, str] = self.storage.view()
        self.payload: ReadOnlyFlatMapView[int, str] = self._view

    def register(self, offset: int, fmt: str) -> None:
        for bad in _DISALLOWED_SPECIFIERS:
            if fmt.find(bad) >= 0:
                assert False, (
                    f"dictionary entry 0x{offset:X} uses '{bad}', which cannot be "
                    "backed by a u32 argument without reading it as a pointer"
                )

        assert self.storage.insert(offset, fmt)
        self._view = self.storage.view()
        self.payload = self._view

    def view(self) -> ReadOnlyFlatMapView[int, str]:
        return self._view

    @property
    def entries(self) -> Sequence[tuple[int, str]]:
        return self.storage

    def format(self, offset: int, args: tuple[int, int, int, int]) -> str:
        """
        FINDING: runtime_logging.md 4.2 says a format string may reference
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
        transport: StreamSink,
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

    def flush(
        self, batch_size: int = 32, interrupt_pending: Callable[[], bool] | None = None
    ) -> int:
        """GOTCHA-LOG-03: entries are grouped into batches of up to `batch_size`.
        A started transfer cannot be preempted, so `interrupt_pending` (if given)
        is checked only after each batch completes, never mid-batch."""
        flushed = 0
        while not self.ring.is_empty():
            batch_count = 0
            while not self.ring.is_empty() and batch_count < batch_size:
                entry = self.ring.pop()
                assert entry is not None
                msg = self.dictionary.format(entry.dict_offset, entry.args)
                line = f"[{entry.level.name}][tick:{entry.tick}] {msg}\n"
                self.transport.write(line.encode("utf-8"))
                flushed += 1
                batch_count += 1
            if interrupt_pending is not None and interrupt_pending():
                break
        return flushed


class ConsoleOutput:
    """Console raw-byte output path (interface_wit.md "console-output"): no dictionary, no ring buffer."""

    def __init__(self, transport: StreamSink):
        self.transport = transport

    def write(self, data: bytes) -> int:
        return self.transport.write(data)
