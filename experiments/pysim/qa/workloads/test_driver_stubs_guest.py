"""WASM guests compiled with WASI-SDK exercise HAL stubs over real IPC."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from tier2_runtime.hal.dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_CLOCK_HZ,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_MODE,
    ARG_OFFSET,
    ARG_PIN_NO,
    ARG_RX_BUFFER_HANDLE,
    ARG_SLAVE_ADDR,
    ARG_TX_BUFFER_HANDLE,
    ARG_VAL,
    HalBufferMapStatus,
    WasiIpcCmd,
)
from interpreter.interpreter import WasmHostFunction, WasmNumber
from ipc_router import Role
from qa.shared.fixtures.hal_stubs import (
    StreamStubDriver,
    StubCommand,
    StubDrivers,
    StubPlatform,
    stub_platform,
)
from qa.shared.helpers import make_native_interpreter
from scheduler import TaskState
from system_containers import ReadOnlyFlatMapView, StaticVector
from tier2_runtime.wasm.reader import parse

ROOT = Path(__file__).resolve().parents[4]
STREAMS = ("uart", "rtt", "stdout", "adc")
ENDPOINTS = ("uart", "rtt", "stdout", "gpio", "i2c", "spi", "timer", "adc", "pwm")


def uri(name: str) -> str:
    return f"fireball://hal/{name}/0"


def params(entries: Sequence[tuple[int, int]] = ()) -> ReadOnlyFlatMapView:
    return ReadOnlyFlatMapView(tuple(sorted(entries)))


def seed_slots(platform: StubPlatform) -> tuple[bytes, ...]:
    for index in range(4):
        platform.system.pool.buffer(index)._storage[:] = bytes((0xA5 + index,)) * 256
    return slots(platform)


def slots(platform: StubPlatform) -> tuple[bytes, ...]:
    return tuple(bytes(platform.system.pool.buffer(index)._storage) for index in range(4))


def stream_driver(drivers: StubDrivers, name: str) -> StreamStubDriver:
    return {"uart": drivers.uart, "rtt": drivers.rtt, "stdout": drivers.stdout, "adc": drivers.adc}[
        name
    ]


@pytest.fixture(scope="module")
def compiled_stub_guest(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("clang-driver-stubs")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/guest_bindings/build_wasi_guest.py"),
            "--output",
            str(output),
            "--driver-stub",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output / "guest.wasm"


class GuestDriverClient:
    """Test-only host import adapter, followed by real URI/IPC/COOS/HalTask."""

    def __init__(self, platform: StubPlatform, address: str, binary: Path) -> None:
        self.platform, self.address = platform, address
        self.module = parse(binary.read_bytes())
        imports = tuple(
            (self.module.import_module_name(i), self.module.import_field_name(i))
            for i in range(len(self.module.imports))
        )
        assert set(imports) == {("fireball-qa", "command"), ("fireball-qa", "stream")}
        bindings: StaticVector[WasmHostFunction | None] = StaticVector.of(
            tuple(self._command if field == "command" else self._stream for _, field in imports),
            capacity=2,
        )
        self.interpreter = make_native_interpreter(self.module, host_functions=bindings)
        assert "clang version" in binary.with_name("compiler.txt").read_text()
        self.call("_initialize", ())

    def call(self, export: str, arguments: tuple[int, ...]) -> int:
        system = self.platform.system
        task_id = system.scheduler.spawn(
            "clang_stub_guest",
            system.run_guest(
                self.interpreter,
                self.module.export_func_index(export),
                arguments,
            ),
            role=Role.RUNTIME,
        )
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
        if export == "_initialize":
            assert tuple(task.result) == ()
            return 0
        assert len(task.result) == 1
        mask = 0xFFFF_FFFF_FFFF_FFFF if export == "command_probe" else 0xFFFF_FFFF
        return int(task.result[0]) & mask

    def send(self, cmd: int, entries: Sequence[tuple[int, int]] = ()) -> int:
        assert len(entries) <= 3
        padded = (*entries, *((0, 0) for _ in range(3 - len(entries))))
        return self.call(
            "command_probe",
            (
                cmd,
                *(value for entry in padded for value in entry),
                len(entries),
            ),
        )

    def _command(self, *args: WasmNumber) -> int:
        values = tuple(int(value) & 0xFFFF_FFFF for value in args)
        assert len(values) == 8 and values[7] <= 3
        entries = tuple((values[1 + 2 * i], values[2 + 2 * i]) for i in range(values[7]))
        mapped = values[0] == WasiIpcCmd.BUS_TRANSFER_BUFFER
        pool = self.platform.system.pool
        handle = 0
        if mapped:
            requested = params(entries).find(ARG_TX_BUFFER_HANDLE)
            assert requested is not None
            handle = requested
            assert pool.map_for_io(handle) == HalBufferMapStatus.MAPPED
        try:
            response = self.platform.engine.send_ipc_command(
                self.address, values[0], params(entries)
            )
            assert response.response_code == 0
            return response.value
        finally:
            if mapped:
                pool.unmap_after_io(handle)

    def _stream(self, *args: WasmNumber) -> int:
        cmd, handle, offset, length = (int(value) & 0xFFFF_FFFF for value in args)
        assert cmd in (WasiIpcCmd.STREAM_READ_BUFFER, WasiIpcCmd.STREAM_WRITE_BUFFER)
        pool = self.platform.system.pool
        assert pool.map_for_io(handle) == HalBufferMapStatus.MAPPED
        try:
            key = ARG_MAX_LEN if cmd == WasiIpcCmd.STREAM_READ_BUFFER else ARG_LENGTH
            response = self.platform.engine.send_ipc_command(
                self.address,
                cmd,
                params(
                    (
                        (ARG_BUFFER_HANDLE, handle),
                        (ARG_OFFSET, offset),
                        (key, length),
                    )
                ),
            )
            assert response.response_code == 0
            return response.value
        finally:
            pool.unmap_after_io(handle)


@pytest.mark.parametrize("name", ENDPOINTS)
def test_clang_guest_reaches_each_stub_through_real_ipc(
    compiled_stub_guest: Path, name: str
) -> None:
    """TEST-HAL-25: Clang binary, QA import adapter, real IPC/task, actual device state."""
    drivers = StubDrivers.create()
    with stub_platform(drivers, (uri(name),)) as platform:
        client = GuestDriverClient(platform, uri(name), compiled_stub_guest)
        before = seed_slots(platform)
        if name in STREAMS:
            driver = stream_driver(drivers, name)
            if name == "adc":
                assert client.send(StubCommand.ADC_CONFIGURE, ((ARG_CLOCK_HZ, 48_000),)) == 0
                assert drivers.adc.configuration == ((ARG_CLOCK_HZ, 48_000),)
            driver.feed_input(b"\x00\xff\x81ADC\x7f")
            assert client.call("stream_probe", (WasiIpcCmd.STREAM_READ_BUFFER, 0, 17, 32)) == 7
            expected = bytearray(before[0])
            expected[17:24] = b"\x00\xff\x81ADC\x7f"
            assert slots(platform) == (bytes(expected), *before[1:])
            assert client.call("stream_probe", (WasiIpcCmd.STREAM_WRITE_BUFFER, 0, 17, 7)) == 7
            assert driver.drain_output() == b"\x00\xff\x81ADC\x7f"
        elif name == "pwm":
            assert client.send(StubCommand.PWM_CONFIGURE, ((ARG_CLOCK_HZ, 20_000),)) == 0
            assert client.send(StubCommand.PWM_WRITE, ((ARG_PIN_NO, 2), (ARG_VAL, 25))) == 0
            assert drivers.pwm.configuration == ((ARG_CLOCK_HZ, 20_000),)
            assert drivers.pwm.output == tuple(sorted(((ARG_PIN_NO, 2), (ARG_VAL, 25))))
            assert slots(platform) == before
        elif name == "gpio":
            assert client.send(WasiIpcCmd.GPIO_CONFIG_PIN, ((ARG_PIN_NO, 3), (ARG_MODE, 1))) == 0
            assert client.send(WasiIpcCmd.GPIO_SET_PIN, ((ARG_PIN_NO, 3), (ARG_VAL, 1))) == 0
            assert client.send(WasiIpcCmd.GPIO_GET_PIN, ((ARG_PIN_NO, 3),)) == 1
            assert drivers.gpio.modes == [None, None, None, 1, None, None, None, None]
            assert drivers.gpio.levels == [0, 0, 0, 1, 0, 0, 0, 0]
            assert slots(platform) == before
        elif name in ("i2c", "spi"):
            driver = drivers.i2c if name == "i2c" else drivers.spi
            assert (
                client.send(
                    WasiIpcCmd.BUS_CONFIG,
                    (
                        (ARG_CLOCK_HZ, 400_000),
                        (ARG_SLAVE_ADDR, 0x31),
                        (ARG_MODE, 3),
                    ),
                )
                == 0
            )
            driver.feed_reply(b"\xff\x00\x81bus")
            assert (
                client.send(
                    WasiIpcCmd.BUS_TRANSFER_BUFFER,
                    (
                        (ARG_TX_BUFFER_HANDLE, 0),
                        (ARG_RX_BUFFER_HANDLE, 0),
                        (ARG_LENGTH, 6),
                    ),
                )
                == 6
            )
            assert driver.configuration == (400_000, 0x31, 3)
            assert driver.transmitted == [b"\xa5" * 6] and driver.reply == b""
            assert slots(platform) == (b"\xff\x00\x81bus" + before[0][6:], *before[1:])
        else:
            drivers.timer.advance(0xFEDC_BA98_9ABC_DEF0)
            assert client.send(WasiIpcCmd.CLOCK_GET_NOW) == 0xFEDC_BA98_9ABC_DEF0
            assert client.send(WasiIpcCmd.CLOCK_GET_RES) == 1
            assert slots(platform) == before
        assert platform.system.pool._mapped_task_id is None
        selected = next(
            driver for address, _, driver in drivers.endpoints() if address == uri(name)
        )
        assert selected.observations
        assert all(item.task_id == selected.expected_task_id for item in selected.observations)


@pytest.mark.parametrize("name,addresses", (("i2c", (0x12, 0x73)), ("spi", (0, 1))))
def test_clang_bus_configuration_command_selects_subsequent_transfer_address(
    compiled_stub_guest: Path,
    name: str,
    addresses: tuple[int, int],
) -> None:
    """TEST-HAL-21/25: slave address is latched by BUS_CONFIG, never supplied as a pointer."""
    drivers = StubDrivers.create()
    driver = drivers.i2c if name == "i2c" else drivers.spi
    with stub_platform(drivers, (uri(name),)) as platform:
        client = GuestDriverClient(platform, uri(name), compiled_stub_guest)
        before = seed_slots(platform)
        for address, data in zip(addresses, (b"first", b"next!"), strict=True):
            assert (
                client.send(
                    WasiIpcCmd.BUS_CONFIG,
                    (
                        (ARG_CLOCK_HZ, 400_000),
                        (ARG_SLAVE_ADDR, address),
                        (ARG_MODE, 3),
                    ),
                )
                == 0
            )
            driver.feed_reply(data)
            assert (
                client.send(
                    WasiIpcCmd.BUS_TRANSFER_BUFFER,
                    (
                        (ARG_TX_BUFFER_HANDLE, 0),
                        (ARG_RX_BUFFER_HANDLE, 0),
                        (ARG_LENGTH, 5),
                    ),
                )
                == 5
            )
            assert slots(platform) == (data + before[0][5:], *before[1:])
        assert driver.transfer_configurations == [(400_000, address, 3) for address in addresses]
        assert driver.transmitted == [b"\xa5" * 5, b"first"]
        assert driver.reply == b"" and platform.system.pool._mapped_buffer_id is None
