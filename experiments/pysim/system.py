"""
experiments/pysim/system.py
Wires HAL + Logger/ConsoleOutput + the recovery-strategy engine + the real
generic fireball_call and dedicated vIRQ/vDMA host-call surfaces into one
running system.
fireball_call's host-call ID space and error-code convention adhere
strictly to the architectural specifications:
- `docs/components/tier2_runtime/runtime_syscall.md` defines the real ID table
- `docs/components/tier2_runtime/runtime_vmmio.md` defines the vMMIO address layout
- `docs/components/tier1_interface/ipc_router.md` defines the URI-routed, zero-copy request/reply CSP
This module uses self-contained simulation modules (`vmmio.py`, `ipc_router.py`,
`tier2_runtime/memory/manager.py`) mirroring the authoritative concept models, and provides
    the actual byte-level storage and wire-level u32 handle numbering
required for end-to-end execution.
All guest output routes through WASI_FD_WRITE (console-output) to adhere strictly
to runtime_logging.md and interface_wit.md's "console-output" section (dictionary
logger is internal-only).
"""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from typing import TYPE_CHECKING

from ipc_router import (
    FB_CONF_ROUTER_MAX_KV_PAIRS,
    IPCMessage,
    IPCRouter,
    IPCStatus,
    Role,
    bytes_to_kv_storage,
    kv_entries_to_bytes,
)
from tier2_runtime.hal.dispatch import HalBufferPool
from tier2_runtime.syscall.hostcall import (
    FbSyscallId,
    RuntimeHostCallGateway,
    SyscallHandler,
    WasiErrno,
    WasiPreview1Host,
)

if TYPE_CHECKING:
    from tier1_core.printk import PrintkWriter
    from tier2_runtime.hal.dispatch import HalDriver, HalTask
    from tier2_runtime.interpreter.execution_context import DebugExecutionView
    from tier2_runtime.interpreter.interpreter import (
        BasicBlock,
        NativeInterpreter,
        WasmNumber,
    )
    from tier3_plugins.debugger.debugger import DebuggerManager
    from tier3_plugins.debugger.gdb_server import GDBServer

from scheduler import FB_CONF_MAX_TASKS, Channel, ChannelAction, Scheduler, Task, TaskState
from system_containers import MutableFlatMapStorage, ReadOnlyFlatMapStorage, StaticVector
from tier1_core.fnv1a import fnv1a_32
from tier2_runtime.memory.manager import (
    FB_CONF_MEMORY_POOL_SIZE,
    MemoryManager,
)
from tier2_runtime.observability.logger import LogDictionary, Logger, LogLevel
from tier2_runtime.runtime.engine import RuntimeDriveMode, RuntimeEngine
from tier2_runtime.vmmio.controller import (
    FC_DYNAMIC,
    FC_SHM,
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
    VmmioStatus,
)
from tier2_runtime.vsoc.virq import DispatchResult
from tier2_runtime.wasm.module import BasicBlock
from tier3_platform.drivers.platform_config import (
    PlatformDriverConfiguration,
    create_default_platform_drivers,
)

# runtime_vmmio.md §4.3: real static-device addresses.
IPCR_BASE = 0xC000_1000
_STATIC_DEVICE_PAGE_MASK = 0xFFFF_F000
FB_CONF_GUEST_RAM_SIZE = 4096  # system_config.md §3.3.4
FB_CONF_VSOC_PASSTHROUGH_BASE = 0xF000_0000  # runtime_vmmio.md §3.3's FC=15 window
_PASSTHROUGH_TEST_PAGES = 16  # this experiment's own arbitrary backing size,


class System:
    __slots__ = (
        "_bound_runtime_task",
        "_channel_table",
        "_gdb_task_id",
        "_guest_memory",
        "_hal_task_storage",
        "_yield_requested",
        "dictionary",
        "drivers",
        "exit_code",
        "gdb_server",
        "halted",
        "host_calls",
        "ipc",
        "ipcr_regs",
        "logger",
        "memory_manager",
        "phys_mem",
        "pool",
        "printk",
        "reset_requested",
        "runtime_engine",
        "scheduler",
        "transport",
        "vmmio",
        "wasi_backend",
        "wasi_context",
    )

    """
    One running Fireball-shaped host: a single platform I/O sink, a single SHM
        buffer pool, one dictionary logger, and a real vMMIO controller (FlatMap PTEs + TLB, reused from
        vmmio_concept.py) fronted by an IPCR static-device page
        and a PASSTHROUGH-backed physical memory window, and a real IPC router
        (reused from ipc_router_concept.py) with its fixed 3-service registry.
    """

    def __init__(
        self,
        printk_sink: PrintkWriter | None = None,
        drivers: PlatformDriverConfiguration | None = None,
        log_dictionary: LogDictionary | None = None,
    ):
        self.drivers = (
            drivers if drivers is not None else create_default_platform_drivers(printk_sink)
        )
        self.wasi_backend = self.drivers.wasi_backend
        self.printk = self.drivers.printk
        self.transport = self.drivers.stdout_transport
        self.dictionary = log_dictionary if log_dictionary is not None else LogDictionary()
        self.logger = Logger(self.printk, self.dictionary, min_level=LogLevel.DEBUG)
        self.scheduler = Scheduler(printk=self.printk)
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

        # Simulated Memory Manager with a 64KB-aligned pool base (WasmPageAlignment).
        # The scheduler is the sole source of the current task identity used by
        # ownership-sensitive memory operations.
        self.memory_manager = MemoryManager(self.scheduler)
        self.vmmio.register_to_memory_manager(self.memory_manager)
        self.memory_manager.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        self.ipc = IPCRouter(
            self.scheduler,
            printk=self.printk,
            memory_manager=self.memory_manager,
        )
        self._channel_table: StaticVector[Channel] = StaticVector(capacity=FB_CONF_MAX_TASKS)
        # Direct 1-based index mapping over sorted self.ipc.registry.keys array (no dynamic dict)
        self.runtime_engine = RuntimeEngine(drive_mode=RuntimeDriveMode.COOS)
        self.scheduler.set_idle_hook(self._on_idle)
        self.halted = False
        self.reset_requested = False
        self.exit_code: int | None = None
        self._guest_memory: bytearray | None = None
        self._yield_requested = False
        self._bound_runtime_task: Task | None = None
        self._hal_task_storage: MutableFlatMapStorage[int, HalTask] = MutableFlatMapStorage(
            capacity=8
        )
        self.gdb_server: GDBServer | None = None
        self._gdb_task_id: int | None = None
        self.wasi_context: WasiPreview1Host | None = None
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
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_read(a0, a1, 4)),
            ),
            (
                FbSyscallId.MMIO_WRITE32,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_write(a0, a1, 4)),
            ),
            (
                FbSyscallId.MMIO_READ8,
                lambda a0, a1, a2, a3, a4, a5: int(self._mmio_read(a0, a1, 1)),
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
                FbSyscallId.IPC_SEND,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_send(a0, a1, a2, a3)),
            ),
            (
                FbSyscallId.IPC_RECV,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_recv(a0, a1, a2, a3)),
            ),
            (
                FbSyscallId.IPC_LOOKUP,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_lookup(a0, a1, a2)),
            ),
            (
                FbSyscallId.IPC_REPLY,
                lambda a0, a1, a2, a3, a4, a5: int(self._ipc_reply(a0, a1)),
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
                lambda a0, a1, a2, a3, a4, a5: int(self._wasi_clock_time_get(a0, a1, a2)),
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
        syscall_handlers: ReadOnlyFlatMapStorage[int, SyscallHandler] = (
            ReadOnlyFlatMapStorage.create(syscall_entries)
        )
        self.host_calls = RuntimeHostCallGateway(
            self.scheduler,
            syscall_handlers,
            self.runtime_engine,
            self._vdma_start,
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

    def run_guest(
        self,
        interp: NativeInterpreter,
        func_index: int,
        args: Sequence[WasmNumber],
        idle_budget: int = 4,
    ) -> Generator[tuple[ChannelAction, None], None, StaticVector[WasmNumber]]:
        """Drive one shared runtime boundary at a time and own COOS handoffs."""
        call_state = interp.start(func_index, args)
        while not call_state.finished:
            boundary = self.runtime_engine.run(interp, call_state, idle_budget)
            call_state = boundary.call_state
            task = self.scheduler.current_task
            assert task is not None, "guest execution requires an active COOS task"
            generation_yield = self.scheduler.observe_reschedule_generation(task)
            syscall_yield = self._yield_requested
            self._yield_requested = False
            if not call_state.finished and (
                boundary.yield_requested or generation_yield or syscall_yield
            ):
                self.runtime_engine.on_yield()
                yield (ChannelAction.YIELD, None)
        return self.runtime_engine.complete_call(interp, call_state, idle_budget)

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

    def attach_wasi_context(self, context: WasiPreview1Host) -> None:
        """Connect the Tier 2 Preview 1 host implementation used by syscall handlers."""
        self.wasi_context = context

    def unbind_runtime(self) -> None:
        """Unbinds guest linear memory; DYNAMIC mappings are operation-scoped."""
        self._bound_runtime_task = None

    def dispatch_current_interrupt(self) -> DispatchResult | None:
        """Dispatch the event handed to the active vSoC task at a COOS yield boundary."""
        task = self.scheduler.current_task
        assert task is not None, "interrupt dispatch requires an active task"
        assert task.role == Role.RUNTIME, "only the vSoC runtime task may dispatch interrupts"
        event = self.scheduler.consume_interrupt_event()
        if event is None:
            return None
        self.runtime_engine.commit_virq_registrations()
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

        return self.host_calls.fireball_call(syscall_id, arg0, arg1, arg2, arg3, arg4, arg5)

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
            # The synchronous host call records a request. run_guest() observes
            # it after the current RuntimeEngine boundary and yields its COOS
            # generator there, where the interpreter continuation is resumable.
            self._yield_requested = True
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
        self, addr: int, is_write: bool, access_size: int = 1
    ) -> tuple[WasiErrno | None, memoryview | None, int | None]:
        """
        Run the permission dispatch and borrow the matching backing storage.
        The permission gate leaves the byte-level effect to its caller.
        Return (errno_or_None, borrowed_storage_or_None, local_offset).
        """

        access_status, _ = self.vmmio.access(addr, is_write, access_size=access_size)
        errno = self._trap_to_errno(access_status)
        if errno is not None:
            return errno, None, None
        a = VmmioAddress(addr)
        if a.fc() == FC_STATIC_DEVICE:
            page = addr & _STATIC_DEVICE_PAGE_MASK
            if page == IPCR_BASE:
                return None, memoryview(self.ipcr_regs), a.offset()
            return WasiErrno.NOENT, None, None
        # Resolve the permitted DYNAMIC buffer or SHM/PASSTHROUGH backing.
        pte = self.vmmio.ptes.view().find(a.vpn())
        assert pte is not None
        if a.fc() == FC_DYNAMIC:
            handle = self.pool.buffer(pte.phys_page)
            if not self.pool.can_view(handle, a.offset(), access_size):
                return WasiErrno.FAULT, None, None
            return None, self.pool.view(handle, a.offset(), access_size), 0
        if a.fc() == FC_SHM and pte.mapped_storage is not None:
            return None, pte.mapped_storage, a.offset()
        phys_addr = pte.physical_base_addr + a.offset()
        return None, memoryview(self.phys_mem), phys_addr

    def _mmio_read(self, addr: int, value_out_ptr: int, width: int) -> WasiErrno:
        # The raw host-call return is reserved for errno. Validate the output
        # before touching a potentially side-effecting MMIO register.
        if not self._guest_ram_ok(value_out_ptr, 4):
            return WasiErrno.FAULT
        errno, backing, off = self._mmio_touch(addr, is_write=False, access_size=width)
        if errno is not None:
            return errno
        assert backing is not None and off is not None
        if off + width > len(backing):
            return WasiErrno.FAULT
        value = int.from_bytes(backing[off : off + width], "little")
        if not self._write_guest(value_out_ptr, value.to_bytes(4, "little")):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _mmio_write(self, addr: int, value: int, width: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=True, access_size=width)
        if errno is not None:
            return errno
        if off + width > len(backing):
            return WasiErrno.FAULT
        backing[off : off + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
        return WasiErrno.SUCCESS

    def _mmio_bulk_read(self, addr: int, dest_offset: int, byte_count: int) -> WasiErrno:
        if not self._guest_ram_ok(dest_offset, byte_count):
            return WasiErrno.FAULT
        errno, backing, off = self._mmio_touch(addr, is_write=False, access_size=byte_count)
        if errno is not None:
            return errno
        if off + byte_count > len(backing):
            return WasiErrno.FAULT
        if not self._write_guest(dest_offset, bytes(backing[off : off + byte_count])):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _mmio_bulk_write(self, addr: int, src_offset: int, byte_count: int) -> WasiErrno:
        data = self._read_guest(src_offset, byte_count)
        if data is None:
            return WasiErrno.FAULT
        errno, backing, off = self._mmio_touch(addr, is_write=True, access_size=byte_count)
        if errno is not None:
            return errno
        if off + byte_count > len(backing):
            return WasiErrno.FAULT
        backing[off : off + byte_count] = data
        return WasiErrno.SUCCESS

    # --- VDMA host call (no vMMIO register transport) -------------------
    def _vdma_start(self, src: int, dst: int, byte_count: int) -> WasiErrno:
        return self._run_vdma(src, dst, byte_count)

    def _vdma_region(
        self, addr: int, count: int, is_write: bool
    ) -> tuple[memoryview | None, int | None]:
        """
        runtime_vmmio.md §4.5: VDMA src/dst may be guest RAM (Tier 1) or
                vMMIO FC=13/14/15 -- resolved through the same permission gate
                as a direct access, owner checks included.
        """

        a = VmmioAddress(addr)
        if a.is_linear():
            return (
                (memoryview(self._guest_memory), addr)
                if self._guest_ram_ok(addr, count)
                else (None, None)
            )
        errno, backing, off = self._mmio_touch(addr, is_write, access_size=count)
        if errno is not None or backing is None or off is None or off + count > len(backing):
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

    def vdma_transfer(self, src: int, dst: int, count: int) -> int:
        """Provide the synchronous transfer callback used by `memory.copy`."""
        return int(self._run_vdma(src, dst, count))

    # --- IPC (real IPCRouter: URI lookup, RBAC, CSP rendezvous handoff) ---
    def _ipc_lookup(self, uri_offset: int, uri_len: int, handle_out_ptr: int) -> WasiErrno:
        if not self._guest_ram_ok(uri_offset, uri_len):
            return WasiErrno.FAULT
        if not self._guest_ram_ok(handle_out_ptr, 4):
            return WasiErrno.FAULT
        raw = self._read_guest(uri_offset, uri_len)
        if raw is None:
            return WasiErrno.FAULT
        try:
            uri = raw.decode("utf-8")
        except UnicodeDecodeError:
            return WasiErrno.INVAL
        task = self.scheduler.current_task
        assert task is not None, "IPC lookup requires an active scheduler task"
        status, channel = self.ipc.lookup(uri)
        if status == IPCStatus.ERR_NOT_FOUND:
            return WasiErrno.NOENT
        if status == IPCStatus.ERR_PERMISSION_DENIED:
            return WasiErrno.PERM
        assert channel is not None, "successful IPC lookup must return a channel"
        if not self._channel_table.push_back(channel):
            return WasiErrno.NOMEM
        handle_id = len(self._channel_table)
        assert self._write_guest(handle_out_ptr, handle_id.to_bytes(4, "little"))
        return WasiErrno.SUCCESS

    def _ipc_send(
        self, handle_id: int, msg_offset: int, msg_len: int, response_code_ptr: int = 0
    ) -> WasiErrno:
        if handle_id < 1 or handle_id > len(self._channel_table):
            return WasiErrno.BADF
        channel = self._channel_table[handle_id - 1]
        if not self._guest_ram_ok(msg_offset, msg_len):
            return WasiErrno.FAULT
        if response_code_ptr != 0 and not self._guest_ram_ok(response_code_ptr, 4):
            return WasiErrno.FAULT
        # The byte bridge reserves one KV pair for the payload length and uses
        # one pair for each four-byte chunk. Reject oversize input before
        # allocating/populating the fixed-size shared message block.
        if (msg_len + 3) // 4 + 1 > FB_CONF_ROUTER_MAX_KV_PAIRS:
            return WasiErrno.MSGSIZE
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
            task.result = None
            task.coro = gen
            if task.state == TaskState.READY:
                self.scheduler.attach(task)
            while task.result is None and task.coro is gen:
                self.scheduler.step()
            status, response = task.result if task.result else (IPCStatus.COMPLETED, None)
            task.result = None
            task.coro = None
            task.state = TaskState.READY
            self.scheduler.current_task = task
        except StopIteration as e:
            # Direct O(1) rendezvous handoff (atomic ownership transfer)
            try:
                status, response = e.value
            except TypeError, ValueError:
                status, response = IPCStatus.COMPLETED, None
            self.scheduler.run_until_idle()

        if status == IPCStatus.COMPLETED:
            if response_code_ptr != 0:
                if response is None or not self._write_guest(
                    response_code_ptr, response.response_code.to_bytes(4, "little")
                ):
                    return WasiErrno.FAULT
            return WasiErrno.SUCCESS
        if status == IPCStatus.ERR_PERMISSION_DENIED:
            return WasiErrno.PERM
        if status == IPCStatus.ERR_MSG_TOO_LARGE:
            return WasiErrno.MSGSIZE
        return WasiErrno.NOENT

    def _ipc_recv(
        self, handle_id: int, buf_offset: int, buf_len: int, recv_len_out_ptr: int
    ) -> WasiErrno:
        # Validate before entering the blocking CSP receive. An invalid buffer
        # must not consume a sender's message or suspend the calling task.
        if not self._guest_ram_ok(buf_offset, buf_len):
            return WasiErrno.FAULT
        if not self._guest_ram_ok(recv_len_out_ptr, 4):
            return WasiErrno.FAULT
        if buf_offset < recv_len_out_ptr + 4 and recv_len_out_ptr < buf_offset + buf_len:
            return WasiErrno.INVAL
        max_payload_size = (FB_CONF_ROUTER_MAX_KV_PAIRS - 1) * 4
        if buf_len < max_payload_size:
            return WasiErrno.MSGSIZE
        task = self.scheduler.current_task
        assert task is not None, "IPC receive requires an active scheduler task"

        gen = self.ipc.recv()
        try:
            next(gen)
            # Counterpart is not ready yet: attach and step cooperatively
            task.result = None
            task.coro = gen
            if task.state == TaskState.READY:
                self.scheduler.attach(task)
            while task.result is None and task.coro is gen:
                self.scheduler.step()
            status, msg = task.result if task.result else (IPCStatus.COMPLETED, None)
            task.result = None
            task.coro = None
            task.state = TaskState.READY
            self.scheduler.current_task = task
        except StopIteration as e:
            # Direct O(1) rendezvous handoff
            try:
                status, msg = e.value
            except TypeError, ValueError:
                status, msg = IPCStatus.COMPLETED, None
            self.scheduler.run_until_idle()

        if status == IPCStatus.ERR_PERMISSION_DENIED:
            return WasiErrno.PERM
        if status == IPCStatus.ERR_NOT_FOUND or msg is None:
            return WasiErrno.NOENT
        data = kv_entries_to_bytes(msg.entries, max_len=buf_len)
        n = len(data)
        if not self._write_guest(buf_offset, data):
            return WasiErrno.FAULT
        assert self._write_guest(recv_len_out_ptr, n.to_bytes(4, "little"))
        task.pending_reply = msg
        return WasiErrno.SUCCESS

    def _ipc_reply(self, handle_id: int, response_code: int) -> WasiErrno:
        """Reply to the most recent IPC_RECV message on the current task."""
        task = self.scheduler.current_task
        assert task is not None, "IPC reply requires an active scheduler task"
        pending = task.pending_reply
        if pending is None:
            return WasiErrno.INVAL
        status = self.ipc.reply(pending, response_code)
        task.pending_reply = None
        self.scheduler.run_until_idle()
        return WasiErrno.SUCCESS if status == IPCStatus.COMPLETED else WasiErrno.IO

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
        assert self.wasi_context is not None, "WASI fd_close requires a bound WASI context"
        return WasiErrno(self.wasi_context.fd_close(fd))

    def _wasi_clock_time_get(self, clock_id: int, precision: int, time_ptr: int) -> WasiErrno:
        assert self.wasi_context is not None, "WASI clock_time_get requires a bound WASI context"
        return WasiErrno(self.wasi_context.clock_time_get(clock_id, precision, time_ptr))

    def _wasi_proc_exit(self, exit_code: int) -> WasiErrno:
        self.halted = True
        self.exit_code = exit_code
        return WasiErrno.SUCCESS

    def _wasi_random_get(self, buf_ptr: int, buf_len: int) -> WasiErrno:
        assert self.wasi_context is not None, "WASI random_get requires a bound WASI context"
        return WasiErrno(self.wasi_context.random_get(buf_ptr, buf_len))

    def start_hal_driver(self, driver: HalDriver, uri: str) -> int:
        """Registers and starts one driver-owned HAL device task."""
        driver.bind_buffer_pool(self.pool)
        desc = self.ipc.find_service(uri)
        assert desc is not None, f"HAL driver URI not registered: {uri}"
        uri_key = fnv1a_32(uri)
        assert self._hal_task_storage.view().find(uri_key) is None, (
            f"duplicate HAL driver URI: {uri}"
        )
        task_id, task = driver.start(
            self.ipc, self.scheduler, desc.role, self.ipc.lookup_service_handle(uri)
        )
        assert self._hal_task_storage.insert(uri_key, task)
        return task_id

    def hal_task_for(self, uri: str) -> HalTask | None:
        """Returns the dedicated HalTask instance bound to `uri`, if spawned."""
        return self._hal_task_storage.view().find(fnv1a_32(uri))

    def spawn_gdbserver_task(
        self,
        dbg: DebuggerManager,
        start_pc: int = 0,
        ctx: DebugExecutionView | None = None,
        blocks: Mapping[int, BasicBlock] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> tuple[int, int]:
        """Spawns the GDB Server Task on the COOS scheduler (debugger.md).
        GDBServer runs as a cooperative task communicating via non-blocking TCP socket.
        Returns: (task_id, bound_port).
        """
        from tier3_plugins.debugger.gdb_server import GDBServer

        gdb_srv = GDBServer(
            dbg,
            host=host,
            port=port,
            transport=self.drivers.debugger_sink,
        )
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
        for _, task in self._hal_task_storage:
            task.running = False
        self.pool.close_all()
        self.wasi_backend.close()
        self.transport.close()
