"""QA-only device stubs using the real HalDriver/HalTask command boundary.

Common commands come from hal_dispatch.md §5.2. ADC/PWM command IDs and
configuration payloads below are a local test profile, not a product ABI.
Snapshots own their bytes/entries; no IPC view is retained after its reply.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from unittest.mock import patch

import ipc_router
from ipc_router import Role, ServiceDescriptor
from system import System
from system_containers import ReadOnlyFlatMapView
from tier2_runtime.hal.dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_CLOCK_HZ,
    ARG_CMD_ID,
    ARG_EDGE_TYPE,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_MODE,
    ARG_NANOS,
    ARG_NANOS_HI,
    ARG_OFFSET,
    ARG_PIN_NO,
    ARG_POLLABLE_HANDLE,
    ARG_RX_BUFFER_HANDLE,
    ARG_SLAVE_ADDR,
    ARG_TX_BUFFER_HANDLE,
    ARG_VAL,
    HalDriver,
    WasiIpcCmd,
)
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.wasi.context import Wasi03pEngine


class StubCommand(IntEnum):
    """QA-local extension IDs; never added to WasiIpcCmd or the product WIT."""

    ADC_CONFIGURE = 0x100
    PWM_CONFIGURE = 0x101
    PWM_WRITE = 0x102


def required(params: ReadOnlyFlatMapView, key: int) -> int:
    value = params.find(key)
    assert value is not None, f"missing stub argument {key:#x}"
    assert 0 <= value <= 0xFFFF_FFFF, "stub argument must be u32"
    return value


def snapshot(params: ReadOnlyFlatMapView) -> tuple[tuple[int, int], ...]:
    entries = tuple((key, value) for key, value in params.entries if key != ARG_CMD_ID)
    assert all(0 <= value <= 0xFFFF_FFFF for _, value in entries)
    return entries


@dataclass(frozen=True)
class CommandObservation:
    command: int
    parameters: tuple[tuple[int, int], ...]
    task_id: int | None


class StubDriver(HalDriver):
    """Record completed device commands and optionally assert their HAL task."""

    def __init__(self) -> None:
        super().__init__()
        self.expected_task_id: int | None = None
        self.observations: list[CommandObservation] = []

    def dispatch(self, cmd_id: int, params: ReadOnlyFlatMapView) -> int:
        task_id = None if self._buffer_pool is None else self._buffer_pool.current_task_id
        if self.expected_task_id is not None:
            assert task_id == self.expected_task_id, "stub called outside its HAL task"
        parameters = snapshot(params)
        result = super().dispatch(cmd_id, params)
        self.observations.append(CommandObservation(cmd_id, parameters, task_id))
        return result

    def buffer_view(self, handle: int, offset: int, length: int) -> memoryview:
        assert self._buffer_pool is not None
        return self._buffer_pool.view_for_driver(handle, offset, length)


class StreamStubDriver(StubDriver):
    """Independent RX/TX storage and controllable short transfers for UART/RTT/stdout."""

    def __init__(self, transport: StreamTransport | None = None) -> None:
        super().__init__()
        self.transport = transport if transport is not None else StreamTransport()
        self.read_limit: int | None = None
        self.write_limit: int | None = None
        self.closed = False
        self.flush_count = 0
        self.register_command(WasiIpcCmd.STREAM_READ_BUFFER, self._read)
        self.register_command(WasiIpcCmd.STREAM_WRITE_BUFFER, self._write)
        self.register_command(WasiIpcCmd.STREAM_FLUSH, self._flush)
        self.register_command(WasiIpcCmd.STREAM_CLOSE, self._close)

    def _view(self, params: ReadOnlyFlatMapView, key: int) -> memoryview:
        assert not self.closed, "stub stream is closed"
        return self.buffer_view(
            required(params, ARG_BUFFER_HANDLE), required(params, ARG_OFFSET), required(params, key)
        )

    @staticmethod
    def _count(requested: int, limit: int | None) -> int:
        assert limit is None or limit >= 0
        return requested if limit is None else min(requested, limit)

    def _read(self, params: ReadOnlyFlatMapView) -> int:
        view = self._view(params, ARG_MAX_LEN)
        count = self._count(len(view), self.read_limit)
        if count == 0:
            return 0
        data = self.transport.read_input(count)
        view[: len(data)] = data
        return len(data)

    def _write(self, params: ReadOnlyFlatMapView) -> int:
        view = self._view(params, ARG_LENGTH)
        count = self._count(len(view), self.write_limit)
        return self.transport.write(view[:count])

    def _flush(self, params: ReadOnlyFlatMapView) -> int:
        assert not self.closed, "stub stream is closed"
        self.flush_count += 1
        return 0

    def _close(self, params: ReadOnlyFlatMapView) -> int:
        self.closed = True
        return 0

    def feed_input(self, data: bytes) -> int:
        assert not self.closed, "stub stream is closed"
        return self.transport.feed_input(data)

    def drain_output(self) -> bytes:
        return self.transport.drain_output()


class AdcStubDriver(StreamStubDriver):
    """Capture setup commands; test-supplied sample bytes travel through stream I/O."""

    def __init__(self) -> None:
        super().__init__()
        self.configuration: tuple[tuple[int, int], ...] | None = None
        self.register_command(StubCommand.ADC_CONFIGURE, self._configure)

    def _configure(self, params: ReadOnlyFlatMapView) -> int:
        configuration = snapshot(params)
        assert configuration, "ADC stub configuration is empty"
        self.configuration = configuration
        return 0

    def _view(self, params: ReadOnlyFlatMapView, key: int) -> memoryview:
        assert self.configuration is not None, "ADC stub is not configured"
        return super()._view(params, key)


class PwmStubDriver(StubDriver):
    """Capture configuration and output commands without inventing physical units."""

    def __init__(self) -> None:
        super().__init__()
        self.configuration: tuple[tuple[int, int], ...] | None = None
        self.output: tuple[tuple[int, int], ...] | None = None
        self.register_command(StubCommand.PWM_CONFIGURE, self._configure)
        self.register_command(StubCommand.PWM_WRITE, self._write)

    def _configure(self, params: ReadOnlyFlatMapView) -> int:
        configuration = snapshot(params)
        assert configuration, "PWM stub configuration is empty"
        self.configuration = configuration
        return 0

    def _write(self, params: ReadOnlyFlatMapView) -> int:
        assert self.configuration is not None, "PWM stub is not configured"
        output = snapshot(params)
        assert output, "PWM stub command is empty"
        self.output = output
        return 0


@dataclass
class Pollable:
    generation: int = 0
    active: bool = False
    ready: bool = False
    source: int = 0
    selector: int = 0
    deadline: int = 0


class PollStubDriver(StubDriver):
    """QA event bank with the existing timer's 16-slot generation-handle shape."""

    def __init__(self) -> None:
        super().__init__()
        self.pollables = [Pollable() for _ in range(16)]
        self.register_command(WasiIpcCmd.POLL_CHECK, self._check)
        self.register_command(WasiIpcCmd.POLL_WAIT, self._check)
        self.register_command(WasiIpcCmd.POLL_DROP, self._drop)

    def reserve(self, *, source: int = 0, selector: int = 0, deadline: int = 0) -> int:
        for index, item in enumerate(self.pollables):
            if not item.active:
                assert item.generation < 0x0FFF_FFFF
                item.generation += 1
                item.active, item.ready = True, False
                item.source, item.selector, item.deadline = source, selector, deadline
                return (item.generation << 4) | index
        assert False, "stub pollable capacity exceeded"

    def pollable(self, handle: int) -> Pollable:
        assert 0 <= handle <= 0xFFFF_FFFF
        item = self.pollables[handle & 15]
        assert item.active and item.generation == handle >> 4, "stale stub pollable"
        return item

    def _check(self, params: ReadOnlyFlatMapView) -> int:
        return int(self.pollable(required(params, ARG_POLLABLE_HANDLE)).ready)

    def _drop(self, params: ReadOnlyFlatMapView) -> int:
        self.pollable(required(params, ARG_POLLABLE_HANDLE)).active = False
        return 0

    def poll_wakeup_ns(self, handle: int) -> int:
        self.pollable(handle)
        return time.monotonic_ns() + 1_000_000


class GpioStubDriver(PollStubDriver):
    """QA pin/edge state, not a vMMIO fast path or a physical ISR emulator."""

    def __init__(self, pins: int = 8) -> None:
        assert pins > 0
        super().__init__()
        self.modes: list[int | None] = [None] * pins
        self.levels = [0] * pins
        self.register_command(WasiIpcCmd.GPIO_CONFIG_PIN, self._configure)
        self.register_command(WasiIpcCmd.GPIO_SET_PIN, self._set)
        self.register_command(WasiIpcCmd.GPIO_GET_PIN, self._get)
        self.register_command(WasiIpcCmd.GPIO_SUBSCRIBE_EDGE, self._subscribe)

    def _pin(self, params: ReadOnlyFlatMapView) -> int:
        pin = required(params, ARG_PIN_NO)
        assert pin < len(self.levels), "stub pin out of range"
        return pin

    def _configure(self, params: ReadOnlyFlatMapView) -> int:
        pin, mode = self._pin(params), required(params, ARG_MODE)
        assert mode <= 3, "stub pin mode out of range"
        self.modes[pin] = mode
        return 0

    def _set(self, params: ReadOnlyFlatMapView) -> int:
        pin, value = self._pin(params), required(params, ARG_VAL)
        assert self.modes[pin] == 1, "stub pin is not an output"
        assert value <= 1, "stub pin level out of range"
        self.levels[pin] = value
        return 0

    def _get(self, params: ReadOnlyFlatMapView) -> int:
        return self.levels[self._pin(params)]

    def _subscribe(self, params: ReadOnlyFlatMapView) -> int:
        pin, edge = self._pin(params), required(params, ARG_EDGE_TYPE)
        assert self.modes[pin] is not None and self.modes[pin] != 1
        assert 1 <= edge <= 3, "stub edge selector out of range"
        return self.reserve(source=pin, selector=edge)

    def set_input(self, pin: int, value: int) -> None:
        assert 0 <= pin < len(self.levels) and value in (0, 1)
        assert self.modes[pin] is not None and self.modes[pin] != 1
        old = self.levels[pin]
        self.levels[pin] = value
        if old == value:
            return
        edge = 1 if value else 2
        for item in self.pollables:
            if item.active and item.source == pin and item.selector & edge:
                item.ready = True


class BusStubDriver(StubDriver):
    """I2C/SPI scripted replies; resolve both slices before changing either one."""

    def __init__(self) -> None:
        super().__init__()
        self.configuration: tuple[int, int, int] | None = None
        self.reply = b""
        self.transmitted: list[bytes] = []
        self.transfer_configurations: list[tuple[int, int, int]] = []
        self.transfer_limit: int | None = None
        self.register_command(WasiIpcCmd.BUS_CONFIG, self._configure)
        self.register_command(WasiIpcCmd.BUS_TRANSFER_BUFFER, self._transfer)

    def _configure(self, params: ReadOnlyFlatMapView) -> int:
        self.configuration = (
            required(params, ARG_CLOCK_HZ),
            required(params, ARG_SLAVE_ADDR),
            required(params, ARG_MODE),
        )
        return 0

    def feed_reply(self, data: bytes) -> None:
        self.reply += data

    def _transfer(self, params: ReadOnlyFlatMapView) -> int:
        assert self.configuration is not None, "stub bus is not configured"
        length = required(params, ARG_LENGTH)
        tx = self.buffer_view(required(params, ARG_TX_BUFFER_HANDLE), 0, length)
        rx = self.buffer_view(required(params, ARG_RX_BUFFER_HANDLE), 0, length)
        assert self.transfer_limit is None or self.transfer_limit >= 0
        count = min(length, len(self.reply))
        if self.transfer_limit is not None:
            count = min(count, self.transfer_limit)
        outgoing = bytes(tx[:count])
        incoming = self.reply[:count]
        rx[:count] = incoming
        self.transmitted.append(outgoing)
        self.transfer_configurations.append(self.configuration)
        self.reply = self.reply[count:]
        return count


class TimerStubDriver(PollStubDriver):
    """Explicitly advanced test clock; no wall-clock sleeps or automatic time jumps."""

    def __init__(self, now_ns: int = 0, resolution_ns: int = 1) -> None:
        assert 0 <= now_ns <= 0xFFFF_FFFF_FFFF_FFFF
        assert 0 < resolution_ns <= 0xFFFF_FFFF_FFFF_FFFF
        super().__init__()
        self.now_ns, self.resolution_ns = now_ns, resolution_ns
        self.register_command(WasiIpcCmd.CLOCK_GET_NOW, lambda params: self.now_ns)
        self.register_command(WasiIpcCmd.CLOCK_GET_RES, lambda params: self.resolution_ns)
        self.register_command(WasiIpcCmd.CLOCK_SUBSCRIBE, self._subscribe)

    def advance(self, nanos: int) -> None:
        assert nanos >= 0 and self.now_ns + nanos <= 0xFFFF_FFFF_FFFF_FFFF
        self.now_ns += nanos

    def _subscribe(self, params: ReadOnlyFlatMapView) -> int:
        nanos = required(params, ARG_NANOS) | required(params, ARG_NANOS_HI) << 32
        assert self.now_ns + nanos <= 0xFFFF_FFFF_FFFF_FFFF
        return self.reserve(deadline=self.now_ns + nanos)

    def _check(self, params: ReadOnlyFlatMapView) -> int:
        item = self.pollable(required(params, ARG_POLLABLE_HANDLE))
        item.ready = self.now_ns >= item.deadline
        return int(item.ready)


@dataclass
class StubDrivers:
    uart: StreamStubDriver
    rtt: StreamStubDriver
    stdout: StreamStubDriver
    gpio: GpioStubDriver
    i2c: BusStubDriver
    spi: BusStubDriver
    timer: TimerStubDriver
    adc: AdcStubDriver
    pwm: PwmStubDriver

    @classmethod
    def create(cls) -> StubDrivers:
        return cls(
            StreamStubDriver(),
            StreamStubDriver(),
            StreamStubDriver(),
            GpioStubDriver(),
            BusStubDriver(),
            BusStubDriver(),
            TimerStubDriver(),
            AdcStubDriver(),
            PwmStubDriver(),
        )

    def endpoints(self) -> tuple[tuple[str, Role, StubDriver], ...]:
        # ADC/PWM/RTT use existing allowed leaf roles in this QA composition only.
        # This table does not define their product RBAC policy.
        return (
            ("fireball://hal/adc/0", Role.HAL_UART, self.adc),
            ("fireball://hal/gpio/0", Role.HAL_GPIO, self.gpio),
            ("fireball://hal/i2c/0", Role.HAL_I2C, self.i2c),
            ("fireball://hal/pwm/0", Role.HAL_TIMER, self.pwm),
            ("fireball://hal/rtt/0", Role.HAL_UART, self.rtt),
            ("fireball://hal/spi/0", Role.HAL_SPI, self.spi),
            ("fireball://hal/stdout/0", Role.HAL_STDOUT, self.stdout),
            ("fireball://hal/timer/0", Role.HAL_TIMER, self.timer),
            ("fireball://hal/uart/0", Role.HAL_UART, self.uart),
        )


@dataclass
class StubPlatform:
    system: System
    drivers: StubDrivers
    engine: Wasi03pEngine


@contextmanager
def stub_platform(
    drivers: StubDrivers,
    uris: Sequence[str],
) -> Iterator[StubPlatform]:
    """Select up to eight endpoints in a QA-only static ROM composition.

    Patch the configuration before constructing the real router/channels, and
    restore it after teardown. Every started endpoint owns a separate HalTask.
    """
    assert 1 <= len(uris) <= 8 and len(set(uris)) == len(uris)
    selected = tuple(entry for entry in drivers.endpoints() if entry[0] in uris)
    assert len(selected) == len(uris), "unknown stub URI"
    table = tuple(
        sorted(
            (
                ("fireball://core/coos/0", ServiceDescriptor(Role.CORE_SERVICE)),
                ("fireball://dbg/manager/0", ServiceDescriptor(Role.DEBUGGER)),
                *(
                    (uri, ServiceDescriptor(role, index))
                    for index, (uri, role, _) in enumerate(selected)
                ),
            ),
        )
    )
    with patch.object(ipc_router, "_SERVICE_ENTRIES", table):
        system = System()
        try:
            for uri, _, driver in selected:
                driver.expected_task_id = system.start_hal_driver(driver, uri)
            runtime = system.start_runtime_task(name="stub_driver_client")
            system.scheduler.current_task = runtime
            yield StubPlatform(system, drivers, Wasi03pEngine(system))
        finally:
            system.shutdown()
            for stream in (drivers.uart, drivers.rtt, drivers.stdout, drivers.adc):
                stream.transport.close()
