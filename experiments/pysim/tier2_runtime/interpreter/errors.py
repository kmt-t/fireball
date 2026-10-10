"""Interpreter traps, execution statuses, and diagnostic event IDs."""

from __future__ import annotations

from enum import IntEnum


class TrapCode(IntEnum):
    LOCAL_STACK_CAPACITY = 1
    CALL_FRAME_CAPACITY = 2
    CALL_STACK_CAPACITY = 3
    OPERAND_STACK_CAPACITY = 4
    NO_HOST_HANDLER = 5
    TABLE_INDEX_OUT_OF_BOUNDS = 6
    TABLE_SLOT_UNINITIALIZED = 7
    UNREACHABLE = 9
    CONTROL_FRAME_CAPACITY = 10
    VMMIO_NOT_CONFIGURED = 11
    VMMIO_ACCESS = 12
    MEMORY_OUT_OF_BOUNDS = 13
    MEMORY_SECTION_MISSING = 14
    INTEGER_DIVIDE_BY_ZERO = 15
    INTEGER_OVERFLOW = 16
    INVALID_CONVERSION = 17


class Trap(Exception):
    __slots__ = ("code", "detail")

    def __init__(self, code: TrapCode, detail: int = 0):
        self.code = code
        self.detail = detail


LOG_EVT_TRAP_BASE = 0x0300

TRAP_LOG_EVENTS: tuple[tuple[int, str], ...] = (
    (
        LOG_EVT_TRAP_BASE + TrapCode.LOCAL_STACK_CAPACITY,
        "TRAP: local stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CALL_FRAME_CAPACITY,
        "TRAP: call frame capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CALL_STACK_CAPACITY,
        "TRAP: call stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.OPERAND_STACK_CAPACITY,
        "TRAP: operand stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.NO_HOST_HANDLER,
        "TRAP: import has no bound host handler (pc=0x%08X, func=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.TABLE_INDEX_OUT_OF_BOUNDS,
        "TRAP: table index out of bounds (pc=0x%08X, slot=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.TABLE_SLOT_UNINITIALIZED,
        "TRAP: table slot uninitialized (pc=0x%08X, slot=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.UNREACHABLE,
        "TRAP: unreachable instruction executed (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CONTROL_FRAME_CAPACITY,
        "TRAP: control frame capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.VMMIO_NOT_CONFIGURED,
        "TRAP: vMMIO region not configured (pc=0x%08X, addr=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.VMMIO_ACCESS,
        "TRAP: vMMIO access rejected (pc=0x%08X, status=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.MEMORY_OUT_OF_BOUNDS,
        "TRAP: linear memory access out of bounds (pc=0x%08X, addr=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.MEMORY_SECTION_MISSING,
        "TRAP: memory access without a declared memory section (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INTEGER_DIVIDE_BY_ZERO,
        "TRAP: integer divide by zero (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INTEGER_OVERFLOW,
        "TRAP: integer division overflow (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INVALID_CONVERSION,
        "TRAP: invalid float-to-integer conversion (pc=0x%08X)",
    ),
)

RETURN_SENTINEL_IP = -1
RETURN_SENTINEL_PC = 0xFFFF_FFFF
NATIVE_DISPATCH_YIELD = 5
NATIVE_DISPATCH_CALL_BOUNDARY = 6
NATIVE_DISPATCH_DEBUG_STOP = 7
NATIVE_DISPATCH_RUNTIME_API_BOUNDARY = 8
