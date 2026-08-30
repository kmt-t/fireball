"""
experiments/pysim/system.py

Wires HAL + Logger/ConsoleOutput + the recovery-strategy engine + the real
fireball_call syscall surface into one running system.

fireball_call's ID space, register layout and error-code convention are
NOT reinvented here: `docs/components/tier1_core/system_syscall.md` §5
defines the real ID table, `docs/components/tier2_runtime/runtime_vmmio.md`
defines the real vMMIO address/register layout, and both
`docs/components/tier2_runtime/concepts/vmmio_concept.py` and
`docs/components/tier1_interface/concepts/ipc_router_concept.py` are
declared authoritative reference implementations for the FlatMap/TLB
permission dispatch and the URI-routed, ownership-transferring message
queue respectively. This module imports and reuses those two concept
modules directly instead of re-deriving their logic, and fills in only
what they explicitly leave to the caller: the actual register/byte-level
read-write effects `VMMIOController.access()` stops short of (see its
module docstring), and the wire-level u32 handle numbering
`IPCRouter` has no concept of.

FINDING (this file's previous version): it invented `FB_SYSCALL_LOG=1` and
`FB_SYSCALL_IPC_SEND=2` without checking them against system_syscall.md's
real ID table -- `0x01` there is already `SYS_YIELD`, and IPC_SEND is
`0x40`, not `2`. Worse, system_logging.md 1 explicitly scopes the
dictionary logger to "build-time-registered internal state logs only" and
calls out `wasi:cli/stdout`/`stderr` (interface_wit.md 5.5's
`console-output`) as the *only* guest-facing text-output path -- there
never was a legitimate "guest calls the dictionary logger" syscall for
`FB_SYSCALL_LOG` to model. Both are fixed below by adopting the real ID
table and routing guest output through `WASI_FD_WRITE` instead.
"""

from __future__ import annotations

import os
import struct
import sys
import time
from dataclasses import dataclass
from enum import IntEnum

from hal import ShmBufferPool, ShmHandle, UartTransport
from logger import ConsoleOutput, LogDictionary, Logger, LogLevel

# --- Reuse the declared-authoritative reference concept implementations ---
# instead of re-deriving their logic (both docs explicitly say not to keep a
# second copy of them).
_DOCS_COMPONENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "components")
for _sub in (("tier2_runtime", "concepts"), ("tier1_interface", "concepts"), ("tier1_core", "concepts")):
    _p = os.path.join(_DOCS_COMPONENTS, *_sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from vmmio_concept import (  # noqa: E402
    FC_PASSTHROUGH,
    FC_SHM,
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
)
from ipc_router_concept import IPCMessage, IPCRouter  # noqa: E402


class FbSyscallId(IntEnum):
    """system_syscall.md §5's real per-category ID table (not a subset picked
    for convenience -- every ID this experiment can plausibly back with real
    behavior is included; ones it can't yet (see README's missing-spec list)
    still route here and fail with a real WASI errno, not silently vanish)."""
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
    """system_syscall.md §4.2: `fireball_call` returns 0 on success, else a
    "WASIのerrno_t に準拠" error code -- the real wasi_snapshot_preview1
    numeric table, not a project-invented sentinel. Only the subset this
    file actually returns is enumerated; values match the real table's
    fixed alphabetical-after-e2big numbering exactly, so adding more later
    is just adding more real entries, never renumbering these."""
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

FB_CONF_GUEST_RAM_SIZE = 4096       # system_config.md §3.3.4
FB_CONF_VSOC_PASSTHROUGH_BASE = 0xF000_0000  # runtime_vmmio.md §3.3's FC=15 window
_PASSTHROUGH_TEST_PAGES = 16         # this experiment's own arbitrary backing size,
                                      # not a spec constant -- real PASSTHROUGH size
                                      # depends on the host peripherals actually mapped


@dataclass(frozen=True)
class ShmSlice:
    """interface_wit.md 5.3's `shm-slice{handle, offset, len}`. There is no
    field here that could ever carry a guest linear-memory address -- only
    a handle name the pool must independently recognize and authorize."""
    handle: ShmHandle
    offset: int
    len: int


class BusMaster:
    """`fireball:host/bus`'s `bus-master.transfer-data`, resolved to the
    real shared-memory pool."""

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
    """One running Fireball-shaped host: a single UART line, a single SHM
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
            self.vmmio.map_passthrough_page(vpn=(FB_CONF_VSOC_PASSTHROUGH_BASE >> 12) + i, phys_page=i)

        # KNOWN GAP (see README's missing-spec list): this SHM window and
        # hal.py's ShmBufferPool are two independent implementations of
        # "shared memory" that are not yet unified -- a real system has
        # exactly one. This experiment does not yet register ShmBufferPool
        # handles as vMMIO FC=14 pages.

        self.ipc = IPCRouter()
        self._ipc_handle_by_uri: dict[str, int] = {}
        self._ipc_uri_by_handle: dict[int, str] = {}
        for i, uri in enumerate(self.ipc.registry.keys, start=1):
            self._ipc_handle_by_uri[uri] = i
            self._ipc_uri_by_handle[i] = uri

        self.halted = False
        self.reset_requested = False
        self.exit_code: int | None = None

        self._guest_memory: bytearray | None = None
        self._current_task_id = 0

    def bus_master(self, task_id: int) -> BusMaster:
        return BusMaster(self.pool, task_id)

    def bus_slave(self, task_id: int) -> BusSlave:
        return BusSlave(self.pool, task_id)

    def bind_guest(self, memory: bytearray | None, task_id: int = 1) -> None:
        """Must be called before invoking guest code that will use
        `fb_offset_t` arguments (IPC_*/WASI_*): those are relative offsets
        into "the calling task's own guest memory" (system_syscall.md
        §4.1), which this single-tenant experiment models as one mutable
        binding set by the embedder rather than a per-task table."""
        self._guest_memory = memory
        self._current_task_id = task_id

    # --- fireball_call ------------------------------------------------

    def fireball_call(self, syscall_id: int, arg0: int, arg1: int, arg2: int,
                       arg3: int, arg4: int, arg5: int) -> int:
        """The one host import a guest actually needs
        (system_syscall.md §3-4): a single syscall-ID-dispatched bridge
        carrying `id` plus six generic u32 args. The real spec offers
        `fireball-call0`..`fireball-call6` as arity-specific variants purely
        to avoid marshalling unused args; since x64_jit.py's host-call glue
        already handles any arity uniformly, this experiment exposes only
        the richest (6-arg) form and lets a guest pass zeros it doesn't need.
        """
        try:
            sid = FbSyscallId(syscall_id)
        except ValueError:
            return int(WasiErrno.NOSYS)

        if sid == FbSyscallId.SYS_YIELD:
            return int(self._apply_sys_control(SYS_CONTROL_YIELD))
        if sid == FbSyscallId.SYS_HALT:
            return int(self._apply_sys_control(SYS_CONTROL_HALT))
        if sid == FbSyscallId.SYS_RESET:
            return int(self._apply_sys_control(SYS_CONTROL_RESET))

        if sid == FbSyscallId.MMIO_READ32:
            return self._mmio_read(arg0, 4)
        if sid == FbSyscallId.MMIO_WRITE32:
            return int(self._mmio_write(arg0, arg1, 4))
        if sid == FbSyscallId.MMIO_READ8:
            return self._mmio_read(arg0, 1)
        if sid == FbSyscallId.MMIO_WRITE8:
            return int(self._mmio_write(arg0, arg1, 1))
        if sid == FbSyscallId.MMIO_BULK_READ:
            return int(self._mmio_bulk_read(arg0, arg1, arg2))
        if sid == FbSyscallId.MMIO_BULK_WRITE:
            return int(self._mmio_bulk_write(arg0, arg1, arg2))

        if sid == FbSyscallId.VDMA_START:
            return int(self._vdma_start(arg0, arg1, arg2))

        if sid == FbSyscallId.IRQ_READ_FLAGS:
            return self._irq_read_flags()
        if sid == FbSyscallId.IRQ_CLEAR:
            return int(self._irq_clear(arg0))

        if sid == FbSyscallId.IPC_SEND:
            return int(self._ipc_send(arg0, arg1, arg2))
        if sid == FbSyscallId.IPC_RECV:
            return int(self._ipc_recv(arg0, arg1, arg2))
        if sid == FbSyscallId.IPC_LOOKUP:
            return self._ipc_lookup(arg0, arg1)

        if sid == FbSyscallId.WASI_FD_WRITE:
            return int(self._wasi_fd_write(arg0, arg1, arg2, arg3))
        if sid == FbSyscallId.WASI_FD_READ:
            return int(self._wasi_fd_read(arg0, arg1, arg2, arg3))
        if sid == FbSyscallId.WASI_FD_CLOSE:
            return int(self._wasi_fd_close(arg0))
        if sid == FbSyscallId.WASI_CLOCK_TIME_GET:
            return int(self._wasi_clock_time_get(arg2))
        if sid == FbSyscallId.WASI_PROC_EXIT:
            return int(self._wasi_proc_exit(arg0))
        if sid == FbSyscallId.WASI_RANDOM_GET:
            return int(self._wasi_random_get(arg0, arg1))

        return int(WasiErrno.NOSYS)

    # --- guest memory (fb_offset_t resolution) -------------------------

    def _guest_ram_ok(self, offset: int, length: int) -> bool:
        return self._guest_memory is not None and 0 <= offset and offset + length <= len(self._guest_memory)

    def _read_guest(self, offset: int, length: int) -> bytes | None:
        if not self._guest_ram_ok(offset, length):
            return None
        return bytes(self._guest_memory[offset:offset + length])

    def _write_guest(self, offset: int, data: bytes) -> bool:
        if not self._guest_ram_ok(offset, len(data)):
            return False
        self._guest_memory[offset:offset + len(data)] = data
        return True

    # --- System (SYS_YIELD/HALT/RESET, real REG_SYS_CONTROL semantics) --

    def _apply_sys_control(self, cmd: int) -> WasiErrno:
        """runtime_vmmio.md §4.4's REG_SYS_CONTROL: `1`=Reset, `2`=Yield,
        `3`=Halt. `fireball_call`'s SYS_YIELD/HALT/RESET IDs are the
        "cannot do a raw vMMIO store" proxy for writing this exact
        register (system_syscall.md §2's "アクセスパスB"), so both paths
        funnel through this one real effect."""
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
        """Runs the real permission dispatch, then resolves this
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
        pte = self.vmmio.ptes.get(a.vpn())
        phys_addr = (pte.phys_page << 12) | a.offset()
        return None, self.phys_mem, phys_addr

    def _mmio_read(self, addr: int, width: int) -> int:
        errno, backing, off = self._mmio_touch(addr, is_write=False)
        if errno is not None:
            return int(errno)
        if off + width > len(backing):
            return int(WasiErrno.FAULT)
        return int.from_bytes(backing[off:off + width], "little")

    def _mmio_write(self, addr: int, value: int, width: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=True)
        if errno is not None:
            return errno
        if off + width > len(backing):
            return WasiErrno.FAULT
        backing[off:off + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
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
        if not self._write_guest(dest_offset, bytes(backing[off:off + byte_count])):
            return WasiErrno.FAULT
        return WasiErrno.SUCCESS

    def _mmio_bulk_write(self, addr: int, src_offset: int, byte_count: int) -> WasiErrno:
        errno, backing, off = self._mmio_touch(addr, is_write=True)
        if errno is not None:
            return errno
        data = self._read_guest(src_offset, byte_count)
        if data is None or off + byte_count > len(backing):
            return WasiErrno.FAULT
        backing[off:off + byte_count] = data
        return WasiErrno.SUCCESS

    # --- VDMA (real REG_VDMA_* registers + a real memcpy) ---------------

    def _vdma_start(self, src: int, dst: int, byte_count: int) -> WasiErrno:
        struct.pack_into("<III", self.vdma_regs, REG_VDMA_SRC,
                          src & 0xFFFF_FFFF, dst & 0xFFFF_FFFF, byte_count & 0xFFFF_FFFF)
        return self._run_vdma()

    def _vdma_region(self, addr: int, count: int, is_write: bool):
        """runtime_vmmio.md §4.5: VDMA src/dst may be guest RAM (Tier 1) or
        vMMIO FC=14/15 -- resolved through the exact same permission gate
        as a direct access, owner checks included."""
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
        dst_backing[dst_off:dst_off + count] = bytes(src_backing[src_off:src_off + count])
        return WasiErrno.SUCCESS

    # --- IRQ (REG_IRQ_FLAGS, shared with SYSCTL's own register file) ----

    def _irq_read_flags(self) -> int:
        return struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]

    def _irq_clear(self, mask: int) -> WasiErrno:
        flags = struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]
        struct.pack_into("<I", self.sysctl_regs, REG_IRQ_FLAGS, flags & ~mask & 0xFFFF_FFFF)
        return WasiErrno.SUCCESS

    def raise_irq(self, mask: int) -> None:
        """Not itself a syscall (system_syscall.md §8: interrupts are a
        host-to-guest notification, not a guest-initiated call) -- lets a
        test or the HAL demo set REG_IRQ_FLAGS bits for IRQ_READ_FLAGS/
        IRQ_CLEAR to observe."""
        flags = struct.unpack_from("<I", self.sysctl_regs, REG_IRQ_FLAGS)[0]
        struct.pack_into("<I", self.sysctl_regs, REG_IRQ_FLAGS, (flags | mask) & 0xFFFF_FFFF)

    # --- IPC (real IPCRouter: URI lookup, RBAC, bounded-queue handoff) ---

    def _ipc_lookup(self, uri_offset: int, uri_len: int) -> int:
        raw = self._read_guest(uri_offset, uri_len)
        if raw is None:
            return int(WasiErrno.FAULT)
        try:
            uri = raw.decode("utf-8")
        except UnicodeDecodeError:
            return int(WasiErrno.INVAL)
        handle = self._ipc_handle_by_uri.get(uri)
        return handle if handle is not None else int(WasiErrno.NOENT)

    def _ipc_send(self, handle_id: int, msg_offset: int, msg_len: int) -> WasiErrno:
        uri = self._ipc_uri_by_handle.get(handle_id)
        if uri is None:
            return WasiErrno.BADF
        payload = self._read_guest(msg_offset, msg_len)
        if payload is None:
            return WasiErrno.FAULT
        msg = IPCMessage(resource_id=f"guest_task_{self._current_task_id}_msg", payload={"bytes": bytes(payload)})
        # ipc_router_concept.py's fixed role matrix has no multi-role guest
        # model -- every fireball_call-issuing guest in this experiment is
        # CLIENT_APP, the only role a guest task could plausibly hold.
        status, _ = self.ipc.route_message("CLIENT_APP", uri, msg)
        if status == "OK_ENQUEUED":
            return WasiErrno.SUCCESS
        if status == "ERR_QUEUE_FULL":
            return WasiErrno.AGAIN
        if status == "ERR_PERMISSION_DENIED":
            return WasiErrno.PERM
        return WasiErrno.NOENT

    def _ipc_recv(self, handle_id: int, buf_offset: int, buf_len: int) -> int:
        uri = self._ipc_uri_by_handle.get(handle_id)
        if uri is None:
            return int(WasiErrno.BADF)
        entry = self.ipc.registry.find(uri)
        msg = self.ipc.receive_message(entry["channel_id"])
        if msg is None:
            # ipc_router.md: an empty queue suspends the coroutine. This
            # synchronous fireball_call boundary cannot suspend a native
            # call mid-flight -- EAGAIN ({Errorcode_To_Strategy}'s `retry`)
            # is the real, standard "would block" signal WASI already
            # defines for exactly this situation, not a stand-in for it.
            return int(WasiErrno.AGAIN)
        data = msg.payload["bytes"]
        n = min(len(data), buf_len)
        if not self._write_guest(buf_offset, data[:n]):
            return int(WasiErrno.FAULT)
        return n

    # --- WASI (interface_wit.md §5.5-5.6) --------------------------------

    def _wasi_fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> WasiErrno:
        """system_syscall.md §7.1: the Shim already loops per-vector before
        trapping, so `iovs_len` reaching here is expected to be 1 -- but
        this loops anyway rather than assuming it, since nothing enforces
        that at this boundary."""
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
