"""
experiments/pysim/platforms/hal.py
Real (not mocked) HAL underlayer for the pysim experiment.
- UartTransport: a genuine OS-level byte pipe (socket.socketpair), standing
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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory import MemoryManager

from ipc_router import DataType, IPCMessage, IPCRouter, IpcStatus, ScopeKind, pack_key32
from scheduler import ChannelAction
from system_containers import FlatMapView, FlatSetView, StaticVector

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
ARG_TASK_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=15)
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
# UART / console transport
# ---------------------------------------------------------------------------


class UartTransport:
    """
    One physical serial line, modeled as a real duplex OS socket pair.
        `device_sock` is the "wire" a real UART peripheral would drive;
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
        """Physical transmit: blocks on the real socket buffer if full."""
        with self._lock:
            n = self.device_sock.send(data)
            self.bytes_written += n
            return n

    def drain(self) -> bytes:
        """Host-side read of everything currently sitting on the wire."""
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
    What acquire_buffer() actually returns: an opaque *name*, not a
        pointer. Handing this value to code running as a different owner is
        meaningless -- there is no address inside it that could be dereferenced
        as guest linear memory, only a lookup key the pool checks against an
        owner table before it will hand back a byte.
    """

    name: str
    owner_task: int
    capacity: int
    _storage: bytearray = field(repr=False, compare=False)


class HalBufferPool:
    """
    `acquire_buffer()` backed by FB_CONF_HAL_MAX_BUFFERS fixed-size slots
        of at most FB_CONF_HAL_BUFFER_SIZE bytes each: a static pool, not a
        dynamic allocator (hal_dispatch.md 5.1's "静的固定長バッファプール"),
        MMIO'd into the vMMIO DYNAMIC region. Multiple client tasks may
        contend for the same device, so acquire_buffer() is the ownership-
        taking call a client must make before it may touch a slot at all --
        every other method re-checks that ownership before honoring a
        request (GOTCHA-HAL-01).
    """

    def __init__(self):
        self._slots: list[HalBufferHandle | None] = [None] * FB_CONF_HAL_MAX_BUFFERS

    def acquire_buffer(self, task_id: int, size: int) -> HalBufferHandle:
        """Claims ownership of one free static slot for `task_id` (GOTCHA-HAL-01)."""
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
        name = f"fb_hal_buf_{uuid.uuid4().hex[:12]}"
        handle = HalBufferHandle(
            name=name, owner_task=task_id, capacity=size, _storage=bytearray(size)
        )
        self._slots[slot_idx] = handle
        return handle

    def release_buffer(self, task_id: int, handle: HalBufferHandle) -> None:
        for i, s in enumerate(self._slots):
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    raise HalBufferTrap(
                        f"task {task_id} cannot release {handle.name}: not the owner"
                    )
                self._slots[i] = None
                return
        raise HalBufferTrap(f"task {task_id} cannot release {handle.name}: not found")

    def _resolve(self, task_id: int, handle: HalBufferHandle) -> HalBufferHandle:
        for s in self._slots:
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    raise HalBufferTrap(
                        f"task {task_id} does not own {handle.name} (owner={s.owner_task}); "
                        "no linear-memory pointer would ever bypass this check"
                    )
                return s
        raise HalBufferTrap(f"handle {handle.name} does not exist (stale, or never acquired)")

    def close_all(self) -> None:
        for i in range(len(self._slots)):
            self._slots[i] = None

    def can_view(self, task_id: int, handle: HalBufferHandle, offset: int, length: int) -> bool:
        """
        Non-throwing precondition check for view(): same ownership/bounds
        rules, but a bool return instead of raising HalBufferTrap, for callers
        that must not depend on catching an exception (exceptions disabled
        in the target C++ build).
        """
        for s in self._slots:
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    return False
                return 0 <= offset and 0 <= length and offset + length <= s.capacity
        return False

    def view(self, task_id: int, handle: HalBufferHandle, offset: int, length: int) -> memoryview:
        """
        Resolves a bounds-checked (offset, length) window inside `handle`.
                This is what interface_wit.md 5.3's `hal-buffer-slice{handle, offset, len}`
                actually resolves to at the HAL layer.
        """

        record = self._resolve(task_id, handle)
        if offset < 0 or length < 0 or offset + length > record.capacity:
            raise HalBufferTrap(
                f"hal-buffer-slice(offset={offset}, len={length}) escapes {handle.name}'s "
                f"acquired capacity ({record.capacity} bytes)"
            )
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


class HalDriver:
    """
    Base class for HAL device drivers supporting WASI 0.3p IPC Commands.
    Matches hal_dispatch.md §5.1's `control(id, cmd, params: ipc-message)`:
    exactly one statically-typed params argument, always a FlatMapView over
    packed kv_pair keys (ipc_router.md §3.3) -- no kwargs escape hatch, no
    runtime inspection of what was passed (C++ has neither RTTI nor
    reflection to do that with).
    """

    def __init__(self, uri: str, supported_commands: Sequence[int] = ()):
        self.uri = uri
        # QUERY_CAPS is always supported.
        self._supported_commands_storage = sorted((WasiIpcCmd.QUERY_CAPS, *supported_commands))
        self.supported_commands = FlatSetView(self._supported_commands_storage)

    def is_supported(self, cmd_id: int) -> int:
        """Checks if this driver supports the given command ID (1=True, 0=False)."""
        return 1 if cmd_id in self.supported_commands else 0

    def dispatch(self, cmd_id: int, params: FlatMapView) -> object:
        """Dispatches an IPC command to the driver handler."""
        if cmd_id == WasiIpcCmd.QUERY_CAPS:
            query_cmd = params.find(ARG_QUERY_CMD_ID)
            return self.is_supported(0 if query_cmd is None else query_cmd)

        return self._handle_command(cmd_id, params)

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        raise NotImplementedError(f"Command {cmd_id} not implemented for {self.uri}")


class DummyUartDriver(HalDriver):
    """Dummy UART Driver supporting Stream Read/Write via SHM."""

    def __init__(
        self, uri: str = "fireball://device/uart/0", transport: UartTransport | None = None
    ):
        super().__init__(
            uri,
            supported_commands=(
                WasiIpcCmd.STREAM_WRITE_BUFFER,
                WasiIpcCmd.STREAM_READ_BUFFER,
                WasiIpcCmd.STREAM_FLUSH,
                WasiIpcCmd.STREAM_CLOSE,
            ),
        )
        self.transport = transport or UartTransport()

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == WasiIpcCmd.STREAM_WRITE_BUFFER:
            # hal_dispatch.md §4.2: buffer_handle/offset/len resolve a zero-copy
            # HAL buffer slice; this dummy has no pool reference to resolve one
            # against, so it stands in with the slice length only.
            length = params.find(ARG_LENGTH)
            return 0 if length is None else length
        elif cmd_id == WasiIpcCmd.STREAM_READ_BUFFER:
            return self.transport.drain()
        elif cmd_id in (WasiIpcCmd.STREAM_FLUSH, WasiIpcCmd.STREAM_CLOSE):
            return 0
        return None


class DummyGpioDriver(HalDriver):
    """Dummy GPIO Driver supporting Pin R/W, Configuration, and Edge IRQ."""

    # hal_dispatch.md doesn't fix a pin count; a real MCU GPIO port is a
    # small, bounded set, so a fixed-size array (not a dict) models it.
    _MAX_PINS = 64

    def __init__(self, uri: str = "fireball://device/gpio/0"):
        super().__init__(
            uri,
            supported_commands=(
                WasiIpcCmd.GPIO_SET_PIN,
                WasiIpcCmd.GPIO_GET_PIN,
                WasiIpcCmd.GPIO_CONFIG_PIN,
                WasiIpcCmd.GPIO_SUBSCRIBE_EDGE,
            ),
        )
        self.pins: StaticVector[bool] = StaticVector.of(
            (False,) * self._MAX_PINS, capacity=self._MAX_PINS
        )
        self.modes: StaticVector[int] = StaticVector.of(
            (0,) * self._MAX_PINS, capacity=self._MAX_PINS
        )

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        pin = params.find(ARG_PIN_NO) or 0
        if cmd_id == WasiIpcCmd.GPIO_SET_PIN:
            self.pins[pin] = bool(params.find(ARG_VAL))
            return 0
        elif cmd_id == WasiIpcCmd.GPIO_GET_PIN:
            return 1 if self.pins[pin] else 0
        elif cmd_id == WasiIpcCmd.GPIO_CONFIG_PIN:
            self.modes[pin] = params.find(ARG_MODE) or 0
            return 0
        elif cmd_id == WasiIpcCmd.GPIO_SUBSCRIBE_EDGE:
            return 1  # pollable handle
        return None


class DummyTimerDriver(HalDriver):
    """Dummy Timer Driver supporting Monotonic Clock and Subscriptions."""

    def __init__(self, uri: str = "fireball://device/timer/0"):
        super().__init__(
            uri,
            supported_commands=(
                WasiIpcCmd.CLOCK_GET_NOW,
                WasiIpcCmd.CLOCK_SUBSCRIBE,
                WasiIpcCmd.CLOCK_GET_RES,
            ),
        )
        self.timer = Timer()

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == WasiIpcCmd.CLOCK_GET_NOW:
            return self.timer.get_now_ns()
        elif cmd_id == WasiIpcCmd.CLOCK_SUBSCRIBE:
            return 1  # pollable handle
        elif cmd_id == WasiIpcCmd.CLOCK_GET_RES:
            return 1_000_000  # 1ms
        return None


class DummyBusDriver(HalDriver):
    """Dummy I2C/SPI Bus Driver supporting Zero-Copy SHM Transfer."""

    def __init__(self, uri: str = "fireball://device/i2c/0"):
        super().__init__(
            uri,
            supported_commands=(
                WasiIpcCmd.BUS_TRANSFER_BUFFER,
                WasiIpcCmd.BUS_CONFIG,
            ),
        )

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == WasiIpcCmd.BUS_TRANSFER_BUFFER:
            return params.find(ARG_LENGTH) or 0
        elif cmd_id == WasiIpcCmd.BUS_CONFIG:
            return 0
        return None


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
    Role that URI resolves to (see system.py `spawn_hal_tasks`).
    """

    def __init__(self, ipc: IPCRouter, driver: HalDriver):
        self.ipc = ipc
        self.driver = driver
        self.running = True
        self.last_handled_cmd: int | None = None
        self.last_result: object = None
        self.processed_count: int = 0

    def run(self):
        """
        Coroutine body of one HAL device's server task.
        Runs continuously in COOS, listening on this task's own dedicated
        role channel via CSP rendezvous.
        """
        while self.running:
            status, msg = yield from self.ipc.recv()
            if status != IpcStatus.COMPLETED or msg is None:
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
    task_id: int = 1,
) -> IPCMessage:
    """Builds a standardized IPCMessage for communicating with HalTask."""
    entries = list(params)
    entries.append((ARG_CMD_ID, cmd_id))
    sorted_entries = sorted(entries, key=lambda kv: kv[0])
    return IPCMessage.from_entries(sorted_entries, memory_manager=memory_manager, task_id=task_id)
