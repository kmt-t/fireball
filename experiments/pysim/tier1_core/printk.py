"""Scheduler-independent diagnostic output for Tier 1 failures."""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol

from system_containers import ReadOnlyFlatMapView

PRINTK_HEADER_SIZE = 4
PRINTK_MAX_RECORD_SIZE = 20


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


class PrintkLevel(IntEnum):
    WARN = 2
    ERROR = 3


class PrintkEvent(IntEnum):
    COOS_HANDOFF_LIMIT = 0x0101
    COOS_TASK_CAPACITY = 0x0102
    COOS_DUPLICATE_TASK = 0x0103
    COOS_IRQ_OVERFLOW = 0x0104

    IPC_RBAC_DENIED = 0x0201
    IPC_UNKNOWN_URI = 0x0202
    IPC_MSG_TOO_LARGE = 0x0203
    IPC_INVALID_OWNERSHIP = 0x0204


PRINTK_DICTIONARY: tuple[tuple[int, str], ...] = (
    (0x0101, "COOS: handoff limit reached (task=%d, count=%d)"),
    (0x0102, "COOS: task capacity exceeded (max=%d, attempted=%d)"),
    (0x0103, "COOS: duplicate task id rejected (task=%d)"),
    (0x0104, "COOS: irq queue overflow dropped (vector=%d, dropped_total=%d)"),
    (0x0201, "IPC: rbac denied (sender_role=%d, target_role=%d)"),
    (0x0202, "IPC: unknown uri routing failed (uri_handle=%d)"),
    (0x0203, "IPC: message too large (kv_count=%d, max=%d)"),
    (0x0204, "IPC: invalid ownership state (current_state=%d, op=%d)"),
)


# Simulates immutable ROM metadata generated from the same build-time dictionary.
_PRINTK_ARGUMENT_COUNTS = bytes(format_argument_count(fmt) for _, fmt in PRINTK_DICTIONARY)
_PRINTK_DICTIONARY_VIEW: ReadOnlyFlatMapView[int, str] = ReadOnlyFlatMapView(PRINTK_DICTIONARY)


def printk_argument_count(event: PrintkEvent) -> int:
    index = _PRINTK_DICTIONARY_VIEW.find_index(int(event))
    assert index >= 0, "unregistered printk event"
    return _PRINTK_ARGUMENT_COUNTS[index]


class PrintkWriter(Protocol):
    """Raw low-level output port shared by printk and buffered runtime logs."""

    def write(self, data: memoryview) -> int: ...


class Printk(PrintkWriter, Protocol):
    """Best-effort low-level event output that does not require COOS or IPC."""

    def write_base64(self, data: memoryview) -> int:
        """Synchronously encode binary bytes plus LF; assert on incomplete output."""
        ...

    def write_raw(self, data: memoryview) -> int:
        """Write guest stderr bytes without dictionary decoding or added newlines."""
        ...

    def write_event(
        self,
        level: PrintkLevel,
        event: PrintkEvent,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> None: ...
