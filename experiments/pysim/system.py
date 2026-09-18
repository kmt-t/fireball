"""
experiments/pysim/system.py
Wires HAL + Logger/ConsoleOutput + the recovery-strategy engine + the real
fireball_call syscall surface into one running system.
fireball_call's host-call ID space and error-code convention adhere
strictly to the architectural specifications:
- `docs/components/tier2_runtime/runtime_syscall.md` defines the real ID table
- `docs/components/tier2_runtime/runtime_vmmio.md` defines the vMMIO address layout
- `docs/components/tier1_interface/ipc_router.md` defines the URI-routed, zero-copy message queue
This module uses self-contained simulation modules (`vmmio.py`, `ipc_router.py`,
`tier2_runtime/memory.py`) mirroring the authoritative concept models, and provides
    the actual byte-level storage and wire-level u32 handle numbering
required for end-to-end execution.
All guest output routes through WASI_FD_WRITE (console-output) to adhere strictly
to runtime_logging.md and interface_wit.md's "console-output" section (dictionary
logger is internal-only).
"""

from __future__ import annotations

import os
import struct
import time
from collections.abc import Mapping
from enum import IntEnum
from typing import TYPE_CHECKING, Callable

from hal_dispatch import HalBufferPool
from ipc_router import (
    IPCMessage,
    IPCRouter,
    IPCStatus,
    Role,
    bytes_to_kv_storage,
    kv_entries_to_bytes,
)

if TYPE_CHECKING:
    from debugger import DebuggerManager
    from gdb_server import GDBServer
    from hal_dispatch import HalDriver, HalTask
    from interpreter import BasicBlock, WASMContext
    from wasi import WasiHostContext

from loader import fnv1a_32
from logger import LogDictionary, Logger, LogLevel
from memory import (
    FB_CONF_MEMORY_POOL_SIZE,
    MemoryManager,
)
from runtime_engine import DispatchResult, RuntimeEngine
from scheduler import FB_CONF_MAX_TASKS, Channel, Scheduler, Task, TaskState
from stream_transport import StreamTransport
from system_containers import MutableFlatMapStorage, ReadOnlyFlatMapStorage, StaticVector
from virq import RegistrationError
from vmmio import (
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
    VmmioStatus,
)
from wasi_hal_bindings import DEFAULT_WASI_HAL_BINDINGS
from wasm_module import BasicBlock


class FbSyscallId(IntEnum):
    """
    runtime_syscall.md's real per-category ID table (not a subset picked
        for convenience -- every ID this experiment can plausibly back with real
        behavior is included; ones it can't yet still route here and fail with
        a real WASI errno (NOSYS), not silently vanish -- see TRIGGER_SET_PIN
        below for the one currently-reserved exception).
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
    # Reserved: registered per runtime_syscall.md's ID table, but this
    # experiment has no dedicated GPIO vMMIO register to back a real pin
    # write with, so it is deliberately left out of syscall_handlers below
    # and falls through fireball_call's NOSYS path (see GOTCHA-SYS-01 /
    # test_syscall.py's test for this exact ID). GPIO in this experiment is
    # instead reachable through the IPC-based HAL_GPIO device (fireball://
    # device/gpio/0), not through fireball_call directly.
    TRIGGER_SET_PIN = 0x16
    VDMA_START = 0x20
    VIRQ_REGISTER = 0x30
    VIRQ_UNREGISTER = 0x31
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
    runtime_syscall.md's calling convention section: `fireball_call` returns 0 on success, else a
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
    IO = 29
    NOENT = 44
    NOMEM = 48
    NOSYS = 52
    PERM = 63
    NOTCAPABLE = 76


SyscallHandler = Callable[[int, int, int, int, int, int], int]


# runtime_vmmio.md §4.3: real static-device addresses.
IPCR_BASE = 0xC000_1000
_STATIC_DEVICE_PAGE_MASK = 0xFFFF_F000
FB_CONF_GUEST_RAM_SIZE = 4096  # system_config.md §3.3.4
FB_CONF_VSOC_PASSTHROUGH_BASE = 0xF000_0000  # runtime_vmmio.md §3.3's FC=15 window
_PASSTHROUGH_TEST_PAGES = 16  # this experiment's own arbitrary backing size,


class System:
    """
    One running Fireball-shaped host: a single platform I/O sink, a single SHM
        buffer pool, one dictionary logger, and a real vMMIO controller (FlatMap PTEs + TLB, reused from
        vmmio_concept.py) fronted by an IPCR static-device page
        and a PASSTHROUGH-backed physical memory window, and a real IPC router
        (reused from ipc_router_concept.py) with its fixed 3-service registry.
    """

    def __init__(self):
        self.wasi_hal_bindings = DEFAULT_WASI_HAL_BINDINGS
        self.transport = StreamTransport()
        self.dictionary = LogDictionary()
        self.logger = Logger(self.transport, self.dictionary, min_level=LogLevel.DEBUG)
        self.scheduler = Scheduler(logger=self.logger)
        # --- vMMIO: real FlatMap+TLB dispatch, this file's own byte
        # storage behind it (vmmio_concept.access() deliberately stops at the
        # dispatch decision -- see its module docstring -- it carries no
        # value/buffer of its own).
        self.vmmio = VMMIOController(
            guest_ram_size=FB_CONF_GUEST_RAM_SIZE, scheduler=self.scheduler
        )
        self.pool = HalBufferPool(self.scheduler, self.vmmio)
        self.ipcr_regs = bytearray(0x10)
        self.vmmio.map_static_device(vpn=IPCR_BASE >> 12)
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

        # Physical Memory Manager (system_memory.md contract / runtime_memory.md impl) with 64KB aligned pool
        # The scheduler is the sole source of the current task identity used by
        # ownership-sensitive memory operations.
        self.memory_manager = MemoryManager(self.scheduler)
        self.vmmio.register_to_memory_manager(self.memory_manager)
        self.memory_manager.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        self.ipc = IPCRouter(self.scheduler, logger=self.logger, memory_manager=self.memory_manager)
        self._channel_table: StaticVector[Channel] = StaticVector(capacity=FB_CONF_MAX_TASKS)
        # Direct 1-based index mapping over sorted self.ipc.registry.keys array (no dynamic dict)
        self.runtime_engine = RuntimeEngine()
        self.scheduler.set_idle_hook(self._on_idle)
        self.halted = False
        self.reset_requested = False
        self.exit_code: int | None = None
        self._guest_memory: bytearray | None = None
        self._bound_runtime_task: Task | None = None
        self._hal_task_storage: MutableFlatMapStorage[int, HalTask] = MutableFlatMapStorage(
            capacity=8
        )
        self._hal_task_index: ReadOnlyFlatMapStorage[int, HalTask] = ReadOnlyFlatMapStorage.create(
            ()
        )
        self.gdb_server: GDBServer | None = None
        self._gdb_task_id: int | None = None
        self.wasi_context: WasiHostContext | None = None
        # Build the small fireball_call dispatch table as read-only storage.
        syscall_entries: tuple[tuple[int, SyscallHandler], ...] = (
            (
                FbSyscallId.SYS_YIELD,
                lambda a0, a1, a2, a3, a4, a5: int(
                    self._apply_sys_control(int(FbSyscallId.SYS_YIELD))
                ),
            ),
            (
                FbSyscallId.SYS_HALT,
                lambda a0, a1, a2, a3, a4, a5: int(
                    self._apply_sys_control(int(FbSyscallId.SYS_HALT))
                ),
            ),
            (
                FbSyscallId.SYS_RESET,
                lambda a0, a1, a2, a3, a4, a5: int(
                    self._apply_sys_control(int(FbSyscallId.SYS_RESET))
                ),
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
                FbSyscallId.VIRQ_REGISTER,
                lambda a0, a1, a2, a3, a4, a5: int(self._virq_register(a0, a1)),
            ),
            (
                FbSyscallId.VIRQ_UNREGISTER,
                lambda a0, a1, a2, a3, a4, a5: int(self._virq_unregister(a0)),
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
        )
        syscall_entries = sorted(syscall_entries, key=lambda x: int(x[0]))
        self._syscall_handlers: ReadOnlyFlatMapStorage[int, SyscallHandler] = (
            ReadOnlyFlatMapStorage.create(syscall_entries)
        )
    def _on_idle(self) -> None:
        """COOS idle_hook dispatch: flushes deferred logs and compiles queued JIT traces."""
        self.logger.flush()
        self.runtime_engine.idle_hook(budget=4)

    def start_runtime_task(self, name: str = "runtime_host", role: Role = Role.RUNTIME) -> Task:
        """Starts an explicitly scheduler-owned task for a runtime host entrypoint."""
        assert self.scheduler.current_task is None, "A scheduler task is already active"
        task_id = self.scheduler.spawn(name, role=role)
        task = self.scheduler.get_task(task_id)
        assert task is not None
        self.scheduler.detach(task)
        self.scheduler.activate_task(task)
        return task

    def bind_runtime(self, memory: bytearray | None, role: Role = Role.RUNTIME) -> None:
        """
        Must be called before invoking guest code that will use
                `fb_offset_t` arguments (IPC_*/WASI_*): those are relative offsets
                into "the calling task's own guest memory" (runtime_syscall.md's
                calling convention section), which this single-tenant experiment
                models as one mutable binding set by the embedder rather than a
                per-task table.
        """
        self._guest_memory = memory
        # fireball_call's IPC_SEND/IPC_RECV delegate to scheduler.Channel,
        # which rendezvous on registered Task objects, not bare task_id ints.
        # This task is driven directly by fireball_call, never by the
        # scheduler's own run_until_idle() loop, so it must not sit in READY.
        task = self.scheduler.current_task
        if task is None:
            task = self.start_runtime_task(name="guest_task", role=role)
        assert task.role == role, "Guest binding role must match the current scheduler task"
        self.scheduler.require_active_task(task)
        self._bound_runtime_task = task
        self.pool.bind_runtime()

    def unbind_runtime(self) -> None:
        """Unmaps the fixed HAL DYNAMIC buffers from the bound Runtime."""
        self.pool.unbind_runtime()
        self._bound_runtime_task = None

    def dispatch_current_interrupt(self) -> DispatchResult | None:
        """Dispatch the event handed to the active vSoC runtime task at a safepoint."""
        task = self.scheduler.current_task
        assert task is not None, "interrupt dispatch requires an active task"
        assert task.role == Role.RUNTIME, "only the vSoC runtime task may dispatch interrupts"
        event = self.scheduler.consume_interrupt_event()
        if event is None:
            return None
        self.runtime_engine.commit_virq_safepoint()
        return self.runtime_engine.dispatch_interrupt_event(event)

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
                (runtime_syscall.md's WIT definition and calling convention): a
                single syscall-ID-dispatched bridge carrying `id` plus six
                generic u32 args, dispatched via the small read-only flat map storage.
        """

        assert self.scheduler.current_task is not None, (
            "fireball_call requires an active scheduler task"
        )
        handler = self._syscall_handlers.view().find(syscall_id)
        if handler is not None:
            return handler(arg0, arg1, arg2, arg3, arg4, arg5)
        return int(WasiErrno.NOSYS)

    # --- guest memory (fb_offset_t resolution) -------------------------
    def _guest_ram_ok(self, offset: int, length: int) -> bool:
        if self._guest_memory is None or offset < 0 or length < 0:
            return False
        memory_length = len(self._guest_memory)
        return offset <= memory_length and length <= memory_length - offset

    def _read_guest(self, offset: int, length: int) -> bytes | None:
        if not self._guest_ram_ok(offset, length):
            return None
        return bytes(self._guest_memory[offset : offset + length])

    def _write_guest(self, offset: int, data: bytes) -> bool:
        if not self._guest_ram_ok(offset, len(data)):
            return False
        self._guest_memory[offset : offset + len(data)] = data
        return True

    # --- System host calls (SYS_YIELD/HALT/RESET) -----------------------
    def _apply_sys_control(self, cmd: int) -> WasiErrno:
        """
        Applies a system-control effect directly for a host-call request.
        There is no SYSCTL vMMIO doorbell or register shadow.
        """
        if cmd == int(FbSyscallId.SYS_RESET):
            self.reset_requested = True
        elif cmd == int(FbSyscallId.SYS_YIELD):
            # {CooperativeMultitasking}: a real yield suspends the calling
            # coroutine until the scheduler resumes it. This experiment's
            # WASM JIT has no continuation/suspend mechanism -- a native
            # `call` into fireball_call runs to completion synchronously --
            # so there is nothing to suspend here. scheduler.py's own
            # generator-based yield is the actual host-side yield model for
            # the HAL demo; this path can only acknowledge the request.
            pass
        elif cmd == int(FbSyscallId.SYS_HALT):
            self.halted = True
        else:
            return WasiErrno.INVAL
        return WasiErrno.SUCCESS

    # --- vMMIO Generic (real FlatMap/TLB dispatch + real backing bytes) -
    def _trap_to_errno(self, status: VmmioStatus) -> WasiErrno | None:
        if (
            status == VmmioStatus.OK_STATIC_DEVICE
            or status == VmmioStatus.OK_PHYSICAL
            or status == VmmioStatus.OK_GUEST_RAM
        ):
            return None
        for trap_status, errno in (
            (TrapCode.OUT_OF_BOUNDS, WasiErrno.FAULT),
            (TrapCode.UNDEFINED_FC, WasiErrno.NOENT),
            (TrapCode.UNREGISTERED_PAGE, WasiErrno.NOENT),
            (TrapCode.ACCESS_VIOLATION, WasiErrno.PERM),
            (TrapCode.OWNER_MISMATCH, WasiErrno.PERM),
        ):
            if status == trap_status:
                return errno
        return WasiErrno.FAULT

    def _mmio_touch(
        self, addr: int, is_write: bool
    ) -> tuple[WasiErrno | None, bytearray | None, int | None]:
        """
        Runs the real permission dispatch, then resolves this
                experiment's own backing storage for the byte-level effect
                vmmio_concept.access() intentionally leaves to the caller.
                Returns (errno_or_None, backing_bytearray_or_None, local_offset).
        """

        access_status, _ = self.vmmio.access(addr, is_write)
        errno = self._trap_to_errno(access_status)
        if errno is not None:
            return errno, None, None
        a = VmmioAddress(addr)
        if a.fc() == FC_STATIC_DEVICE:
            page = addr & _STATIC_DEVICE_PAGE_MASK
            if page == IPCR_BASE:
                return None, self.ipcr_regs, a.offset()
            return WasiErrno.NOENT, None, None
        # Tier 3 (SHM / PASSTHROUGH): resolve the same phys_addr formula
        # vmmio_concept.access() itself already computed internally, from
        # the same public PTE fields it exposes (self.vmmio.ptes is a
        # public FlatMap, not a hidden implementation detail).
        pte = self.vmmio.ptes.view().find(a.vpn())
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

    # --- VDMA host call (no vMMIO register transport) -------------------
    def _vdma_start(self, src: int, dst: int, byte_count: int) -> WasiErrno:
        return self._run_vdma(src, dst, byte_count)

    def _vdma_region(
        self, addr: int, count: int, is_write: bool
    ) -> tuple[bytearray | None, int | None]:
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

    def _run_vdma(self, src: int, dst: int, count: int) -> WasiErrno:
        src_backing, src_off = self._vdma_region(src, count, is_write=False)
        if src_backing is None:
            return WasiErrno.FAULT
        dst_backing, dst_off = self._vdma_region(dst, count, is_write=True)
        if dst_backing is None:
            return WasiErrno.FAULT
        dst_backing[dst_off : dst_off + count] = bytes(src_backing[src_off : src_off + count])
        return WasiErrno.SUCCESS

    # --- vIRQ registration host calls ------------------------------------
    @staticmethod
    def _virq_errno(error: RegistrationError | None) -> WasiErrno:
        if error == RegistrationError.MODULE_UNAVAILABLE:
            return WasiErrno.NOSYS
        return WasiErrno.INVAL

    def _virq_register(self, node_id: int, function_index: int) -> WasiErrno:
        result = self.runtime_engine.register_virq_dispatcher(node_id, function_index)
        if result.is_ok:
            return WasiErrno.SUCCESS
        return self._virq_errno(result.error)

    def _virq_unregister(self, node_id: int) -> WasiErrno:
        result = self.runtime_engine.unregister_virq_dispatcher(node_id)
        if result.is_ok:
            return WasiErrno.SUCCESS
        return self._virq_errno(result.error)

    # --- IPC (real IPCRouter: URI lookup, RBAC, CSP rendezvous handoff) ---
    def _ipc_lookup(self, uri_offset: int, uri_len: int) -> int:
        raw = self._read_guest(uri_offset, uri_len)
        if raw is None:
            return int(WasiErrno.FAULT)
        try:
            uri = raw.decode("utf-8")
        except UnicodeDecodeError:
            return int(WasiErrno.INVAL)
        task = self.scheduler.current_task
        assert task is not None, "IPC lookup requires an active scheduler task"
        status, channel = self.ipc.lookup(uri)
        if status == IPCStatus.ERR_NOT_FOUND or channel is None:
            return int(WasiErrno.NOENT)
        if status == IPCStatus.ERR_PERMISSION_DENIED:
            return int(WasiErrno.PERM)
        if not self._channel_table.push_back(channel):
            return WasiErrno.NOMEM
        return len(self._channel_table)

    def _ipc_send(self, handle_id: int, msg_offset: int, msg_len: int) -> WasiErrno:
        if handle_id < 1 or handle_id > len(self._channel_table):
            return WasiErrno.BADF
        channel = self._channel_table[handle_id - 1]
        payload = self._read_guest(msg_offset, msg_len)
        if payload is None:
            return WasiErrno.FAULT
        task = self.scheduler.current_task
        assert task is not None, "IPC send requires an active scheduler task"
        msg = IPCMessage.from_entries(
            memory_manager=self.memory_manager,
            entries=bytes_to_kv_storage(payload),
        )

        gen = self.ipc.send(channel, msg)
        try:
            next(gen)
            # Counterpart is not ready yet: attach and step cooperatively
            task.coro = gen
            self.scheduler.attach(task)
            while (
                task.state != TaskState.TERMINATED
                and task.state != TaskState.READY
                and task.coro is not None
            ):
                self.scheduler.step()
            status, _ = task.result if task.result else (IPCStatus.COMPLETED, None)
        except StopIteration as e:
            # Direct O(1) rendezvous handoff (atomic ownership transfer)
            try:
                status, _ = e.value
            except (TypeError, ValueError):
                status = IPCStatus.COMPLETED
            self.scheduler.run_until_idle()

        if status == IPCStatus.COMPLETED:
            return WasiErrno.SUCCESS
        if status == IPCStatus.ERR_PERMISSION_DENIED:
            return WasiErrno.PERM
        return WasiErrno.NOENT

    def _ipc_recv(self, handle_id: int, buf_offset: int, buf_len: int) -> int:
        task = self.scheduler.current_task
        assert task is not None, "IPC receive requires an active scheduler task"

        gen = self.ipc.recv()
        try:
            next(gen)
            # Counterpart is not ready yet: attach and step cooperatively
            task.coro = gen
            self.scheduler.attach(task)
            while (
                task.state != TaskState.TERMINATED
                and task.state != TaskState.READY
                and task.coro is not None
            ):
                self.scheduler.step()
            status, msg = task.result if task.result else (IPCStatus.COMPLETED, None)
        except StopIteration as e:
            # Direct O(1) rendezvous handoff
            try:
                status, msg = e.value
            except (TypeError, ValueError):
                status, msg = IPCStatus.COMPLETED, None
            self.scheduler.run_until_idle()

        if (
            status == IPCStatus.ERR_NOT_FOUND
            or status == IPCStatus.ERR_PERMISSION_DENIED
            or msg is None
        ):
            return int(WasiErrno.NOENT)
        data = kv_entries_to_bytes(msg.entries, max_len=buf_len)
        n = len(data)
        if not self._write_guest(buf_offset, data):
            return int(WasiErrno.FAULT)
        return n

    # --- WASI (interface_wit.md §5.5-5.6) --------------------------------
    def _wasi_fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> WasiErrno:
        """Dispatches fd_write through the registered WASI-to-HAL adapter."""
        assert self.wasi_context is not None, "WASI fd_write requires a bound WASI context"
        result = self.wasi_context.fd_write(fd, iovs_ptr, iovs_len, nwritten_ptr)
        return WasiErrno(result)

    def _wasi_fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> WasiErrno:
        """Dispatches fd_read through the registered WASI-to-HAL adapter."""
        assert self.wasi_context is not None, "WASI fd_read requires a bound WASI context"
        result = self.wasi_context.fd_read(fd, iovs_ptr, iovs_len, nread_ptr)
        return WasiErrno(result)

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

    def start_hal_driver(self, driver: HalDriver) -> int:
        """Registers and starts one driver-owned HAL device task."""
        driver.bind_buffer_pool(self.pool)
        desc = self.ipc.find_service(driver.uri)
        assert desc is not None, f"HAL driver URI not registered: {driver.uri}"
        uri_key = fnv1a_32(driver.uri)
        assert self._hal_task_index.view().find(uri_key) is None, (
            f"duplicate HAL driver URI: {driver.uri}"
        )
        task_id, task = driver.start(self.ipc, self.scheduler, desc.role)
        assert self._hal_task_storage.insert(uri_key, task)
        self._hal_task_index = ReadOnlyFlatMapStorage.create(self._hal_task_storage.view().entries)
        return task_id

    def hal_task_for(self, uri: str) -> HalTask | None:
        """Returns the dedicated HalTask instance bound to `uri`, if spawned."""
        return self._hal_task_index.view().find(fnv1a_32(uri))

    def spawn_gdbserver_task(
        self,
        dbg: DebuggerManager,
        start_pc: int = 0,
        ctx: WASMContext | None = None,
        blocks: Mapping[int, BasicBlock] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> tuple[int, int]:
        """Spawns the GDB Server Task on the COOS scheduler (debug_manager.md).
        GDBServer runs as a cooperative task communicating via non-blocking TCP socket.
        Returns: (task_id, bound_port).
        """
        from gdb_server import GDBServer

        gdb_srv = GDBServer(dbg, host=host, port=port)
        bound_port = gdb_srv.bind_socket()
        self.gdb_server = gdb_srv
        task_id = self.scheduler.spawn(
            "gdbserver_task",
            gdb_srv.run_task(
                start_pc,
                ctx,
                blocks or MutableFlatMapStorage[int, BasicBlock](capacity=0),
            ),
        )
        self._gdb_task_id = task_id
        return (task_id, bound_port)

    def shutdown(self) -> None:
        if self.gdb_server is not None:
            self.gdb_server.stop()
        for _, task in self._hal_task_index.entries:
            task.running = False
        self.pool.close_all()
        self.transport.close()
