"""
docs/components/tier2_runtime/concepts/syscall_concept.py
Reference Concept Implementation: fireball_call dispatch and guest-range validation

This concept implementation covers the component-level invariants for the host
syscall boundary. vMMIO address translation remains the responsibility of
vmmio_concept.py.
"""

from collections.abc import Mapping
from typing import NamedTuple

BACKS = [
    "components/tier2_runtime/runtime_syscall.md",
]


class WasiErrno:
    SUCCESS = 0
    FAULT = 21
    NOSYS = 52


class SyscallResult(NamedTuple):
    errno: int
    handler: str | None
    packed_args: tuple[int, int, int, int, int, int]


class SyscallDispatcher:
    """Fixed-arity fireball_call dispatcher used by the concept tests."""

    def __init__(self, guest_memory_size: int) -> None:
        if guest_memory_size <= 0:
            raise ValueError("guest_memory_size must be positive")
        self.guest_memory_size = guest_memory_size
        self.handlers: Mapping[int, str] = {
            0x01: "SYS_YIELD",
            0x10: "MMIO_READ32",
            0x40: "IPC_SEND",
            0x80: "WASI_FD_WRITE",
        }

    @staticmethod
    def pack_args(args: tuple[int, ...]) -> tuple[int, int, int, int, int, int]:
        if len(args) > 6:
            raise ValueError("fireball_call accepts at most six arguments")
        return (*args, 0, 0, 0, 0, 0, 0)[:6]

    def validate_guest_range(self, offset: int, size: int) -> bool:
        """Validate without evaluating an overflowing offset + size expression."""
        return (
            offset >= 0
            and size >= 0
            and offset <= self.guest_memory_size
            and size <= self.guest_memory_size - offset
        )

    def dispatch(
        self,
        syscall_id: int,
        args: tuple[int, ...],
        guest_range: tuple[int, int] | None = None,
    ) -> SyscallResult:
        packed_args = self.pack_args(args)
        handler = self.handlers.get(syscall_id)
        if handler is None:
            return SyscallResult(WasiErrno.NOSYS, None, packed_args)
        if guest_range is not None and not self.validate_guest_range(*guest_range):
            return SyscallResult(WasiErrno.FAULT, handler, packed_args)
        return SyscallResult(WasiErrno.SUCCESS, handler, packed_args)

    def fireball_call(
        self,
        syscall_id: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
        arg4: int = 0,
        arg5: int = 0,
        guest_range: tuple[int, int] | None = None,
    ) -> SyscallResult:
        return self.dispatch(
            syscall_id,
            (arg0, arg1, arg2, arg3, arg4, arg5),
            guest_range,
        )


def test_dispatch_and_argument_packing() -> None:
    dispatcher = SyscallDispatcher(guest_memory_size=1024)
    result = dispatcher.fireball_call(0x40, 1, 2, 3)
    assert result.errno == WasiErrno.SUCCESS
    assert result.handler == "IPC_SEND"
    assert result.packed_args == (1, 2, 3, 0, 0, 0)


def test_reserved_irq_and_unknown_id_return_nosys() -> None:
    dispatcher = SyscallDispatcher(guest_memory_size=1024)
    for syscall_id in (0x30, 0x31, 0x3F, 0xFF):
        result = dispatcher.fireball_call(syscall_id)
        assert result.errno == WasiErrno.NOSYS
        assert result.handler is None


def test_guest_range_validation_precedes_access() -> None:
    dispatcher = SyscallDispatcher(guest_memory_size=1024)
    valid = dispatcher.fireball_call(0x80, guest_range=(100, 924))
    invalid = dispatcher.fireball_call(0x80, guest_range=(100, 925))
    overflow = dispatcher.fireball_call(0x80, guest_range=(0xFFFF_FFF0, 32))
    assert valid.errno == WasiErrno.SUCCESS
    assert invalid.errno == WasiErrno.FAULT
    assert overflow.errno == WasiErrno.FAULT


if __name__ == "__main__":
    test_dispatch_and_argument_packing()
    test_reserved_irq_and_unknown_id_return_nosys()
    test_guest_range_validation_precedes_access()
    print("[PASS] Syscall concept dispatch, NOSYS, and guest-range tests passed.")
