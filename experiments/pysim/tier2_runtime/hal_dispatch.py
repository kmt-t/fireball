"""
experiments/pysim/tier3_platform/hal.py
Real (not mocked) HAL underlayer for the pysim experiment.
This module contains only the Tier 2 HAL command and buffer-access contracts.
The stream endpoint, timer, and fixed-buffer storage are Tier 3 platform
implementations.
This intentionally sets aside C++ naming/type conventions and is not wired
into the C++ build. It exists to pressure-test whether the *design* in
docs/components/tier1_interface/interface_wit.md and
docs/components/tier2_runtime/hal_dispatch.md (contract) / docs/components/tier3_platform/platform_driver.md (impl) actually holds together when
something has to really run.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from memory import MemoryManager
    from scheduler import Scheduler

from ipc_router import DataType, IPCMessage, IPCRouter, IPCStatus, Role, ScopeKind, pack_key32
from scheduler import ChannelAction
from system_containers import ReadOnlyFlatMapView, StaticVector
from vmmio import FC_DYNAMIC, VMMIO_PAGE_SHIFT, VMMIOController, VmmioStatus

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
    A guest touched an unmapped or stale HAL buffer-pool handle, or a slice
        escaped the handle's acquired bounds. DYNAMIC buffers do not carry
        shared-memory task ownership; the mapped-guest boundary is checked
        separately from the HAL driver's privileged view.
    """


# ---------------------------------------------------------------------------
# Host-side stream endpoint
# ---------------------------------------------------------------------------


class StreamSink(Protocol):
    """Tier 2が要求するストリーム出力の最小契約。実体はTier 3が提供する。"""

    def write(self, data: memoryview) -> int: ...


# ---------------------------------------------------------------------------
# HAL buffer pool (static buffers MMIO'd into the vMMIO DYNAMIC region)
# ---------------------------------------------------------------------------

FB_CONF_HAL_BUFFER_SIZE = 256  # docs/components/tier1_core/system_config.md 3.3.3
FB_CONF_HAL_MAX_BUFFERS = 4  # docs/components/tier1_core/system_config.md 3.3.3


@dataclass
class HalBufferHandle:
    """
    What the fixed buffer accessor returns: an opaque integer ID, not a
    pointer. The handle identifies a live slot in the HAL-owned fixed pool;
    it is not a shared-memory ownership token.
    """

    buffer_id: int
    capacity: int
    virtual_address: int
    _storage: bytearray = field(repr=False, compare=False)


class HalBufferPool:
    """
    FB_CONF_HAL_MAX_BUFFERS fixed-size slots of
        FB_CONF_HAL_BUFFER_SIZE bytes each: a static pool, not a dynamic
        allocator. The pool is MMIO'd into the vMMIO DYNAMIC region when the
        single runtime mapping is bound. There is no acquire/release lifecycle;
        HAL owns all slots for the lifetime of the system. The HAL driver may
        access every slot, while the one mapped guest may access the same slots.
    """

    def __init__(self, scheduler: Scheduler, vmmio: VMMIOController):
        self._scheduler = scheduler
        self._vmmio = vmmio
        self._slots: StaticVector[HalBufferHandle] = StaticVector(capacity=FB_CONF_HAL_MAX_BUFFERS)
        for slot_idx in range(FB_CONF_HAL_MAX_BUFFERS):
            self._slots.append(
                HalBufferHandle(
                    buffer_id=slot_idx,
                    capacity=FB_CONF_HAL_BUFFER_SIZE,
                    virtual_address=(FC_DYNAMIC << 28) | (slot_idx << VMMIO_PAGE_SHIFT),
                    _storage=bytearray(FB_CONF_HAL_BUFFER_SIZE),
                )
            )
        self._mapped_runtime_task: int | None = None

    def bind_runtime(self) -> None:
        """Binds and maps the fixed DYNAMIC buffer array to one runtime task."""
        task_id = self.current_task_id
        if self._mapped_runtime_task is None:
            self._mapped_runtime_task = task_id
            for slot_idx, handle in enumerate(self._slots):
                self._vmmio.map_dynamic_page(handle.virtual_address >> VMMIO_PAGE_SHIFT, slot_idx)
            return
        assert self._mapped_runtime_task == task_id, "HAL DYNAMIC mapping supports one runtime only"

    @property
    def current_task_id(self) -> int:
        return self._scheduler.current_task_id

    def buffer(self, buffer_id: int) -> HalBufferHandle:
        """Returns one fixed HAL buffer visible to the mapped guest."""
        task_id = self.current_task_id
        assert self._mapped_runtime_task == task_id, (
            "HAL DYNAMIC mapping is not bound to this runtime"
        )
        assert 0 <= buffer_id < len(self._slots)
        return self._slots[buffer_id]

    def unbind_runtime(self) -> None:
        """Unmaps all fixed DYNAMIC buffers from the currently bound Runtime."""
        task_id = self.current_task_id
        assert self._mapped_runtime_task == task_id, (
            "HAL DYNAMIC mapping is not bound to this runtime"
        )
        for handle in self._slots:
            self._vmmio.unmap_dynamic_page(handle.virtual_address >> VMMIO_PAGE_SHIFT)
        self._mapped_runtime_task = None

    def _resolve(self, handle: HalBufferHandle) -> HalBufferHandle:
        task_id = self.current_task_id
        assert self._mapped_runtime_task == task_id, (
            "HAL DYNAMIC mapping is not bound to this runtime"
        )
        assert 0 <= handle.buffer_id < len(self._slots), f"buffer {handle.buffer_id} does not exist"
        record = self._slots[handle.buffer_id]
        assert record.buffer_id == handle.buffer_id, "stale HAL buffer handle"
        return record

    def view_for_driver(self, buffer_id: int, offset: int, length: int) -> memoryview:
        """Resolves a live HAL-owned buffer for the driver currently serving it.

        The driver is the trusted HAL subsystem endpoint for the pool, so its access is
        independent of the guest task that mapped the DYNAMIC region. The public
        guest-facing ``view`` method checks the mapped guest instead.
        """
        assert buffer_id >= 0
        assert buffer_id < len(self._slots), f"buffer {buffer_id} does not exist"
        record = self._slots[buffer_id]
        assert record.buffer_id == buffer_id, f"buffer {buffer_id} does not exist"
        assert 0 <= offset <= record.capacity
        assert 0 <= length <= record.capacity - offset
        status, _physical = self._vmmio.access(record.virtual_address + offset, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL, "HAL buffer is not mapped in vMMIO DYNAMIC"
        return memoryview(record._storage)[offset : offset + length]

    def close_all(self) -> None:
        if self._mapped_runtime_task is None:
            return
        for handle in self._slots:
            self._vmmio.unmap_dynamic_page(handle.virtual_address >> VMMIO_PAGE_SHIFT)
        self._mapped_runtime_task = None

    def can_view(self, handle: HalBufferHandle, offset: int, length: int) -> bool:
        """
        Non-throwing precondition check for view(): same mapping/bounds
        rules, but a bool return instead of raising HalBufferTrap, for callers
        that must not depend on catching an exception (exceptions disabled
        in the target C++ build).
        """
        task_id = self.current_task_id
        if self._mapped_runtime_task != task_id:
            return False
        if handle.buffer_id < 0 or handle.buffer_id >= len(self._slots):
            return False
        slot = self._slots[handle.buffer_id]
        return (
            slot.buffer_id == handle.buffer_id
            and 0 <= offset
            and 0 <= length
            and length <= slot.capacity - offset
        )

    def view(self, handle: HalBufferHandle, offset: int, length: int) -> memoryview:
        """
        Resolves a bounds-checked (offset, length) window inside `handle`.
                This is what interface_wit.md 5.3's `hal-buffer-slice{handle, offset, len}`
                actually resolves to at the HAL layer.
        """

        record = self._resolve(handle)
        if (
            offset < 0
            or length < 0
            or offset > record.capacity
            or length > record.capacity - offset
        ):
            assert False, (
                f"hal-buffer-slice(offset={offset}, len={length}) escapes fixed buffer "
                f"{handle.buffer_id}'s capacity ({record.capacity} bytes)"
            )
        status, _physical = self._vmmio.access(record.virtual_address + offset, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL, "HAL buffer is not mapped in vMMIO DYNAMIC"
        return memoryview(record._storage)[offset : offset + length]


# ---------------------------------------------------------------------------
# HAL Drivers with WASI 0.3p IPC Command Dispatch & Capability Query
# ---------------------------------------------------------------------------


HalResult = int
HalCommandCallback = Callable[[ReadOnlyFlatMapView], HalResult]


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
        self._command_bindings.append(HalCommandBinding(command_id, callback))
        self._command_bindings.sort(key=lambda binding: binding.command_id)

    def _find_command(self, command_id: int) -> HalCommandCallback | None:
        index = bisect.bisect_left(
            self._command_bindings,
            command_id,
            key=lambda binding: binding.command_id,
        )
        if index < len(self._command_bindings):
            binding = self._command_bindings[index]
            if binding.command_id == command_id:
                return binding.callback
        return None

    def _query_caps(self, params: ReadOnlyFlatMapView) -> int:
        query_cmd = params.find(ARG_QUERY_CMD_ID)
        return 1 if query_cmd is not None and self._find_command(query_cmd) is not None else 0

    def is_supported(self, cmd_id: int) -> int:
        """Checks if this driver supports the given command ID (1=True, 0=False)."""
        return 1 if self._find_command(cmd_id) is not None else 0

    def dispatch(self, cmd_id: int, params: ReadOnlyFlatMapView) -> HalResult:
        """Dispatches an IPC command through the driver's registered callback."""
        callback = self._find_command(cmd_id)
        assert callback is not None, f"unregistered HAL command {cmd_id:#x}"
        return callback(params)

    def start(self, ipc: IPCRouter, scheduler: Scheduler, role: Role) -> tuple[int, HalTask]:
        """Starts this driver's dedicated HAL task and returns its task handle."""
        task = HalTask(ipc, self)
        task_id = scheduler.spawn(f"hal_task[{self.uri}]", task.run(), role=role)
        return task_id, task


ARG_RESULT = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0xFF)


class HalTask:
    """
    COOS Task for one HAL device/HAL-subsystem instance ({META_3TierSeparation},
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
    *,
    memory_manager: MemoryManager,
) -> IPCMessage:
    """Builds a standardized IPCMessage for communicating with HalTask."""
    entries: StaticVector[tuple[int, int]] = StaticVector(capacity=len(params) + 1)
    for entry in params:
        entries.append(entry)
    entries.append((ARG_CMD_ID, cmd_id))
    entries.sort(key=lambda kv: kv[0])
    return IPCMessage.from_entries(entries, memory_manager=memory_manager)
