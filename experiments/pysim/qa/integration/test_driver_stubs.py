"""TEST-HAL-17..25: reusable QA device profiles and real IPC/HAL integration.

ADC/PWM payloads, pin modes and edge numbers are local QA profiles. The
compiled guest uses an explicit QA adapter, not production HAL WIT lowering.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from pathlib import Path

import pytest
from tier2_runtime.hal.dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_CLOCK_HZ,
    ARG_EDGE_TYPE,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_MODE,
    ARG_OFFSET,
    ARG_PIN_NO,
    ARG_POLLABLE_HANDLE,
    ARG_QUERY_CMD_ID,
    ARG_RX_BUFFER_HANDLE,
    ARG_SLAVE_ADDR,
    ARG_TX_BUFFER_HANDLE,
    ARG_VAL,
    HalBufferMapStatus,
    WasiIpcCmd,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from ipc_router import Role
from qa.shared.fixtures.hal_stubs import (
    AdcStubDriver,
    GpioStubDriver,
    PollStubDriver,
    PwmStubDriver,
    StreamStubDriver,
    StubCommand,
    StubDrivers,
    StubPlatform,
    TimerStubDriver,
    stub_platform,
)
from scheduler import ChannelAction, TaskState
from system_containers import ReadOnlyFlatMapView

ROOT = Path(__file__).resolve().parents[4]
STREAMS = ("uart", "rtt", "stdout", "adc")
ENDPOINTS = ("uart", "rtt", "stdout", "gpio", "i2c", "spi", "timer", "adc", "pwm")


def uri(name: str) -> str:
    return f"fireball://hal/{name}/0"


def params(entries: Sequence[tuple[int, int]] = ()) -> ReadOnlyFlatMapView:
    return ReadOnlyFlatMapView(tuple(sorted(entries)))


def command(
    platform: StubPlatform,
    name: str,
    cmd: int,
    entries: Sequence[tuple[int, int]] = (),
) -> int:
    response = platform.engine.send_ipc_command(uri(name), cmd, params(entries))
    assert response.response_code == 0
    return response.value


def stream(
    platform: StubPlatform,
    name: str,
    cmd: int,
    offset: int,
    length: int,
) -> int:
    pool = platform.system.pool
    assert pool.map_for_io(0) == HalBufferMapStatus.MAPPED
    try:
        key = ARG_MAX_LEN if cmd == WasiIpcCmd.STREAM_READ_BUFFER else ARG_LENGTH
        return command(
            platform,
            name,
            cmd,
            (
                (ARG_BUFFER_HANDLE, 0),
                (ARG_OFFSET, offset),
                (key, length),
            ),
        )
    finally:
        pool.unmap_after_io(0)


def stream_driver(drivers: StubDrivers, name: str) -> StreamStubDriver:
    return {"uart": drivers.uart, "rtt": drivers.rtt, "stdout": drivers.stdout, "adc": drivers.adc}[
        name
    ]


def seed_slots(platform: StubPlatform) -> tuple[bytes, ...]:
    for index in range(4):
        platform.system.pool.buffer(index)._storage[:] = bytes((0xA5 + index,)) * 256
    return slots(platform)


def slots(platform: StubPlatform) -> tuple[bytes, ...]:
    return tuple(bytes(platform.system.pool.buffer(index)._storage) for index in range(4))


def configure_adc(platform: StubPlatform) -> None:
    assert (
        command(
            platform, "adc", StubCommand.ADC_CONFIGURE, ((ARG_PIN_NO, 3), (ARG_CLOCK_HZ, 48_000))
        )
        == 0
    )
    assert platform.drivers.adc.configuration == tuple(
        sorted(
            ((ARG_PIN_NO, 3), (ARG_CLOCK_HZ, 48_000)),
        )
    )


@pytest.mark.parametrize("name", ENDPOINTS)
def test_stub_capabilities_match_each_device_profile(name: str) -> None:
    """TEST-HAL-17: real query replies and unsupported commands, one device per task."""
    drivers = StubDrivers.create()
    supported = {
        "uart": (1, 2, 3, 4),
        "rtt": (1, 2, 3, 4),
        "stdout": (1, 2, 3, 4),
        "adc": (1, 2, 3, 4, 0x100),
        "pwm": (0x101, 0x102),
        "gpio": (0x20, 0x21, 0x22, 0x23, 0x40, 0x41, 0x42),
        "i2c": (0x30, 0x31),
        "spi": (0x30, 0x31),
        "timer": (0x10, 0x11, 0x12, 0x40, 0x41, 0x42),
    }[name]
    driver = next(driver for address, _, driver in drivers.endpoints() if address == uri(name))
    with stub_platform(drivers, (uri(name),)) as platform:
        for candidate in (*WasiIpcCmd, *StubCommand):
            assert driver.is_supported(candidate) == int(candidate == 0 or candidate in supported)
        assert (
            command(platform, name, WasiIpcCmd.QUERY_CAPS, ((ARG_QUERY_CMD_ID, supported[0]),)) == 1
        )
        assert command(platform, name, WasiIpcCmd.QUERY_CAPS, ((ARG_QUERY_CMD_ID, 0xFFFF),)) == 0
        assert all(item.task_id == driver.expected_task_id for item in driver.observations)
        before = tuple(driver.observations)
        task = platform.system.scheduler.get_task(driver.expected_task_id)
        assert task is not None
        with platform.system.scheduler.task_context(task), pytest.raises(AssertionError):
            driver.dispatch(0xFFFF, params())
        assert tuple(driver.observations) == before


@pytest.mark.parametrize("name", STREAMS)
@pytest.mark.parametrize("offset,length", ((0, 0), (256, 0), (17, 7), (0, 256)))
@pytest.mark.parametrize("limit", (None, 0, 3))
def test_stream_stubs_preserve_slices_and_independent_rx_tx(
    name: str,
    offset: int,
    length: int,
    limit: int | None,
) -> None:
    """TEST-HAL-18: actual output/input bytes, short transfers, tails and unmapping."""
    drivers = StubDrivers.create()
    driver = stream_driver(drivers, name)
    driver.read_limit = driver.write_limit = limit
    with stub_platform(drivers, (uri(name),)) as platform:
        if name == "adc":
            configure_adc(platform)
        before = seed_slots(platform)
        payload = bytes((index * 37 + 11) & 255 for index in range(length))
        platform.system.pool.buffer(0)._storage[offset : offset + length] = payload
        outgoing_slots = slots(platform)
        driver.feed_input(bytes(reversed(payload)))
        count = length if limit is None else min(length, limit)
        assert stream(platform, name, WasiIpcCmd.STREAM_WRITE_BUFFER, offset, length) == count
        assert driver.drain_output() == payload[:count]
        assert driver.drain_output() == b""
        assert slots(platform) == outgoing_slots
        assert stream(platform, name, WasiIpcCmd.STREAM_READ_BUFFER, offset, length) == count
        expected = bytearray(outgoing_slots[0])
        expected[offset : offset + count] = bytes(reversed(payload))[:count]
        assert slots(platform) == (bytes(expected), *before[1:])
        assert driver.transport._input_len == length - count
        assert platform.system.pool._mapped_task_id is None
        assert platform.system.pool._mapped_buffer_id is None


@settings(max_examples=24, deadline=None, derandomize=True)
@given(payloads=st.lists(st.binary(max_size=128), min_size=1, max_size=3))
@pytest.mark.parametrize("name", STREAMS)
def test_stream_stub_histories_reuse_slots_without_losing_bytes(
    name: str, payloads: list[bytes]
) -> None:
    """TEST-HAL-19: whole-byte oracle after every operation in generated histories."""
    drivers = StubDrivers.create()
    driver = stream_driver(drivers, name)
    with stub_platform(drivers, (uri(name),)) as platform:
        if name == "adc":
            configure_adc(platform)
        seed_slots(platform)
        for data in payloads:
            before = slots(platform)
            platform.system.pool.buffer(0)._storage[13 : 13 + len(data)] = data
            expected = bytearray(before[0])
            expected[13 : 13 + len(data)] = data
            assert stream(platform, name, WasiIpcCmd.STREAM_WRITE_BUFFER, 13, len(data)) == len(
                data
            )
            assert driver.drain_output() == data
            assert slots(platform) == (bytes(expected), *before[1:])
            assert platform.system.pool._mapped_task_id is None


@pytest.mark.parametrize("name", STREAMS)
def test_stream_close_flush_and_invalid_slice_preserve_state(name: str) -> None:
    """TEST-HAL-18: invalid request does not consume input or write; explicit lifecycle."""
    drivers = StubDrivers.create()
    driver = stream_driver(drivers, name)
    with stub_platform(drivers, (uri(name),)) as platform:
        if name == "adc":
            configure_adc(platform)
        before = seed_slots(platform)
        driver.feed_input(b"pending")
        task = platform.system.scheduler.get_task(driver.expected_task_id)
        assert task is not None
        pool = platform.system.pool
        assert pool.map_for_io(0) == HalBufferMapStatus.MAPPED
        try:
            with platform.system.scheduler.task_context(task), pytest.raises(AssertionError):
                driver.dispatch(
                    WasiIpcCmd.STREAM_READ_BUFFER,
                    params(
                        (
                            (ARG_BUFFER_HANDLE, 0),
                            (ARG_OFFSET, 255),
                            (ARG_MAX_LEN, 2),
                        )
                    ),
                )
        finally:
            pool.unmap_after_io(0)
        assert slots(platform) == before and driver.transport._input_len == 7
        assert driver.drain_output() == b""
        assert command(platform, name, WasiIpcCmd.STREAM_FLUSH) == 0
        assert driver.flush_count == 1 and driver.transport._input_len == 7
        assert command(platform, name, WasiIpcCmd.STREAM_CLOSE) == 0
        assert driver.closed
        with pytest.raises(AssertionError, match="closed"):
            driver.feed_input(b"new")
        assert slots(platform) == before and driver.transport._input_len == 7


@pytest.mark.parametrize("edge", (1, 2, 3))
@pytest.mark.parametrize("initial", (0, 1))
def test_gpio_stub_commands_and_edges_are_pin_scoped(edge: int, initial: int) -> None:
    """TEST-HAL-20: configured pin/edge, unrelated input, readiness and stale generation."""
    drivers = StubDrivers.create()
    with stub_platform(drivers, (uri("gpio"),)) as platform:
        for pin in (0, 1):
            assert (
                command(
                    platform, "gpio", WasiIpcCmd.GPIO_CONFIG_PIN, ((ARG_PIN_NO, pin), (ARG_MODE, 0))
                )
                == 0
            )
        drivers.gpio.set_input(0, initial)
        handle = command(
            platform,
            "gpio",
            WasiIpcCmd.GPIO_SUBSCRIBE_EDGE,
            ((ARG_PIN_NO, 0), (ARG_EDGE_TYPE, edge)),
        )
        assert not platform.engine.poll_check(uri("gpio"), handle)
        drivers.gpio.set_input(1, 1)
        drivers.gpio.set_input(0, initial)
        assert not platform.engine.poll_check(uri("gpio"), handle)
        drivers.gpio.set_input(0, 1 - initial)
        expected = bool(edge & (1 if initial == 0 else 2))
        assert platform.engine.poll_check(uri("gpio"), handle) == expected
        assert command(platform, "gpio", WasiIpcCmd.GPIO_GET_PIN, ((ARG_PIN_NO, 0),)) == 1 - initial
        if expected:
            assert platform.engine.poll_wait(uri("gpio"), handle)
        assert platform.engine.poll_drop(uri("gpio"), handle) == 0
        replacement = command(
            platform,
            "gpio",
            WasiIpcCmd.GPIO_SUBSCRIBE_EDGE,
            ((ARG_PIN_NO, 0), (ARG_EDGE_TYPE, edge)),
        )
        assert replacement != handle
        with pytest.raises(AssertionError, match="stale"):
            drivers.gpio.pollable(handle)
        assert drivers.gpio.levels == [1 - initial, 1, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("name", ("i2c", "spi"))
@pytest.mark.parametrize("length", (0, 1, 256))
@pytest.mark.parametrize("limit", (None, 0, 5))
def test_bus_stubs_capture_tx_before_in_place_rx_and_preserve_tails(
    name: str,
    length: int,
    limit: int | None,
) -> None:
    """TEST-HAL-21: single mapped slot, known reply, short count and all-slot oracle."""
    drivers = StubDrivers.create()
    driver = drivers.i2c if name == "i2c" else drivers.spi
    driver.transfer_limit = limit
    with stub_platform(drivers, (uri(name),)) as platform:
        assert (
            command(
                platform,
                name,
                WasiIpcCmd.BUS_CONFIG,
                ((ARG_CLOCK_HZ, 100_000), (ARG_SLAVE_ADDR, 0x52), (ARG_MODE, 1)),
            )
            == 0
        )
        assert driver.configuration == (100_000, 0x52, 1)
        before = seed_slots(platform)
        incoming = bytes(index ^ 0x5A for index in range(length))
        driver.feed_reply(incoming)
        assert platform.system.pool.map_for_io(0) == HalBufferMapStatus.MAPPED
        count = length if limit is None else min(length, limit)
        try:
            assert (
                command(
                    platform,
                    name,
                    WasiIpcCmd.BUS_TRANSFER_BUFFER,
                    (
                        (ARG_TX_BUFFER_HANDLE, 0),
                        (ARG_RX_BUFFER_HANDLE, 0),
                        (ARG_LENGTH, length),
                    ),
                )
                == count
            )
        finally:
            platform.system.pool.unmap_after_io(0)
        expected = bytearray(before[0])
        expected[:count] = incoming[:count]
        assert slots(platform) == (bytes(expected), *before[1:])
        assert driver.transmitted == [before[0][:count]]
        assert driver.transfer_configurations == [(100_000, 0x52, 1)]
        assert driver.reply == incoming[count:]
        assert platform.system.pool._mapped_buffer_id is None


@pytest.mark.parametrize("invalid", ("tx", "rx", "bounds", "missing"))
def test_bus_stub_rejection_preserves_reply_and_all_buffers(invalid: str) -> None:
    """TEST-HAL-22: no second mapping is fabricated for distinct TX/RX handles."""
    drivers = StubDrivers.create()
    with stub_platform(drivers, (uri("spi"),)) as platform:
        assert (
            command(
                platform,
                "spi",
                WasiIpcCmd.BUS_CONFIG,
                ((ARG_CLOCK_HZ, 1), (ARG_SLAVE_ADDR, 0), (ARG_MODE, 0)),
            )
            == 0
        )
        before = seed_slots(platform)
        drivers.spi.feed_reply(b"reply")
        values = {ARG_TX_BUFFER_HANDLE: 0, ARG_RX_BUFFER_HANDLE: 0, ARG_LENGTH: 5}
        if invalid == "tx":
            values[ARG_TX_BUFFER_HANDLE] = 4
        elif invalid == "rx":
            values[ARG_RX_BUFFER_HANDLE] = 1
        elif invalid == "bounds":
            values[ARG_LENGTH] = 257
        else:
            del values[ARG_RX_BUFFER_HANDLE]
        assert platform.system.pool.map_for_io(0) == HalBufferMapStatus.MAPPED
        task = platform.system.scheduler.get_task(drivers.spi.expected_task_id)
        assert task is not None
        before_calls = tuple(drivers.spi.observations)
        try:
            with platform.system.scheduler.task_context(task), pytest.raises(AssertionError):
                drivers.spi.dispatch(WasiIpcCmd.BUS_TRANSFER_BUFFER, params(tuple(values.items())))
            assert platform.system.pool._mapped_buffer_id == 0
            assert slots(platform) == before
            assert drivers.spi.reply == b"reply" and drivers.spi.transmitted == []
            assert drivers.spi.transfer_configurations == []
            assert tuple(drivers.spi.observations) == before_calls
        finally:
            platform.system.pool.unmap_after_io(0)


def test_adc_pwm_stubs_require_setup_and_own_configuration_snapshots() -> None:
    """TEST-HAL-23: QA setup precondition, state after commands and no retained IPC view."""
    adc, pwm = AdcStubDriver(), PwmStubDriver()
    with pytest.raises(AssertionError, match="not configured"):
        adc.dispatch(WasiIpcCmd.STREAM_READ_BUFFER, params())
    with pytest.raises(AssertionError, match="not configured"):
        pwm.dispatch(StubCommand.PWM_WRITE, params(((ARG_VAL, 25),)))
    assert adc.configuration is None and pwm.output is None
    assert adc.observations == [] and pwm.observations == []
    entries = [(ARG_CLOCK_HZ, 20_000)]
    assert adc.dispatch(StubCommand.ADC_CONFIGURE, ReadOnlyFlatMapView(entries)) == 0
    assert pwm.dispatch(StubCommand.PWM_CONFIGURE, ReadOnlyFlatMapView(entries)) == 0
    entries[0] = (ARG_CLOCK_HZ, 1)
    assert adc.configuration == pwm.configuration == ((ARG_CLOCK_HZ, 20_000),)
    assert pwm.dispatch(StubCommand.PWM_WRITE, params(((ARG_PIN_NO, 2), (ARG_VAL, 25)))) == 0
    assert pwm.output == tuple(sorted(((ARG_PIN_NO, 2), (ARG_VAL, 25))))
    assert pwm.is_supported(WasiIpcCmd.STREAM_WRITE_BUFFER) == 0


def test_timer_stub_manual_deadlines_keep_u64_and_stale_handles() -> None:
    """TEST-HAL-24: exact before/at deadline, u64 result, explicit time, slot reuse."""
    drivers = StubDrivers.create()
    drivers.timer.advance(0xFEDC_BA98_0000_0001)
    with stub_platform(drivers, (uri("timer"),)) as platform:
        assert command(platform, "timer", WasiIpcCmd.CLOCK_GET_NOW) == 0xFEDC_BA98_0000_0001
        assert command(platform, "timer", WasiIpcCmd.CLOCK_GET_RES) == 1
        handle = platform.engine.clock_subscribe(uri("timer"), 500)
        drivers.timer.advance(499)
        assert not platform.engine.poll_check(uri("timer"), handle)
        drivers.timer.advance(1)
        assert platform.engine.poll_wait(uri("timer"), handle)
        assert platform.engine.poll_drop(uri("timer"), handle) == 0
        assert platform.engine.clock_subscribe(uri("timer"), 0) != handle
        with pytest.raises(AssertionError, match="stale"):
            drivers.timer.pollable(handle)


def test_timer_stub_wait_allows_peer_to_advance_test_time() -> None:
    """TEST-HAL-24: pending poll uses the real cooperative wait, with explicit stimulus."""
    drivers = StubDrivers.create()
    with stub_platform(drivers, (uri("timer"),)) as platform:
        handle = platform.engine.clock_subscribe(uri("timer"), 500)
        task_id = drivers.timer.expected_task_id
        assert task_id is not None
        hal_task = platform.system.scheduler.get_task(task_id)
        assert hal_task is not None
        observed: list[int] = []

        def peer() -> Generator[tuple[ChannelAction, None], None, None]:
            for _ in range(8):
                if hal_task.state == TaskState.BLOCKED_TIMER:
                    break
                yield (ChannelAction.YIELD, None)
            assert hal_task.state == TaskState.BLOCKED_TIMER
            observed.append(drivers.timer.now_ns)
            drivers.timer.advance(500)
            yield (ChannelAction.YIELD, None)

        platform.system.scheduler.spawn("stub_clock_peer", peer(), role=Role.RUNTIME)
        assert platform.engine.poll_wait(uri("timer"), handle)
        assert observed == [0] and drivers.timer.now_ns == 500
        assert platform.engine.poll_drop(uri("timer"), handle) == 0


@pytest.mark.parametrize("driver", (GpioStubDriver, TimerStubDriver))
def test_stub_pollable_capacity_rejection_and_reuse(driver: type[PollStubDriver]) -> None:
    """TEST-HAL-24: bounded reservations reject without invalidating existing handles."""
    instance = driver()
    handles = tuple(instance.reserve() for _ in range(16))
    before = tuple((item.generation, item.active, item.ready) for item in instance.pollables)
    with pytest.raises(AssertionError, match="capacity"):
        instance.reserve()
    assert (
        tuple((item.generation, item.active, item.ready) for item in instance.pollables) == before
    )
    assert (
        instance.dispatch(WasiIpcCmd.POLL_DROP, params(((ARG_POLLABLE_HANDLE, handles[5]),))) == 0
    )
    replacement = instance.reserve()
    assert replacement != handles[5] and replacement & 15 == handles[5] & 15
    for handle in (*handles[:5], *handles[6:], replacement):
        assert instance.pollable(handle).active


def test_stub_platform_keeps_eight_devices_and_same_role_instances_separate() -> None:
    """TEST-HAL-17: separate channels/tasks and output for UART-shaped test instances."""
    drivers = StubDrivers.create()
    names = tuple(name for name in ENDPOINTS if name != "stdout")
    with stub_platform(drivers, tuple(uri(name) for name in names)) as platform:
        selected = tuple(
            driver for address, _, driver in drivers.endpoints() if address != uri("stdout")
        )
        task_ids = tuple(driver.expected_task_id for driver in selected)
        assert len(set(task_ids)) == 8
        seed_slots(platform)
        for name, data in (("uart", b"UART"), ("rtt", b"RTT!")):
            platform.system.pool.buffer(0)._storage[:4] = data
            assert stream(platform, name, WasiIpcCmd.STREAM_WRITE_BUFFER, 0, 4) == 4
        assert drivers.uart.drain_output() == b"UART"
        assert drivers.rtt.drain_output() == b"RTT!"
        assert drivers.adc.drain_output() == b""
        assert drivers.pwm.configuration is None
