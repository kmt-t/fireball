from __future__ import annotations

"""
Unit tests for Tier 3 Platform: HAL Drivers & HalBufferPool
Traceability: hal_dispatch_test_spec.md / platform_driver_test_spec.md
"""

import sys
import time
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from hal_dispatch import (
    FB_CONF_HAL_BUFFER_SIZE,
    FB_CONF_HAL_MAX_BUFFERS,
    HalBufferPool,
    HalBufferTrap,
    WasiIpcCmd,
    Timer,
    StreamTransport,
)
from dummy_drivers import DummyDriver
from scheduler import Scheduler
from system import (
    System,
)
from system_containers import (
    FlatMapView,
)
from vmmio import TrapCode, VMMIOController, VmmioStatus


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_hal_01_stream_transport_is_real_pipe():
    t = StreamTransport()
    try:
        assert t.write(b"fireball\n") == 9
        assert t.drain_output() == b"fireball\n"
        assert t.drain_output() == b""
    finally:
        t.close()


def test_hal_02_dummy_stdio_driver_streams_stdin_and_stdout():
    from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_MAX_LEN, ARG_OFFSET

    scheduler = Scheduler()
    task_id = scheduler.spawn("stdio_guest")
    scheduler.current_task = scheduler.get_task(task_id)
    assert scheduler.current_task is not None
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    pool.bind_guest()
    driver = DummyDriver()
    driver.bind_buffer_pool(pool)
    try:
        rx = pool.acquire_buffer(size=32)
        tx = pool.acquire_buffer(size=32)
        tx_view = pool.view(tx, 0, 10)
        tx_view[:] = b"out-1out-2"
        assert driver.feed_stdin(b"in-1in-2") == 8
        assert driver.dispatch(
            WasiIpcCmd.STREAM_READ_BUFFER,
            FlatMapView(
                [(ARG_BUFFER_HANDLE, rx.buffer_id), (ARG_OFFSET, 0), (ARG_MAX_LEN, 32)]
            ),
        ) == 8
        assert bytes(pool.view(rx, 0, 8)) == b"in-1in-2"
        assert driver.dispatch(
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            FlatMapView(
                [(ARG_BUFFER_HANDLE, tx.buffer_id), (ARG_OFFSET, 0), (ARG_LENGTH, 10)]
            ),
        ) == 10
        assert driver.drain_stdout() == b"out-1out-2"
    finally:
        pool.close_all()
        driver.transport.close()


def test_hal_03_timer_monotonic_ns():
    timer = Timer()
    t1 = timer.get_now_ns()
    time.sleep(0.001)
    t2 = timer.get_now_ns()
    assert t2 > t1


def test_hal_04_hal_buffer_pool_rejects_oversized():
    scheduler = Scheduler()
    task_id = scheduler.spawn("test_task")
    scheduler.current_task = scheduler.get_task(task_id)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    pool.bind_guest()
    try:
        try:
            pool.acquire_buffer(size=FB_CONF_HAL_BUFFER_SIZE + 1)
            raise AssertionError("expected ValueError for oversized acquire_buffer")
        except ValueError:
            pass
        handles = [pool.acquire_buffer(size=32) for _ in range(FB_CONF_HAL_MAX_BUFFERS)]
        assert len(handles) == FB_CONF_HAL_MAX_BUFFERS
    finally:
        pool.close_all()


def test_hal_05_hal_buffer_slice_bounds_and_ownership():
    scheduler = Scheduler()
    owner_id = scheduler.spawn("owner")
    other_id = scheduler.spawn("other")
    scheduler.current_task = scheduler.get_task(owner_id)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    pool.bind_guest()
    try:
        scheduler.current_task = scheduler.get_task(other_id)
        try:
            pool.bind_guest()
        except AssertionError as error:
            assert str(error) == "HAL DYNAMIC mapping supports one guest only"
        else:
            raise AssertionError("expected one-guest DYNAMIC mapping assertion")
        scheduler.current_task = scheduler.get_task(owner_id)
        h = pool.acquire_buffer(size=16)
        status, _ = vmmio.access(h.virtual_address, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL
        view = pool.view(h, 0, 16)
        assert len(view) == 16
        scheduler.current_task = scheduler.get_task(other_id)
        try:
            pool.view(h, 0, 16)
            raise AssertionError("expected HalBufferTrap: non-owner does not own handle")
        except HalBufferTrap:
            pass
        scheduler.current_task = scheduler.get_task(owner_id)
        pool.release_buffer(h)
        status, _ = vmmio.access(h.virtual_address, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
    finally:
        pool.close_all()


# ===========================================================================
# 5. Tier 2 Logging & Recovery (runtime_logging_test_spec.md)
# ===========================================================================


def test_hal_task_ipc_communication():
    """TEST-HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET
    from wasi import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_ipc_guest")
        sysv.scheduler.current_task = runtime_task
        sysv.pool.bind_guest()
        buffer_handle = sysv.pool.acquire_buffer(size=128)
        buffer_view = sysv.pool.view(buffer_handle, 0, 128)
        buffer_view[:] = b"x" * 128
        sysv.start_hal_driver(DummyDriver(transport=sysv.transport))
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        nwritten = engine.send_ipc_command(
            "fireball://device/uart/0",
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            FlatMapView(
                [(ARG_BUFFER_HANDLE, buffer_handle.buffer_id), (ARG_LENGTH, 128), (ARG_OFFSET, 0)]
            ),
        )
        assert nwritten == 128
        uart_task = sysv.hal_task_for("fireball://device/uart/0")
        assert uart_task.processed_count == 1
        assert uart_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_BUFFER
    finally:
        sysv.shutdown()


if __name__ == "__main__":
    test_hal_01_stream_transport_is_real_pipe()
    test_hal_02_dummy_stdio_driver_streams_stdin_and_stdout()
    test_hal_03_timer_monotonic_ns()
    test_hal_04_hal_buffer_pool_rejects_oversized()
    test_hal_05_hal_buffer_slice_bounds_and_ownership()
    test_hal_task_ipc_communication()
    print("[PASS] All 6 HAL Drivers & HalBufferPool tests passed.")
