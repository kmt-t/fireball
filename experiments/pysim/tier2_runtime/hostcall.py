"""Tier 2 Fireball host-call contract and dispatch gateway."""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from typing import Protocol

from recovery import Result
from scheduler import Scheduler
from system_containers import ReadOnlyFlatMapStorage
from virq import RegistrationError, RegistrationStatus


class FbSyscallId(IntEnum):
    """Stable IDs accepted by the generic ``fireball_call`` trap."""

    RESERVED = 0x00
    SYS_YIELD = 0x01
    SYS_HALT = 0x02
    SYS_RESET = 0x03
    MMIO_READ32 = 0x10
    MMIO_WRITE32 = 0x11
    MMIO_READ8 = 0x12
    MMIO_WRITE8 = 0x13
    MMIO_BULK_READ = 0x14
    MMIO_BULK_WRITE = 0x15
    TRIGGER_SET_PIN = 0x16
    IPC_SEND = 0x40
    IPC_RECV = 0x41
    IPC_LOOKUP = 0x42
    WASI_FD_WRITE = 0x80
    WASI_FD_READ = 0x81
    WASI_FD_CLOSE = 0x82
    WASI_CLOCK_TIME_GET = 0x83
    WASI_PROC_EXIT = 0x84
    WASI_RANDOM_GET = 0x85


class WasiErrno(IntEnum):
    """WASI Preview 1 errno values returned by low-level host calls."""

    SUCCESS = 0
    AGAIN = 6
    BADF = 8
    FAULT = 21
    INVAL = 28
    IO = 29
    NOENT = 44
    NOMEM = 48
    NOSYS = 52
    PERM = 63
    NOTCAPABLE = 76


SyscallHandler = Callable[[int, int, int, int, int, int], int]
VdmaTransfer = Callable[[int, int, int], int]


class VirqRegistrationPort(Protocol):
    """Tier 2 vSoC service used by the host-call gateway."""

    __slots__ = ()

    def register_virq_dispatcher(
        self, node_id: int, function_index: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Validate and stage one dispatcher registration."""

    def unregister_virq_dispatcher(
        self, node_id: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Stage removal of one dispatcher registration."""


class WasiPreview1Host(Protocol):
    """Narrow callback surface used by generic WASI syscall handlers."""

    __slots__ = ()

    def fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        """Handle one Preview 1 fd_write import."""

    def fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        """Handle one Preview 1 fd_read import."""


class FireballHostCallPort(Protocol):
    """Stateless call surface corresponding to ``fireball-hostcall`` WIT imports."""

    __slots__ = ()

    def fireball_call(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        """Dispatch one generic seven-word Fireball host call."""

    def virq_register(self, node_id: int, function_index: int) -> int:
        """Stage a vIRQ dispatcher registration."""

    def virq_unregister(self, node_id: int) -> int:
        """Stage removal of a vIRQ dispatcher registration."""

    def vdma_start(self, source: int, destination: int, byte_count: int) -> int:
        """Execute one validated virtual DMA transfer."""


class RuntimeHostCallGateway:
    """Own the WIT-facing dispatch boundary and delegate domain work to Tier 2 ports."""

    __slots__ = ("_scheduler", "_syscall_handlers", "_virq", "_vdma_transfer")

    def __init__(
        self,
        scheduler: Scheduler,
        syscall_handlers: ReadOnlyFlatMapStorage[int, SyscallHandler],
        virq: VirqRegistrationPort,
        vdma_transfer: VdmaTransfer,
    ) -> None:
        self._scheduler = scheduler
        self._syscall_handlers = syscall_handlers
        self._virq = virq
        self._vdma_transfer = vdma_transfer

    def _require_active_task(self) -> None:
        assert self._scheduler.current_task is not None, (
            "Fireball host calls require an active scheduler task"
        )

    def fireball_call(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        self._require_active_task()
        handler = self._syscall_handlers.view().find(syscall_id)
        if handler is None:
            return int(WasiErrno.NOSYS)
        return handler(arg0, arg1, arg2, arg3, arg4, arg5)

    @staticmethod
    def _virq_errno(error: RegistrationError | None) -> WasiErrno:
        if error == RegistrationError.MODULE_UNAVAILABLE:
            return WasiErrno.NOSYS
        return WasiErrno.INVAL

    def virq_register(self, node_id: int, function_index: int) -> int:
        self._require_active_task()
        result = self._virq.register_virq_dispatcher(node_id, function_index)
        if result.is_ok:
            return int(WasiErrno.SUCCESS)
        return int(self._virq_errno(result.error))

    def virq_unregister(self, node_id: int) -> int:
        self._require_active_task()
        result = self._virq.unregister_virq_dispatcher(node_id)
        if result.is_ok:
            return int(WasiErrno.SUCCESS)
        return int(self._virq_errno(result.error))

    def vdma_start(self, source: int, destination: int, byte_count: int) -> int:
        self._require_active_task()
        return self._vdma_transfer(source, destination, byte_count)
