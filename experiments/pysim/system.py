"""
experiments/pysim/system.py
Wires HAL + Logger/ConsoleOutput + the recovery-strategy engine + the real
fireball_call syscall surface into one running system.
fireball_call's ID space, register layout and error-code convention adhere
strictly to the architectural specifications:
- `docs/components/tier1_core/system_syscall.md` §5 defines the real ID table
- `docs/components/tier2_runtime/runtime_vmmio.md` defines the vMMIO address/register layout
- `docs/components/tier1_interface/ipc_router.md` defines the URI-routed, zero-copy message queue
This module uses self-contained simulation modules (`vmmio.py`, `ipc_router.py`,
`platform_memory.py`) mirroring the authoritative concept models, and provides
the actual register/byte-level storage and wire-level u32 handle numbering
required for end-to-end execution.
All guest output routes through WASI_FD_WRITE (console-output) to adhere strictly
to system_logging.md and interface_wit.md §5.5 (dictionary logger is internal-only).
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from hal import ShmBufferPool, ShmHandle, UartTransport
from ipc_router import IPCMessage, IPCRouter, IpcStatus, Role
from logger import ConsoleOutput, LogDictionary, Logger, LogLevel
from memory import (
    FB_CONF_MEMORY_POOL_SIZE,
    MemoryManager,
)
from runtime_engine import RuntimeEngine
from scheduler import Scheduler, TaskState
from system_containers import RadixBinaryTreeView
from vmmio import (
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
)


class FbSyscallId(IntEnum):
    """
    system_syscall.md §5's real per-category ID table (not a subset picked
        for convenience -- every ID this experiment can plausibly back with real
        behavior is included; ones it can't yet (see README's missing-spec list)
        still route here and fail with a real WASI errno, not silently vanish).
    """

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
    VDMA_START = 0x20
    IRQ_READ_FLAGS = 0x30
    IRQ_CLEAR = 0x31
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
    """
    system_syscall.md §4.2: `fireball_call` returns 0 on success, else a
        "WASIのerrno_t に準拠" error code -- the real wasi_snapshot_preview1
        numeric table, not a project-invented sentinel. Only the subset this
        file actually returns is enumerated; values match the real table's
        fixed alphabetical-after-e2big numbering exactly, so adding more later
        is just adding more real entries, never renumbering these.
    """

    SUCCESS = 0
    AGAIN = 6
    BADF = 8
    FAULT = 21
    INVAL = 28
    NOENT = 44
    NOMEM = 48
    NOSYS = 52
    PERM = 63
    NOTCAPABLE = 76


# runtime_vmmio.md §4.3-§4.5: real static-device addresses and register
# offsets (not invented -- copied from the spec's own register tables).
SYSCTL_BASE = 0xC000_0000
IPCR_BASE = 0xC000_1000
VDMA_BASE = 0xC000_2000
_STATIC_DEVICE_PAGE_MASK = 0xFFFF_F000
REG_SYS_CONTROL = 0x00
REG_SYS_STATUS = 0x04
REG_IRQ_FLAGS = 0x08
REG_VDMA_SRC = 0x00
REG_VDMA_DST = 0x04
REG_VDMA_COUNT = 0x08
REG_VDMA_CTRL = 0x0C
VDMA_CTRL_START_BIT = 0x1
SYS_CONTROL_RESET = 1
SYS_CONTROL_YIELD = 2
SYS_CONTROL_HALT = 3
SYS_CONTROL_SYSCALL = 4
FB_CONF_GUEST_RAM_SIZE = 4096  # system_config.md §3.3.4
FB_CONF_VSOC_PASSTHROUGH_BASE = 0xF000_0000  # runtime_vmmio.md §3.3's FC=15 window
_PASSTHROUGH_TEST_PAGES = 16  # this experiment's own arbitrary backing size,


# not a spec constant -- real PASSTHROUGH size
# depends on the host peripherals actually mapped
@dataclass(frozen=True)
class ShmSlice:
    """
    interface_wit.md 5.3's `shm-slice{handle, offset, len}`. There is no
        field here that could ever carry a guest linear-memory address -- only
        a handle name the pool must independently recognize and authorize.
    """

    handle: ShmHandle
    offset: int
    len: int


class BusMaster:
    """
    `fireball:host/bus`'s `bus-master.transfer-data`, resolved to the
        real shared-memory pool.
    """

    def __init__(self, pool: ShmBufferPool, task_id: int):
        self.pool = pool
        self.task_id = task_id

    def transfer_data(self, tx: ShmSlice, rx: ShmSlice) -> int:
        tx_view = self.pool.view(self.task_id, tx.handle, tx.offset, tx.len)
        rx_view = self.pool.view(self.task_id, rx.handle, rx.offset, rx.len)
        n = min(len(tx_view), len(rx_view))
        rx_view[:n] = bytes(tx_view[:n])
        return n


class BusSlave:
    """`fireball:host/bus`'s `bus-slave.set-response` / `get-received`."""

    def __init__(self, pool: ShmBufferPool, task_id: int):
        self.pool = pool
        self.task_id = task_id
        self._pending_response: bytes = b""

    def set_response(self, data: ShmSlice) -> None:
        view = self.pool.view(self.task_id, data.handle, data.offset, data.len)
        self._pending_response = bytes(view)

    def get_received(self, dest: ShmSlice) -> int:
        view = self.pool.view(self.task_id, dest.handle, dest.offset, dest.len)
        n = min(len(view), len(self._pending_response))
        view[:n] = self._pending_response[:n]
        return n


class System:
    """
    One running Fireball-shaped host: a single UART line, a single SHM
        buffer pool, one dictionary logger and one raw console writer sharing
        that line, a real vMMIO controller (FlatMap PTEs + TLB, reused from
        vmmio_concept.py) fronted by SYSCTL/IPCR/VDMA static-device registers
        and a PASSTHROUGH-backed physical memory window, and a real IPC router
        (reused from ipc_router_concept.py) with its fixed 3-service registry.
    """

    def __init__(self):
        self.transport = UartTransport()
        self.pool = ShmBufferPool()
        self.dictionary = LogDictionary()
        self.logger = Logger(self.transport, self.dictionary, min_level=LogLevel.DEBUG)
        self.console = ConsoleOutput(self.transport)
        # --- vMMIO: real FlatMap+TLB dispatch, this file's own register/byte
        # storage behind it (vmmio_concept.access() deliberately stops at the
        # dispatch decision -- see its module docstring -- it carries no
        # value/buffer of its own).
        self.vmmio = VMMIOController(guest_ram_size=FB_CONF_GUEST_RAM_SIZE)
        self.sysctl_regs = bytearray(0x30)
        self.ipcr_regs = bytearray(0x10)
        self.vdma_regs = bytearray(0x10)
        self.vmmio.map_static_device(vpn=SYSCTL_BASE >> 12)
        self.vmmio.map_static_device(vpn=IPCR_BASE >> 12)
        self.vmmio.map_static_device(vpn=VDMA_BASE >> 12)
        # PASSTHROUGH (FC=15) test window. Real PASSTHROUGH pages map to
        # actual host peripherals (FB_CONF_VSOC_PASSTHROUGH_BASE); this
        # experiment has none, so it backs the window with plain memory --
        # enough to prove the FlatMap/TLB/permission mechanics for real,
        # not to model any specific device.
        self.phys_mem = bytearray(_PASSTHROUGH_TEST_PAGES * 4096)
        for i in range(_PASSTHROUGH_TEST_PAGES):
            self.vmmio.map_passthrough_page(
                vpn=(FB_CONF_VSOC_PASSTHROUGH_BASE >> 12) + i, phys_page=i
            )

        # Physical Memory Manager (platform_memory.md) with 64KB aligned pool
        self.memory_manager = MemoryManager()
        self.memory_manager.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        self.scheduler = Scheduler()
        self.ipc = IPCRouter(self.scheduler)
        # Direct 1-based index mapping over sorted self.ipc.registry.keys array (no dynamic dict)
        self.runtime_engine = RuntimeEngine()
        self.scheduler.set_idle_hook(self._on_idle)
        self.halted = False
        self.reset_requested = False
        self.exit_code: int | None = None
        self._guest_memory: bytearray | None = None
        self._current_task_id = 0
        # Build fireball_call dispatch table via RadixBinaryTreeView
        syscall_handlers: list[tuple[int, Any]] = [
            (
                FbSyscallId.SYS_YIELD,
                lambda a0, a1, a2, a3, a4, a5: int(self._apply_sys_control(SYS_CONTROL_YIELD)),
            ),
            (
                FbSyscallId.SYS_HALT,
                lambda a0, a1, a2, a3, a4, a5: int(self._apply_sys_control(SYS_CONTROL_HALT)),
            ),
            (
                FbSyscallId.SYS_RESET,
                lambda a0, a1, a2, a3, a4, a5: int(self._apply_sys_control(SYS_CONTROL_RESET)),
            ),
            (
                FbSyscallId.MMIO_READ32,
                lambda a0, a1, a2, a3, a4, a5: self._mmio_read(a0, 4),
            ),
            (
                FbSyscallId.MMIO_WRITE32,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_write(a0, a1, 4)),
            ),
            (
                FbSyscallId.MMIO_READ8,
                lambda a0, a1, a2, a3, a4, a5: self._mmio_read(a0, 1),
            ),
            (
                FbSyscallId.MMIO_WRITE8,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_write(a0, a1, 1)),
            ),
            (
                FbSyscallId.MMIO_BULK_READ,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_bulk_read(a0, a1, a2)),
            ),
            (
                FbSyscallId.MMIO_BULK_WRITE,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_bulk_write(a0, a1, a2)),
            ),
            (
                FbSyscallId.VDMA_START,
                lambda a0, a1, a2, a3, a4, a5: int(self._vdma_start(a0, a1, a2)),
            ),
            (
                FbSyscallId.IRQ_READ_FLAGS,
                lambda a0, a1, a2, a3, a4, a5: self._irq_read_flags(),
            ),
            (
                FbSyscallId.IRQ_CLEAR,
                lambda a0, a1, a2, a3, a4, a5: int(self._irq_clear(a0)),
            ),
            (
                FbSyscallId.IPC_SEND,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_send(a0, a1, a2)),
            ),
            (
                FbSyscallId.IPC_RECV,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_recv(a0, a1, a2)),
            ),
            (
                FbSyscallId.IPC_LOOKUP,
                lambda a0, a1, a2, a3, a4, a5: self._ipc_lookup(a0, a1),
            ),
            (
                FbSyscallId.WASI_FD_WRITE,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_fd_write(a0, a1, a2, a3)),
            ),
            (
                FbSyscallId.WASI_FD_READ,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_fd_read(a0, a1, a2, a3)),
            ),
            (
                FbSyscallId.WASI_FD_CLOSE,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_fd_close(a0)),
            ),
            (
                FbSyscallId.WASI_CLOCK_TIME_GET,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_clock_time_get(a2)),
            ),
            (
                FbSyscallId.WASI_PROC_EXIT,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_proc_exit(a0)),
            ),
            (
                FbSyscallId.WASI_RANDOM_GET,
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_random_get(a0, a1)),
            ),
        ]
        syscall_handlers.sort(key=lambda x: int(x[0]))
        keys = [int(x[0]) for x in syscall_handlers]
        values = [x[1] for x in syscall_handlers]
        radix_shift = 4
        max_prefix = max(keys) >> radix_shift
        radix_table = [0] * (max_prefix + 2)
        current_prefix = 0
        for idx, k in enumerate(keys):
            prefix = k >> radix_shift
            while current_prefix < prefix:
                current_prefix += 1
                radix_table[current_prefix] = idx
        while current_prefix <= max_prefix:
            current_prefix += 1
            radix_table[current_prefix] = len(keys)

        self._syscall_dispatch_tree = RadixBinaryTreeView(
            keys, values, radix_table, radix_shift=radix_shift
        )

    def _on_idle(self) -> None:
        """COOS idle_hook dispatch: flushes deferred logs and compiles queued JIT traces."""
        self.logger.flush()
        self.runtime_engine.idle_hook(budget=4)

    def bus_master(self, task_id: int) -> BusMaster:
        return BusMaster(self.pool, task_id)

    def bus_slave(self, task_id: int) -> BusSlave:
        return BusSlave(self.pool, task_id)

    def bind_guest(self, memory: bytearray | None, task_id: int = 1) -> None:
        """
        Must be called before invoking guest code that will use
                `fb_offset_t` arguments (IPC_*/WASI_*): those are relative offsets
                into "the calling task's own guest memory" (system_syscall.md
                §4.1), which this single-tenant experiment models as one mutable
                binding set by the embedder rather than a per-task table.
        """
        self._guest_memory = memory
        self._current_task_id = task_id
        # fireball_call's IPC_SEND/IPC_RECV delegate to scheduler.Channel,
        # which rendezvous on registered Task objects, not bare task_id ints.
        # This task is driven directly by fireball_call, never by the
        # scheduler's own run_until_idle() loop, so it must not sit in READY.
        if self.scheduler.get_task(task_id) is None:
            self.scheduler.spawn(name=f"guest_task_{task_id}", task_id=task_id)
            self.scheduler.detach(self.scheduler.get_task(task_id))

    # --- fireball_call ------------------------------------------------
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
        """
        The one host import a guest actually needs
                (system_syscall.md §3-4): a single syscall-ID-dispatched bridge
                carrying `id` plus six generic u32 args, dispatched via RadixBinaryTreeView.
        """

        handler = self._syscall_dispatch_tree.find(syscall_id)
        if handler is not None:
            return handler(arg0, arg1, arg2, arg3, arg4, arg5)
        return int(WasiErrno.NOSYS)

    # --- guest memory (fb_offset_t resolution) -------------------------
    def _guest_ram_ok(self, offset: int, length: int) -> bool:
        return (
            self._guest_memory is not None
            and 0 <= offset
            and offset + length <= len(self._guest_memory)
        )

    def _read_guest(self, offset: int, length: int) -> bytes | None:
        if not self._guest_ram_ok(offset, length):
            return None
        return bytes(self._guest_memory[offset : offset + length])

    def _write_guest(self, offset: int, data: bytes) -> bool:
        if not self._guest_ram_ok(offset, len(data)):
            return False
        self._guest_memory[offset : offset + len(data)] = data
        return True

    # --- System (SYS_YIELD/HALT/RESET, real REG_SYS_CONTROL semantics) --
    def _apply_sys_control(self, cmd: int) -> WasiErrno:
        """
        runtime_vmmio.md §4.4's REG_SYS_CONTROL: `1`=Reset, `2`=Yield,
                `3`=Halt. `fireball_call`'s SYS_YIELD/HALT/RESET IDs are the
                "cannot do a raw vMMIO store" proxy for writing this exact
                register (system_syscall.md §2's "アクセスパスB"), so both paths
                funnel through this one real effect.
        """
        struct.pack_into("<I", self.sysctl_regs, REG_SYS_CONTROL, cmd & 0xFFFF_FFFF)
        if cmd == SYS_CONTROL_RESET:
            self.reset_requested = True
        elif cmd == SYS_CONTROL_YIELD:
            # {CooperativeMultitasking}: a real yield suspends the calling
            # coroutine until the scheduler resumes it. This experiment's
            # WASM JIT has no continuation/suspend mechanism -- a native
            # `call` into fireball_call runs to completion synchronously --
            # so there is nothing to suspend here. scheduler.py's own
            # generator-based yield is the actual host-side yield model for
            # the HAL demo; this path can only acknowledge the request.
            pass
        elif cmd == SYS_CONTROL_HALT:
            self.halted = True
        else:
            return WasiErrno.INVAL
        return WasiErrno.SUCCESS

    # --- vMMIO Generic (real FlatMap/TLB dispatch + real backing bytes) -
    def _trap_to_errno(self, status: str) -> WasiErrno | None:
        if status in ("OK_SYSCALL", "OK_PHYSICAL", "OK_GUEST_RAM"):
            return None
        return {
            TrapCode.OUT_OF_BOUNDS: WasiErrno.FAULT,
            TrapCode.UNDEFINED_FC: WasiErrno.NOENT,
            TrapCode.UNREGISTERED_PAGE: WasiErrno.NOENT,
            TrapCode.ACCESS_VIOLATION: WasiErrno.PERM,
            TrapCode.OWNER_MISMATCH: WasiErrno.PERM,
        }.get(status, WasiErrno.FAULT)

    def _mmio_touch(self, addr: int, is_write: bool):
        """
        Runs the real permission dispatch, then resolves this
                experiment's own backing storage for the byte-level effect
                vmmio_concept.access() intentionally leaves to the caller.
                Returns (errno_or_None, backing_bytearray_or_None, local_offset).
        """

        status, _ = self.vmmio.access(addr, is_write, current_task_id=self._current_task_id)
        errno = self._trap_to_errno(status)
        if errno is not None:
            return errno, None, None
        a = VmmioAddress(addr)
        if a.fc() == FC_STATIC_DEVICE:
            page = addr & _STATIC_DEVICE_PAGE_MASK
            if page == SYSCTL_BASE:
                return None, self.sysctl_regs, a.offset()
            if page == IPCR_BASE:
                return None, self.ipcr_regs, a.offset()
            if page == VDMA_BASE:
                return None, self.vdma_regs, a.offset()
            return WasiErrno.NOENT, None, None
        # Tier 3 (SHM / PASSTHROUGH): resolve the same phys_addr formula
        # vmmio_concept.access() itself already computed internally, from
        # the same public PTE fields it exposes (self.vmmio.ptes is a
        # public FlatMap, not a hidden implementation detail).
        pte = self.vmmio.ptes.find(a.vpn())
        phys_addr = (pte.phys_page << 12) | a.offset()
        return None, self.phys_mem, phys_addr

    def _mmio_read(self, addr: int, width: int) -> int:
        errno, backing, off = self._mmio_touch(addr, is_write=False)
        if errno is not None:
            return int(errno)
        if off + width > len(backing):
            return int(WasiErrno.FAULT)
        return int.from_bytes(backing[off : off + width], "little")

    def _mmio_write(self, addr: int, value: int, width: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=True)
        if errno is not None:
            return errno
        if off + width > len(backing):
            return WasiErrno.FAULT
        backing[off : off + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
        if backing is self.sysctl_regs and off == REG_SYS_CONTROL:
            return self._apply_sys_control(value)
        if backing is self.vdma_regs and off == REG_VDMA_CTRL and (value & VDMA_CTRL_START_BIT):
            return self._run_vdma()
        return WasiErrno.SUCCESS

    def _mmio_bulk_read(self, addr: int, dest_offset: int, byte_count: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=False)
        if errno is not None:
            return errno
        if off + byte_count > len(backing):
            return WasiErrno.FAULT
        if not self._write_guest(dest_offset, bytes(backing[off : off + byte_count])):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _mmio_bulk_write(self, addr: int, src_offset: int, byte_count: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=True)
        if errno is not None:
            return errno
        data = self._read_guest(src_offset, byte_count)
        if data is None or off + byte_count > len(backing):
            return WasiErrno.FAULT
        backing[off : off + byte_count] = data
        return WasiErrno.SUCCESS

    # --- VDMA (real REG_VDMA_* registers + a real memcpy) ---------------
    def _vdma_start(self, src: int, dst: int, byte_count: int) -> WasiErrno:
        struct.pack_into(
            "<III",
            self.vdma_regs,
            REG_VDMA_SRC,
            src & 0xFFFF_FFFF,
            dst & 0xFFFF_FFFF,
            byte_count & 0xFFFF_FFFF,
        )
        return self._run_vdma()

    def _vdma_region(self, addr: int, count: int, is_write: bool):
        """
        runtime_vmmio.md §4.5: VDMA src/dst may be guest RAM (Tier 1) or
                vMMIO FC=14/15 -- resolved through the exact same permission gate
                as a direct access, owner checks included.
        """

        a = VmmioAddress(addr)
        if a.is_linear():
            return (self._guest_memory, addr) if self._guest_ram_ok(addr, count) else (None, None)
        errno, backing, off = self._mmio_touch(addr, is_write)
        if errno is not None or off + count > len(backing):
            return None, None
        return backing, off

    def _run_vdma(self) -> WasiErrno:
        src, dst, count = struct.unpack_from("<III", self.vdma_regs, REG_VDMA_SRC)
        src_backing, src_off = self._vdma_region(src, count, is_write=False)
        if src_backing is None:
            return WasiErrno.FAULT
        dst_backing, dst_off = self._vdma_region(dst, count, is_write=True)
        if dst_backing is None:
            return WasiErrno.FAULT
        dst_backing[dst_off : dst_off + count] = bytes(src_backing[src_off : src_off + count])
        return WasiErrno.SUCCESS

    # --- IRQ (REG_IRQ_FLAGS, shared with SYSCTL's own register file) ----
    def _irq_read_flags(self) -> int:
        return struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]

    def _irq_clear(self, mask: int) -> WasiErrno:
        flags = struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]
        struct.pack_into("<I", self.sysctl_regs, REG_IRQ_FLAGS, flags & ~mask & 0xFFFF_FFFF)
        return WasiErrno.SUCCESS

    def raise_irq(self, mask: int) -> None:
        """
        Not itself a syscall (system_syscall.md §8: interrupts are a
                host-to-guest notification, not a guest-initiated call) -- lets a
                test or the HAL demo set REG_IRQ_FLAGS bits for IRQ_READ_FLAGS/
                IRQ_CLEAR to observe.
        """

        flags = struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]
        struct.pack_into("<I", self.sysctl_regs, REG_IRQ_FLAGS, (flags | mask) & 0xFFFF_FFFF)

    # --- IPC (real IPCRouter: URI lookup, RBAC, CSP rendezvous handoff) ---
    def _ipc_lookup(self, uri_offset: int, uri_len: int) -> int:
        raw = self._read_guest(uri_offset, uri_len)
        if raw is None:
            return int(WasiErrno.FAULT)
        try:
            uri = raw.decode("utf-8")
        except UnicodeDecodeError:
            return int(WasiErrno.INVAL)
        for i, u in enumerate(self.ipc.registry.keys, start=1):
            if u == uri:
                return i
        return int(WasiErrno.NOENT)

    def _ipc_send(self, handle_id: int, msg_offset: int, msg_len: int) -> WasiErrno:
        if handle_id < 1 or handle_id > len(self.ipc.registry.keys):
            return WasiErrno.BADF
        uri = self.ipc.registry.keys[handle_id - 1]
        payload = self._read_guest(msg_offset, msg_len)
        if payload is None:
            return WasiErrno.FAULT
        msg = IPCMessage.from_bytes(bytes(payload))
        # The guest task's own execution *is* this call: system_syscall.md
        # models a host call as running inside the calling task's own
        # coroutine (the runtime task, never the Interpreter itself -- it
        # only ever executes-and-returns), so waiting for a receiver is this
        # task waiting, via the scheduler's ordinary sleep/wake queue -- not
        # a separate mechanism, and never a queue/EAGAIN (ipc_router.md §5.1).
        task = self.scheduler.get_task(self._current_task_id)
        self.scheduler.current_task = task
        # The sender here is this runtime task -- the hypervisor-side
        # execution context hosting the guest across the fireball_call trap
        # boundary -- acting with the RUNTIME role on the guest's behalf;
        # it is not the guest's own (untrusted) code reaching into IPC
        # directly. ipc_router_concept.py's fixed role matrix has no
        # multi-role guest model, so RUNTIME is the only role this
        # runtime task ever sends as.
        task.coro = self.ipc.send(Role.RUNTIME, uri, msg)
        self.scheduler.attach(task)
        while task.state != TaskState.TERMINATED:
            self.scheduler.run_until_idle()
        status, _ = task.result
        if status == IpcStatus.COMPLETED:
            return WasiErrno.SUCCESS
        if status == IpcStatus.ERR_PERMISSION_DENIED:
            return WasiErrno.PERM
        return WasiErrno.NOENT

    def _ipc_recv(self, handle_id: int, buf_offset: int, buf_len: int) -> int:
        if handle_id < 1 or handle_id > len(self.ipc.registry.keys):
            return int(WasiErrno.BADF)
        uri = self.ipc.registry.keys[handle_id - 1]
        # See _ipc_send: this task's own coroutine *is* the recv() call.
        # recv() itself selects across every sender_role this URI's service
        # role may legitimately be sent from -- the guest never states one.
        task = self.scheduler.get_task(self._current_task_id)
        self.scheduler.current_task = task
        task.coro = self.ipc.recv(uri)
        self.scheduler.attach(task)
        while task.state != TaskState.TERMINATED:
            self.scheduler.run_until_idle()
        status, msg = task.result
        if status in (IpcStatus.ERR_NOT_FOUND, IpcStatus.ERR_PERMISSION_DENIED):
            return int(WasiErrno.NOENT)
        data = msg.raw_payload or b""
        n = min(len(data), buf_len)
        if not self._write_guest(buf_offset, data[:n]):
            return int(WasiErrno.FAULT)
        return n

    # --- WASI (interface_wit.md §5.5-5.6) --------------------------------
    def _wasi_fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> WasiErrno:
        """
        system_syscall.md §7.1: the Shim already loops per-vector before
                trapping, so `iovs_len` reaching here is expected to be 1 -- but
                this loops anyway rather than assuming it, since nothing enforces
                that at this boundary.
        """

        if fd not in (1, 2):
            return WasiErrno.BADF
        total = 0
        for i in range(iovs_len):
            iov = self._read_guest(iovs_ptr + i * 8, 8)
            if iov is None:
                return WasiErrno.FAULT
            buf, buf_len = struct.unpack("<II", iov)
            data = self._read_guest(buf, buf_len)
            if data is None:
                return WasiErrno.FAULT
            self.console.write(data)
            total += len(data)

        if not self._write_guest(nwritten_ptr, struct.pack("<I", total)):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _wasi_fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> WasiErrno:
        # No real stdin exists in this experiment -- reporting 0 bytes read
        # (EOF) is a genuine, spec-legal WASI outcome, not a stand-in value.
        if not self._write_guest(nread_ptr, struct.pack("<I", 0)):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _wasi_fd_close(self, fd: int) -> WasiErrno:
        return WasiErrno.SUCCESS

    def _wasi_clock_time_get(self, time_ptr: int) -> WasiErrno:
        # wasi:clocks/monotonic-clock (interface_wit.md 5.1/5.6): backed by
        # the real host monotonic clock, same as hal.py's Timer.
        now_ns = time.monotonic_ns()
        if not self._write_guest(time_ptr, struct.pack("<Q", now_ns)):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _wasi_proc_exit(self, exit_code: int) -> WasiErrno:
        self.halted = True
        self.exit_code = exit_code
        return WasiErrno.SUCCESS

    def _wasi_random_get(self, buf_ptr: int, buf_len: int) -> WasiErrno:
        data = os.urandom(buf_len)
        if not self._write_guest(buf_ptr, data):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def shutdown(self) -> None:
        self.pool.close_all()
        self.transport.close()
