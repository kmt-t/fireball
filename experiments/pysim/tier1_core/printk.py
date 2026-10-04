"""Scheduler-independent diagnostic output for Tier 1 failures."""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol

PRINTK_RECORD_SIZE = 20


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


class PrintkWriter(Protocol):
    """Raw low-level output port shared by printk and buffered runtime logs."""

    def write(self, data: memoryview) -> int: ...


class Printk(PrintkWriter, Protocol):
    """Best-effort low-level event output that does not require COOS or IPC."""

    def write_event(
        self,
        level: PrintkLevel,
        event: PrintkEvent,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> None: ...
