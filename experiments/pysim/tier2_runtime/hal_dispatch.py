"""
experiments/pysim/tier3_platform/hal.py
Real (not mocked) HAL underlayer for the pysim experiment.
- StreamTransport: a genuine OS-level byte pipe (socket.socketpair), standing
  in for the physical UART/ITM line. Bytes written here really cross a
  kernel-buffered duplex socket, so a full/blocked transport is an actual
  socket condition, not an in-memory flag someone forgot to flip.
- HalBufferPool: acquire_buffer()/release_buffer() backed by a plain
  bytearray per slot. The point being tested -- "a guest can only touch a
  buffer via a handle the pool has authorized, never via a raw pointer" --
  is a property of the *lookup discipline* (every access goes through
  _resolve()'s ownership/bounds check), not of the byte storage being real
  OS shared memory, so a bytearray proves it exactly as well without the
  extra process-boundary machinery.
- Timer: wall-clock timer via time.monotonic_ns(), matching
  wasi:clocks/monotonic-clock's nanosecond contract.
This intentionally sets aside C++ naming/type conventions and is not wired
into the C++ build. It exists to pressure-test whether the *design* in
docs/components/tier1_interface/interface_wit.md and
docs/components/tier2_runtime/hal_dispatch.md (contract) / docs/components/tier3_platform/platform_driver.md (impl) actually holds together when
something has to really run.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory import MemoryManager
    from scheduler import Scheduler

from ipc_router import DataType, IPCMessage, IPCRouter, IPCStatus, Role, ScopeKind, pack_key32
from scheduler import ChannelAction
from system_containers import FlatMapView, StaticVector
from vmmio import FC_DYNAMIC, VMMIOController, VMMIO_PAGE_SHIFT, VmmioStatus

# hal_dispatch.md §4.2's kv_pair command arguments: each is a packed
# (ScopeKind.FUNCTIONAL, DataType.UINT32, key_id) key per ipc_router.md §3.3,
# never a string name -- a string key has no C++ counterpart once RTTI is
# disabled, and the doc's argument names ("pin_no", "val", ...) are only the
# human-readable label for a given key_id, not the wire key itself.
ARG_CMD_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0)
ARG_QUERY_CMD_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
ARG_BUFFER_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=2)
ARG_OFFSET = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=3)
ARG_LENGTH = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=4)
ARG_MAX_LEN = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=5)
ARG_NANOS = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=6)
ARG_PIN_NO = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=7)
ARG_VAL = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=8)
ARG_MODE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=9)
ARG_EDGE_TYPE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=10)
ARG_TX_BUFFER_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=11)
ARG_RX_BUFFER_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=12)
ARG_CLOCK_HZ = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=13)
ARG_SLAVE_ADDR = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=14)
ARG_FD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=16)


class WasiIpcCmd(IntEnum):
    """WASI 0.3p IPC Driver Command Protocol IDs (hal_dispatch.md §5.2)."""

    # Common Capability Query
    QUERY_CAPS = 0x00
    # Stream (0x01..0x04)
    STREAM_WRITE_BUFFER = 0x01
    STREAM_READ_BUFFER = 0x02
    STREAM_FLUSH = 0x03
    STREAM_CLOSE = 0x04
    # Clock / Timer (0x10..0x12)
    CLOCK_GET_NOW = 0x10
    CLOCK_SUBSCRIBE = 0x11
    CLOCK_GET_RES = 0x12
    # GPIO / Trigger (0x20..0x23)
    GPIO_SET_PIN = 0x20
    GPIO_GET_PIN = 0x21
    GPIO_CONFIG_PIN = 0x22
    GPIO_SUBSCRIBE_EDGE = 0x23
    # Bus (0x30..0x31)
    BUS_TRANSFER_BUFFER = 0x30
    BUS_CONFIG = 0x31
    # Poll (0x40..0x41)
    POLL_CHECK = 0x40
    POLL_WAIT = 0x41


class HalError(Exception):
    """Base class for HAL-level failures that must map to a recovery-strategy-category."""


class HalBufferTrap(HalError):
    """
    A guest touched a HAL buffer-pool handle it does not own, or a slice
        escaped the handle's acquired bounds. Mirrors runtime_vmmio.md 4.6's
        vMMIO PTE ownership trap -- a real MMU would fault here.
    """


# ---------------------------------------------------------------------------
# Host-side stream endpoint
# ---------------------------------------------------------------------------


class StreamTransport:
    """
    One host-side duplex stream, modeled as a real OS socket pair.
        `device_sock` is the endpoint a driver writes to;
        `host_sock` is what a host-side terminal/log collector reads from.
        Nothing here is a Python list standing in for hardware: bytes written
        via write() genuinely traverse a kernel socket buffer.
    """

    def __init__(self):
        self.device_sock, self.host_sock = socket.socketpair()
        self.device_sock.settimeout(0.2)
        self.host_sock.settimeout(0.2)
        self._lock = threading.Lock()
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        """Writes physical output to the host-side stream endpoint."""
        with self._lock:
            n = self.device_sock.send(data)
            self.bytes_written += n
            return n

    def feed_input(self, data: bytes) -> int:
        """Feeds host input into the device-side stream endpoint."""
        with self._lock:
            return self.host_sock.send(data)

    def read_input(self, max_len: int = 4096) -> bytes:
        """Reads bytes that the host supplied to the device side."""
        assert max_len > 0
        chunks: list[bytes] = []
        total = 0
        try:
            while total < max_len:
                chunk = self.device_sock.recv(max_len - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if len(chunk) < max_len:
                    break
        except (TimeoutError, BlockingIOError):
            pass
        return b"".join(chunks)

    def drain_output(self) -> bytes:
        """Reads everything currently sitting on the host output endpoint."""
        chunks: list[bytes] = []
        try:
            while True:
                chunk = self.host_sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if len(chunk) < 4096:
                    break
        except (TimeoutError, BlockingIOError):
            pass
        return b"".join(chunks)

    def close(self) -> None:
        self.device_sock.close()
        self.host_sock.close()


# ---------------------------------------------------------------------------
# HAL buffer pool (static buffers MMIO'd into the vMMIO DYNAMIC region)
# ---------------------------------------------------------------------------

FB_CONF_HAL_BUFFER_SIZE = 256  # docs/components/tier1_core/system_config.md 3.3.3
FB_CONF_HAL_MAX_BUFFERS = 4  # docs/components/tier1_core/system_config.md 3.3.3


@dataclass
class HalBufferHandle:
    """
    What acquire_buffer() actually returns: an opaque integer ID, not a
    pointer. Handing this value to code running as a different owner is
        meaningless -- there is no address inside it that could be dereferenced
        as guest linear memory, only a lookup key the pool checks against an
        owner table before it will hand back a byte.
    """

    buffer_id: int
    owner_task: int
    capacity: int
    virtual_address: int
    _storage: bytearray = field(repr=False, compare=False)


class HalBufferPool:
    """
    `acquire_buffer()` backed by FB_CONF_HAL_MAX_BUFFERS fixed-size slots
        of at most FB_CONF_HAL_BUFFER_SIZE bytes each: a static pool, not a
        dynamic allocator (hal_dispatch.md 5.1's "静的固定長バッファプール"),
        MMIO'd into the vMMIO DYNAMIC region. The DYNAMIC mapping is bound to
        exactly one guest task for the pool lifetime. acquire_buffer() is the
        ownership-taking call a client must make before it may touch a slot at all --
        every other method re-checks that ownership before honoring a
        request (GOTCHA-HAL-01).
    """

    def __init__(self, scheduler: Scheduler, vmmio: VMMIOController):
        self._scheduler = scheduler
        self._vmmio = vmmio
        self._slots: StaticVector[HalBufferHandle | None] = StaticVector.of(
            (None,) * FB_CONF_HAL_MAX_BUFFERS, capacity=FB_CONF_HAL_MAX_BUFFERS
        )
        self._mapped_guest_task: int | None = None

    def bind_guest(self) -> None:
        """Binds the DYNAMIC mapping to one guest for its whole lifetime."""
        task_id = self.current_task_id
        if self._mapped_guest_task is None:
            self._mapped_guest_task = task_id
            return
        assert self._mapped_guest_task == task_id, "HAL DYNAMIC mapping supports one guest only"

    @property
    def current_task_id(self) -> int:
        return self._scheduler.current_task_id

    def acquire_buffer(self, size: int) -> HalBufferHandle:
        """Claims ownership of one free static slot for the running task."""
        task_id = self.current_task_id
        assert self._mapped_guest_task == task_id, "HAL DYNAMIC mapping is not bound to this guest"
        if size <= 0 or size > FB_CONF_HAL_BUFFER_SIZE:
            raise ValueError(
                f"acquire_buffer(size={size}) exceeds FB_CONF_HAL_BUFFER_SIZE={FB_CONF_HAL_BUFFER_SIZE}"
            )

        slot_idx = -1
        for i, s in enumerate(self._slots):
            if s is None:
                slot_idx = i
                break
        if slot_idx < 0:
            raise HalError("HAL buffer pool exhausted (FB_CONF_HAL_MAX_BUFFERS)")
        virtual_address = (FC_DYNAMIC << 28) | (slot_idx << VMMIO_PAGE_SHIFT)
        self._vmmio.map_dynamic_page(virtual_address >> VMMIO_PAGE_SHIFT, slot_idx)
        handle = HalBufferHandle(
            buffer_id=slot_idx,
            owner_task=task_id,
            capacity=size,
            virtual_address=virtual_address,
            _storage=bytearray(size),
        )
        self._slots[slot_idx] = handle
        return handle

    def release_buffer(self, handle: HalBufferHandle) -> None:
        task_id = self.current_task_id
        for i, s in enumerate(self._slots):
            if s is not None and s.buffer_id == handle.buffer_id:
                if s.owner_task != task_id:
                    raise HalBufferTrap(
                        f"task {task_id} cannot release buffer {handle.buffer_id}: not the owner"
                    )
                self._slots[i] = None
                self._vmmio.unmap_dynamic_page(s.virtual_address >> VMMIO_PAGE_SHIFT)
                return
        raise HalBufferTrap(f"task {task_id} cannot release buffer {handle.buffer_id}: not found")

    def _resolve(self, handle: HalBufferHandle) -> HalBufferHandle:
        task_id = self.current_task_id
        for s in self._slots:
            if s is not None and s.buffer_id == handle.buffer_id:
                if s.owner_task != task_id:
                    raise HalBufferTrap(
                        f"task {task_id} does not own buffer {handle.buffer_id} (owner={s.owner_task}); "
                        "no linear-memory pointer would ever bypass this check"
                    )
                return s
        raise HalBufferTrap(f"buffer {handle.buffer_id} does not exist (stale, or never acquired)")

    def view_for_driver(self, buffer_id: int, offset: int, length: int) -> memoryview:
        """Resolves a caller-owned buffer for the HAL driver currently serving it.

        The driver is the trusted HAL subsystem endpoint for the pool, so its access is
        independent of the guest task that acquired the handle. The public
        guest-facing ``view`` method keeps the ownership check.
        """
        assert buffer_id >= 0
        record: HalBufferHandle | None = None
        for slot in self._slots:
            if slot is not None and slot.buffer_id == buffer_id:
                record = slot
                break
        assert record is not None, f"buffer {buffer_id} does not exist"
        assert 0 <= offset <= record.capacity
        assert 0 <= length <= record.capacity - offset
        status, _physical = self._vmmio.access(record.virtual_address + offset, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL, "HAL buffer is not mapped in vMMIO DYNAMIC"
        return memoryview(record._storage)[offset : offset + length]

    def close_all(self) -> None:
        for i in range(len(self._slots)):
            handle = self._slots[i]
            if handle is not None:
                self._vmmio.unmap_dynamic_page(handle.virtual_address >> VMMIO_PAGE_SHIFT)
            self._slots[i] = None

    def can_view(self, handle: HalBufferHandle, offset: int, length: int) -> bool:
        """
        Non-throwing precondition check for view(): same ownership/bounds
        rules, but a bool return instead of raising HalBufferTrap, for callers
        that must not depend on catching an exception (exceptions disabled
        in the target C++ build).
        """
        task_id = self.current_task_id
        for s in self._slots:
            if s is not None and s.buffer_id == handle.buffer_id:
                if s.owner_task != task_id:
                    return False
                return 0 <= offset and 0 <= length and length <= s.capacity - offset
        return False

    def view(self, handle: HalBufferHandle, offset: int, length: int) -> memoryview:
        """
        Resolves a bounds-checked (offset, length) window inside `handle`.
                This is what interface_wit.md 5.3's `hal-buffer-slice{handle, offset, len}`
                actually resolves to at the HAL layer.
        """

        record = self._resolve(handle)
        if offset < 0 or length < 0 or offset > record.capacity or length > record.capacity - offset:
            raise HalBufferTrap(
                f"hal-buffer-slice(offset={offset}, len={length}) escapes buffer {handle.buffer_id}'s "
                f"acquired capacity ({record.capacity} bytes)"
            )
        status, _physical = self._vmmio.access(record.virtual_address + offset, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL, "HAL buffer is not mapped in vMMIO DYNAMIC"
        return memoryview(record._storage)[offset : offset + length]


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------


class Timer:
    """wasi:clocks/monotonic-clock, backed by the real system clock."""

    def get_now_ns(self) -> int:
        return time.monotonic_ns()

    def subscribe(self, nanos: int, callback) -> threading.Timer:
        t = threading.Timer(nanos / 1e9, callback)
        t.daemon = True
        t.start()
        return t


# ---------------------------------------------------------------------------
# HAL Drivers with WASI 0.3p IPC Command Dispatch & Capability Query
# ---------------------------------------------------------------------------


HalResult = int | bytes | None
HalCommandCallback = Callable[[FlatMapView], HalResult]


@dataclass(frozen=True, slots=True)
class HalCommandBinding:
    """One driver-owned command ID and its access callback."""

    command_id: int
    callback: HalCommandCallback


class HalDriver:
    """
    Base class for device-owned HAL command configuration.

    A driver registers the command IDs it accepts and the callback invoked on
    access. The generic HAL task only transports the command and performs the
    callback lookup; it does not contain device-specific dispatch logic.
    """

    def __init__(self, uri: str):
        self.uri = uri
        self._buffer_pool: HalBufferPool | None = None
        self._command_bindings: StaticVector[HalCommandBinding] = StaticVector(capacity=16)
        self.register_command(WasiIpcCmd.QUERY_CAPS, self._query_caps)

    def bind_buffer_pool(self, pool: HalBufferPool) -> None:
        """Binds the system HAL buffer pool before this driver task starts."""
        assert self._buffer_pool is None or self._buffer_pool is pool
        self._buffer_pool = pool

    def register_command(self, command_id: int, callback: HalCommandCallback) -> None:
        """Registers one driver command callback before the task is started."""
        assert self._find_command(command_id) is None, f"duplicate HAL command {command_id:#x}"
        assert self._command_bindings.push_back(HalCommandBinding(command_id, callback))
        self._command_bindings.sort(key=lambda binding: binding.command_id)

    def _find_command(self, command_id: int) -> HalCommandCallback | None:
        for binding in self._command_bindings:
            if binding.command_id == command_id:
                return binding.callback
        return None

    def _query_caps(self, params: FlatMapView) -> int:
        query_cmd = params.find(ARG_QUERY_CMD_ID)
        return 1 if query_cmd is not None and self._find_command(query_cmd) is not None else 0

    def is_supported(self, cmd_id: int) -> int:
        """Checks if this driver supports the given command ID (1=True, 0=False)."""
        return 1 if self._find_command(cmd_id) is not None else 0

    def dispatch(self, cmd_id: int, params: FlatMapView) -> HalResult:
        """Dispatches an IPC command through the driver's registered callback."""
        callback = self._find_command(cmd_id)
        return None if callback is None else callback(params)

    def start(self, ipc: IPCRouter, scheduler: Scheduler, role: Role) -> tuple[int, HalTask]:
        """Starts this driver's dedicated HAL task and returns its task handle."""
        task = HalTask(ipc, self)
        task_id = scheduler.spawn(f"hal_task[{self.uri}]", task.run(), role=role)
        return task_id, task


ARG_RESULT = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0xFF)


class HalTask:
    """
    COOS Task for one HAL device/service instance ({META_3TierSeparation},
    {hal_dispatch.md}). HAL operates as one independent cooperative task
    *per device instance*, not one shared task for the whole HAL layer: the
    IPC router hands each task's role its own dedicated CSP channel (1
    channel = 1 waiter, ipc_router.md), so a single shared task could not
    have told two same-type instances (two UART-shaped endpoints, two
    GPIO-shaped endpoints) apart -- only the *role* selects which channel a
    message lands on, the URI itself never rides the hot transfer path. One
    HalTask thus owns exactly one HalDriver and is spawned under exactly the
    Role that URI resolves to (see system.py `start_hal_driver`).
    """

    def __init__(self, ipc: IPCRouter, driver: HalDriver):
        self.ipc = ipc
        self.driver = driver
        self.running = True
        self.last_handled_cmd: int | None = None
        self.last_result: HalResult = None
        self.processed_count: int = 0

    def run(self):
        """
        Coroutine body of one HAL device's server task.
        Runs continuously in COOS, listening on this task's own dedicated
        role channel via CSP rendezvous.
        """
        while self.running:
            status, msg = yield from self.ipc.recv()
            if status != IPCStatus.COMPLETED or msg is None:
                yield (ChannelAction.BLOCK, None)
                continue

            self.processed_count += 1
            cmd_id = msg.get(ARG_CMD_ID)
            if cmd_id is None:
                cmd_id = msg.get(ARG_QUERY_CMD_ID, 0x00)

            self.last_result = self.driver.dispatch(cmd_id, msg.payload)
            self.last_handled_cmd = cmd_id
            yield (ChannelAction.YIELD, None)


def make_hal_ipc_message(
    cmd_id: int,
    params: Sequence[tuple[int, int]] = (),
    memory_manager: MemoryManager | None = None,
) -> IPCMessage:
    """Builds a standardized IPCMessage for communicating with HalTask."""
    entries = list(params)
    entries.append((ARG_CMD_ID, cmd_id))
    sorted_entries = sorted(entries, key=lambda kv: kv[0])
    return IPCMessage.from_entries(sorted_entries, memory_manager=memory_manager)
