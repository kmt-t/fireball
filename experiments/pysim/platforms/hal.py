"""
experiments/pysim/platforms/hal.py
Real (not mocked) HAL underlayer for the pysim experiment.
- UartTransport: a genuine OS-level byte pipe (socket.socketpair), standing
  in for the physical UART/ITM line. Bytes written here really cross a
  kernel-buffered duplex socket, so a full/blocked transport is an actual
  socket condition, not an in-memory flag someone forgot to flip.
- ShmBufferPool: acquire_buffer()/release_buffer() backed by a plain
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
docs/components/tier3_platform/platform_hal.md actually holds together when
something has to really run.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory import MemoryManager

from ipc_router import DataType, IPCMessage, IPCRouter, IpcStatus, ScopeKind, pack_key32
from scheduler import ChannelAction
from system_containers import FlatMapView, FlatSetView

# platform_hal.md §4.2's kv_pair command arguments: each is a packed
# (ScopeKind.FUNCTIONAL, DataType.UINT32, key_id) key per ipc_router.md §3.3,
# never a string name -- a string key has no C++ counterpart once RTTI is
# disabled, and the doc's argument names ("pin_no", "val", ...) are only the
# human-readable label for a given key_id, not the wire key itself.
ARG_CMD_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0)
ARG_QUERY_CMD_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
ARG_SHM_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=2)
ARG_OFFSET = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=3)
ARG_LENGTH = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=4)
ARG_MAX_LEN = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=5)
ARG_NANOS = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=6)
ARG_PIN_NO = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=7)
ARG_VAL = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=8)
ARG_MODE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=9)
ARG_EDGE_TYPE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=10)
ARG_TX_SHM_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=11)
ARG_RX_SHM_HANDLE = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=12)
ARG_CLOCK_HZ = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=13)
ARG_SLAVE_ADDR = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=14)
ARG_TASK_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=15)
ARG_FD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=16)


class HalError(Exception):
    """Base class for HAL-level failures that must map to a recovery-strategy-category."""


class ShmTrap(HalError):
    """
    A guest touched a shared-memory handle it does not own, or a slice
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
# Shared-memory buffer pool (the HAL / vMMIO SHM region)
# ---------------------------------------------------------------------------

FB_CONF_HAL_BUFFER_SIZE = 256  # docs/components/tier1_core/system_config.md 3.3.3
FB_CONF_HAL_MAX_BUFFERS = 4  # docs/components/tier1_core/system_config.md 3.3.3


@dataclass
class ShmHandle:
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


class ShmBufferPool:
    """
    `acquire_buffer()` backed by FB_CONF_HAL_MAX_BUFFERS fixed-size slots
        of at most FB_CONF_HAL_BUFFER_SIZE bytes each: a static pool, not a
        dynamic allocator (platform_hal.md 5.1's "静的固定長バッファプール").
    """

    def __init__(self):
        self._slots: list[ShmHandle | None] = [None] * FB_CONF_HAL_MAX_BUFFERS

    def acquire_buffer(self, task_id: int, size: int) -> ShmHandle:
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
        name = f"fb_shm_{uuid.uuid4().hex[:12]}"
        handle = ShmHandle(name=name, owner_task=task_id, capacity=size, _storage=bytearray(size))
        self._slots[slot_idx] = handle
        return handle

    def release_buffer(self, task_id: int, handle: ShmHandle) -> None:
        for i, s in enumerate(self._slots):
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    raise ShmTrap(f"task {task_id} cannot release {handle.name}: not the owner")
                self._slots[i] = None
                return
        raise ShmTrap(f"task {task_id} cannot release {handle.name}: not found")

    def _resolve(self, task_id: int, handle: ShmHandle) -> ShmHandle:
        for s in self._slots:
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    raise ShmTrap(
                        f"task {task_id} does not own {handle.name} (owner={s.owner_task}); "
                        "no linear-memory pointer would ever bypass this check"
                    )
                return s
        raise ShmTrap(f"handle {handle.name} does not exist (stale, or never acquired)")

    def close_all(self) -> None:
        for i in range(len(self._slots)):
            self._slots[i] = None

    def can_view(self, task_id: int, handle: ShmHandle, offset: int, length: int) -> bool:
        """
        Non-throwing precondition check for view(): same ownership/bounds
        rules, but a bool return instead of raising ShmTrap, for callers
        that must not depend on catching an exception (exceptions disabled
        in the target C++ build).
        """
        for s in self._slots:
            if s is not None and s.name == handle.name:
                if s.owner_task != task_id:
                    return False
                return 0 <= offset and 0 <= length and offset + length <= s.capacity
        return False

    def view(self, task_id: int, handle: ShmHandle, offset: int, length: int) -> memoryview:
        """
        Resolves a bounds-checked (offset, length) window inside `handle`.
                This is what interface_wit.md 5.3's `shm-slice{handle, offset, len}`
                actually resolves to at the HAL layer.
        """

        record = self._resolve(task_id, handle)
        if offset < 0 or length < 0 or offset + length > record.capacity:
            raise ShmTrap(
                f"shm-slice(offset={offset}, len={length}) escapes {handle.name}'s "
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
    Matches platform_hal.md §5.1's `control(id, cmd, params: ipc-message)`:
    exactly one statically-typed params argument, always a FlatMapView over
    packed kv_pair keys (ipc_router.md §3.3) -- no kwargs escape hatch, no
    runtime inspection of what was passed (C++ has neither RTTI nor
    reflection to do that with).
    """

    def __init__(self, uri: str, supported_commands: Sequence[int] = ()):
        self.uri = uri
        # CMD_QUERY_CAPS (0x00) is always supported.
        self._supported_commands_storage = sorted({0x00, *supported_commands})
        self.supported_commands = FlatSetView(self._supported_commands_storage)

    def is_supported(self, cmd_id: int) -> int:
        """Checks if this driver supports the given command ID (1=True, 0=False)."""
        return 1 if cmd_id in self.supported_commands else 0

    def dispatch(self, cmd_id: int, params: FlatMapView) -> object:
        """Dispatches an IPC command to the driver handler."""
        if cmd_id == 0x00:  # CMD_QUERY_CAPS
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
                0x01,  # CMD_STREAM_WRITE_SHM
                0x02,  # CMD_STREAM_READ_SHM
                0x03,  # CMD_STREAM_FLUSH
                0x04,  # CMD_STREAM_CLOSE
            ),
        )
        self.transport = transport or UartTransport()

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == 0x01:  # STREAM_WRITE_SHM
            # platform_hal.md §4.2: shm_handle/offset/len resolve a zero-copy
            # SHM slice; this dummy has no pool reference to resolve one
            # against, so it stands in with the slice length only.
            length = params.find(ARG_LENGTH)
            return 0 if length is None else length
        elif cmd_id == 0x02:  # STREAM_READ_SHM
            return self.transport.drain()
        elif cmd_id in (0x03, 0x04):
            return 0
        return None


class DummyGpioDriver(HalDriver):
    """Dummy GPIO Driver supporting Pin R/W, Configuration, and Edge IRQ."""

    # platform_hal.md doesn't fix a pin count; a real MCU GPIO port is a
    # small, bounded set, so a fixed-size array (not a dict) models it.
    _MAX_PINS = 64

    def __init__(self, uri: str = "fireball://device/gpio/0"):
        super().__init__(
            uri,
            supported_commands=(
                0x20,  # CMD_GPIO_SET_PIN
                0x21,  # CMD_GPIO_GET_PIN
                0x22,  # CMD_GPIO_CONFIG_PIN
                0x23,  # CMD_GPIO_SUBSCRIBE_EDGE
            ),
        )
        self.pins: list[bool] = [False] * self._MAX_PINS
        self.modes: list[int] = [0] * self._MAX_PINS

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        pin = params.find(ARG_PIN_NO) or 0
        if cmd_id == 0x20:  # SET_PIN
            self.pins[pin] = bool(params.find(ARG_VAL))
            return 0
        elif cmd_id == 0x21:  # GET_PIN
            return 1 if self.pins[pin] else 0
        elif cmd_id == 0x22:  # CONFIG_PIN
            self.modes[pin] = params.find(ARG_MODE) or 0
            return 0
        elif cmd_id == 0x23:  # SUBSCRIBE_EDGE
            return 1  # pollable handle
        return None


class DummyTimerDriver(HalDriver):
    """Dummy Timer Driver supporting Monotonic Clock and Subscriptions."""

    def __init__(self, uri: str = "fireball://device/timer/0"):
        super().__init__(
            uri,
            supported_commands=(
                0x10,  # CMD_CLOCK_GET_NOW
                0x11,  # CMD_CLOCK_SUBSCRIBE
                0x12,  # CMD_CLOCK_GET_RES
            ),
        )
        self.timer = Timer()

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == 0x10:  # CLOCK_GET_NOW
            return self.timer.get_now_ns()
        elif cmd_id == 0x11:  # CLOCK_SUBSCRIBE
            return 1  # pollable handle
        elif cmd_id == 0x12:  # CLOCK_GET_RES
            return 1_000_000  # 1ms
        return None


class DummyBusDriver(HalDriver):
    """Dummy I2C/SPI Bus Driver supporting Zero-Copy SHM Transfer."""

    def __init__(self, uri: str = "fireball://device/i2c/0"):
        super().__init__(
            uri,
            supported_commands=(
                0x30,  # CMD_BUS_TRANSFER_SHM
                0x31,  # CMD_BUS_CONFIG
            ),
        )

    def _handle_command(self, cmd_id: int, params: FlatMapView) -> object:
        if cmd_id == 0x30:  # BUS_TRANSFER_SHM
            return params.find(ARG_LENGTH) or 0
        elif cmd_id == 0x31:  # BUS_CONFIG
            return 0
        return None


ARG_RESULT = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0xFF)


class HalTask:
    """
    COOS Task for the Hardware Abstraction Layer ({META_3TierSeparation}, {platform_hal.md}).
    HAL operates as an independent cooperative task on COOS.
    All communications with HAL (from Runtime, Debugger, Core Services) occur
    strictly via IPC messages across CSP rendezvous channels.
    HalTask listens for incoming IPC messages directed to HAL device URIs,
    dispatches them to the corresponding registered HalDriver,
    and records execution results.
    """

    def __init__(
        self,
        ipc: IPCRouter,
        drivers: Sequence[HalDriver] | None = None,
    ):
        self.ipc = ipc
        self.drivers: dict[str, HalDriver] = {}
        if drivers:
            for d in drivers:
                self.register_driver(d)
        self.running = True
        self.last_handled_uri: str | None = None
        self.last_handled_cmd: int | None = None
        self.last_result: object = None
        self.processed_count: int = 0

    def register_driver(self, driver: HalDriver) -> None:
        self.drivers[driver.uri] = driver

    def get_driver(self, uri: str) -> HalDriver | None:
        return self.drivers.get(uri)

    def run(self):
        """
        Coroutine body of the HAL server task.
        Runs continuously in COOS, listening on registered device URIs via CSP rendezvous.
        """
        while self.running:
            default_uri = next(iter(self.drivers.keys()), "fireball://device/uart/0")
            status, msg = yield from self.ipc.recv()
            if status != IpcStatus.COMPLETED or msg is None:
                yield (ChannelAction.BLOCK, None)
                continue

            self.processed_count += 1
            cmd_id = msg.get(ARG_CMD_ID)
            if cmd_id is None:
                cmd_id = msg.get(ARG_QUERY_CMD_ID, 0x00)

            # Dispatch to appropriate driver based on command ID hierarchy
            target_driver = None
            if 0x01 <= cmd_id <= 0x04:
                target_driver = self.drivers.get("fireball://device/uart/0")
            elif 0x10 <= cmd_id <= 0x12:
                target_driver = self.drivers.get("fireball://device/timer/0")
            elif 0x20 <= cmd_id <= 0x23:
                target_driver = self.drivers.get("fireball://device/gpio/0")
            elif 0x30 <= cmd_id <= 0x31:
                target_driver = self.drivers.get("fireball://device/i2c/0")
            else:
                target_driver = self.drivers.get(default_uri)

            result = None
            if target_driver is not None:
                result = target_driver.dispatch(cmd_id, msg.payload)

            self.last_handled_uri = default_uri
            self.last_handled_cmd = cmd_id
            self.last_result = result
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
