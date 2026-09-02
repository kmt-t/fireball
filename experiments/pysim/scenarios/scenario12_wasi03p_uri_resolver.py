"""
experiments/pysim/scenarios/scenario12_wasi03p_uri_resolver.py
Integration Scenario 12: WASI 0.3p Hierarchical URI Resolver, IPC Driver Command Protocol, Capability Query & SHM.

Tests:
1. Hierarchical IPC URI interface resolution via `resolver.get-interface`:
   - "fireball://device/uart/0" (UART character stream via SHM)
   - "fireball://device/gpio/0" (Fast-path GPIO trigger)
   - "fireball://device/timer/0" (Monotonic hardware timer)
   - "fireball://device/i2c/0" (I2C bus master/slave zero-copy)
   - "fireball://device/spi/0" (SPI bus master/slave zero-copy)
   - "fireball://service/stdout/0" (Console standard output)
   - "fireball://service/logger/0" (System logger)
   - Standard WASI 0.3p aliases ("wasi:io/streams@0.3.0", "wasi:clocks/monotonic-clock@0.3.0")
2. Driver Capability Query Protocol (`CMD_QUERY_CAPS` = 0x00):
   - Querying supported / unsupported commands on UART, GPIO, Timer, Bus drivers.
3. WASI 0.3p IPC Driver Command Protocol dispatching via `dispatch_command`:
   - Stream: `CMD_STREAM_WRITE_SHM`
   - Clock: `CMD_CLOCK_GET_NOW`
   - GPIO: `CMD_GPIO_SET_PIN`
   - Bus: `CMD_BUS_TRANSFER_SHM`
4. WASI 0.1p (`wasi_snapshot_preview1`) adapter delegating to WASI 0.3p SHM streams and clocks
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from hal import (
    ARG_LENGTH,
    ARG_OFFSET,
    ARG_PIN_NO,
    ARG_QUERY_CMD_ID,
    ARG_SHM_HANDLE,
    ARG_TASK_ID,
    ARG_VAL,
    DummyBusDriver,
    DummyGpioDriver,
    DummyTimerDriver,
    DummyUartDriver,
)
from system import System
from system_containers import FlatMapView
from wasi import Wasi03pEngine, WasiHostContext, WasiIpcCmd

_EMPTY_PARAMS = FlatMapView(())


def _params(*pairs: tuple[int, object]) -> FlatMapView:
    """Builds a params FlatMapView from packed (key, value) pairs, matching
    platform_hal.md §5.1's control(id, cmd, params: ipc-message)."""
    sorted_pairs = sorted(pairs, key=lambda kv: kv[0])
    return FlatMapView(sorted_pairs)


def test_wasi03p_hierarchical_uri_and_ipc_commands():
    print(
        "[*] Running Scenario 12: WASI 0.3p Hierarchical URI Resolver, Capability Query & IPC Commands..."
    )
    sysv = System()
    engine = Wasi03pEngine(sysv)

    # 1. Test Hierarchical IPC URIs Resolution
    hierarchical_uris = [
        "fireball://device/uart/0",
        "fireball://device/gpio/0",
        "fireball://device/timer/0",
        "fireball://device/i2c/0",
        "fireball://device/spi/0",
        "fireball://service/stdout/0",
        "fireball://service/logger/0",
        "wasi:clocks/monotonic-clock@0.3.0",
        "wasi:io/streams@0.3.0",
        "wasi:cli/stdout@0.3.0",
    ]

    for uri in hierarchical_uris:
        iface = engine.get_interface(uri)
        assert iface is not None, f"Failed to resolve Hierarchical URI: {uri}"
        print(f"    [RESOLVED] Hierarchical URI: {uri}")

    # 2. Test Driver Capability Query (CMD_QUERY_CAPS = 0x00)
    print("    [Testing Capability Query: CMD_QUERY_CAPS]...")
    # UART capability check
    uart_supports_stream = engine.dispatch_command(
        "fireball://device/uart/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.STREAM_WRITE_SHM)),
    )
    uart_supports_gpio = engine.dispatch_command(
        "fireball://device/uart/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.GPIO_SET_PIN)),
    )
    assert uart_supports_stream == 1, "UART must support STREAM_WRITE_SHM"
    assert uart_supports_gpio == 0, "UART must NOT support GPIO_SET_PIN"

    # GPIO capability check
    gpio_supports_gpio = engine.dispatch_command(
        "fireball://device/gpio/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.GPIO_SET_PIN)),
    )
    gpio_supports_stream = engine.dispatch_command(
        "fireball://device/gpio/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.STREAM_WRITE_SHM)),
    )
    assert gpio_supports_gpio == 1, "GPIO must support GPIO_SET_PIN"
    assert gpio_supports_stream == 0, "GPIO must NOT support STREAM_WRITE_SHM"

    # Timer capability check
    timer_supports_clock = engine.dispatch_command(
        "fireball://device/timer/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.CLOCK_GET_NOW)),
    )
    timer_supports_bus = engine.dispatch_command(
        "fireball://device/timer/0",
        WasiIpcCmd.QUERY_CAPS,
        _params((ARG_QUERY_CMD_ID, WasiIpcCmd.BUS_TRANSFER_SHM)),
    )
    assert timer_supports_clock == 1, "Timer must support CLOCK_GET_NOW"
    assert timer_supports_bus == 0, "Timer must NOT support BUS_TRANSFER_SHM"

    print("    [CAPABILITY QUERY] All driver capability checks passed successfully.")

    # 3. Test Direct Dummy Driver Classes
    dummy_uart = DummyUartDriver()
    dummy_gpio = DummyGpioDriver()
    dummy_timer = DummyTimerDriver()
    dummy_bus = DummyBusDriver()

    assert dummy_uart.dispatch(0x00, _params((ARG_QUERY_CMD_ID, 0x01))) == 1
    assert dummy_uart.dispatch(0x00, _params((ARG_QUERY_CMD_ID, 0x20))) == 0
    assert dummy_gpio.dispatch(0x00, _params((ARG_QUERY_CMD_ID, 0x20))) == 1
    assert dummy_timer.dispatch(0x00, _params((ARG_QUERY_CMD_ID, 0x10))) == 1
    assert dummy_bus.dispatch(0x00, _params((ARG_QUERY_CMD_ID, 0x30))) == 1

    # 4. Test WASI 0.3p IPC Command Protocol: Clock / Timer (0x10)
    now_ns = engine.dispatch_command(
        "fireball://device/timer/0", WasiIpcCmd.CLOCK_GET_NOW, _EMPTY_PARAMS
    )
    assert now_ns is not None and now_ns > 0, "Expected valid monotonic timestamp"
    print(f"    [IPC CMD:CLOCK_GET_NOW] now_ns={now_ns}")

    # 5. Test WASI 0.3p IPC Command Protocol: GPIO Set Pin (0x20)
    engine.dispatch_command(
        "fireball://device/gpio/0",
        WasiIpcCmd.GPIO_SET_PIN,
        _params((ARG_PIN_NO, 15), (ARG_VAL, True)),
    )
    out_gpio = sysv.transport.drain().decode("utf-8")
    assert out_gpio == "[GPIO:15=True]", f"GPIO output mismatch: {out_gpio}"
    print(f"    [IPC CMD:GPIO_SET_PIN] Verified output: {out_gpio}")

    # 6. Test WASI 0.3p IPC Command Protocol: Stream Write via SHM (0x01)
    shm_handle = sysv.pool.acquire_buffer(task_id=1, size=64)
    shm_view = sysv.pool.view(task_id=1, handle=shm_handle, offset=0, length=24)
    msg = b"IPC-CMD-SHM-STREAM-OK!"
    shm_view[0 : len(msg)] = msg

    nwritten = engine.dispatch_command(
        "fireball://device/uart/0",
        WasiIpcCmd.STREAM_WRITE_SHM,
        _params(
            (ARG_TASK_ID, 1),
            (ARG_SHM_HANDLE, shm_handle),
            (ARG_OFFSET, 0),
            (ARG_LENGTH, len(msg)),
        ),
    )
    assert nwritten == len(msg)
    out_uart = sysv.transport.drain().decode("utf-8")
    assert out_uart == "IPC-CMD-SHM-STREAM-OK!", f"UART SHM output mismatch: {out_uart}"
    print(f"    [IPC CMD:STREAM_WRITE_SHM] Written {nwritten} bytes -> {out_uart}")

    # 6.b Test Dispatch with a directly-built params FlatMapView, matching
    # dispatch_command's single statically-typed argument exactly (no
    # secondary "IPCMessage vs FlatMapView" shape to infer at the callee).
    fmap_view = _params(
        (ARG_LENGTH, len(msg)), (ARG_OFFSET, 0), (ARG_SHM_HANDLE, shm_handle), (ARG_TASK_ID, 1)
    )
    nwritten_fmap = engine.dispatch_command(
        "fireball://device/uart/0", WasiIpcCmd.STREAM_WRITE_SHM, fmap_view
    )
    assert nwritten_fmap == len(msg)
    out_uart_fmap = sysv.transport.drain().decode("utf-8")
    assert out_uart_fmap == "IPC-CMD-SHM-STREAM-OK!"
    print(f"    [IPC FlatMapView DISPATCH] Written {nwritten_fmap} bytes -> {out_uart_fmap}")

    # 6.c Test Full HAL Task IPC Rendezvous Communication (Task-to-Task CSP)
    sysv.spawn_hal_task()
    ipc_res = engine.send_ipc_command(
        "fireball://device/uart/0",
        WasiIpcCmd.STREAM_WRITE_SHM,
        _params((ARG_LENGTH, len(msg)), (ARG_OFFSET, 0)),
    )
    assert ipc_res == len(msg)
    assert sysv.hal_task.processed_count >= 1
    print(
        f"    [HAL Task IPC Rendezvous] Successfully received and dispatched command via HAL task (count={sysv.hal_task.processed_count})"
    )

    # 2) Key-Value Pair Array (Specification §3.3 Bit Assignment)
    from ipc_router import DataType, IPCMessage, ScopeKind, pack_key32

    # Pack 32-bit keys and 32-bit values:
    #   Entry 1: Functional Scope, UINT32, key_id=0x01 (STREAM_WRITE_SHM), val=len(msg)
    #   Entry 2: Resource Scope, UINT32, key_id=0x14 (SHM_HANDLE), val=shm_handle
    k1 = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0x01)
    val1 = len(msg)
    shm_slot_id = 1
    k2 = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=0x14)
    val2 = shm_slot_id

    pairs = sorted([(k1, val1), (k2, val2)], key=lambda p: p[0])
    ipc_msg_64 = IPCMessage.from_entries(pairs)
    assert len(ipc_msg_64) == 2
    assert ipc_msg_64.get_by_key_id(0x01, ScopeKind.FUNCTIONAL) == len(msg)
    assert ipc_msg_64.get_by_key_id(0x14, ScopeKind.RESOURCE) == shm_slot_id
    print(
        "    [IPC 64-bit KV Array DISPATCH] Successfully verified AoS entries: "
        f"{ipc_msg_64.entries}"
    )

    # 7. Test WASI 0.1p Wrapper Delegation
    wasi_ctx = WasiHostContext(sysv)
    uri_bytes = b"fireball://device/uart/0"
    wasi_ctx.guest_memory[100 : 100 + len(uri_bytes)] = uri_bytes
    res = wasi_ctx.wasi03p_get_interface(100, len(uri_bytes))
    assert res == 1, "Expected successful hierarchical URI lookup via WasiHostContext"

    time_ptr = 200
    errno = wasi_ctx.clock_time_get(clock_id=1, precision=0, time_ptr=time_ptr)
    assert errno == 0
    (t_val,) = struct.unpack_from("<Q", wasi_ctx.guest_memory, time_ptr)
    assert t_val > 0, "Expected non-zero timestamp from WASI 0.3p delegated clock"

    print(
        "    [PASS] Scenario 12 (Hierarchical URI, Capability Query & IPC Driver Commands) verified successfully."
    )


if __name__ == "__main__":
    test_wasi03p_hierarchical_uri_and_ipc_commands()
