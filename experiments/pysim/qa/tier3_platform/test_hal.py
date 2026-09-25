from __future__ import annotations

"""
Unit tests for Tier 3 Platform: HAL Drivers & HalBufferPool
Traceability: hal_dispatch_test_spec.md / platform_driver_test_spec.md
"""

import io
import time
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from fixtures.uvwasi_reference import UvwasiReferenceContext, WasiErrno, WasiWhence
from hal_dispatch import (
    FB_CONF_HAL_BUFFER_SIZE,
    FB_CONF_HAL_MAX_BUFFERS,
    HalBufferMapStatus,
    HalBufferPool,
    WasiIpcCmd,
)
from helpers import expect_assertion
from ipc_router import FB_URI_HAL_STDOUT
from scheduler import Scheduler
from system import (
    System,
)
from system_containers import (
    ReadOnlyFlatMapView,
)
from tier2_runtime.logger import LogLevel
from tier3_platform.drivers.hal.dummy import DummyDriver, Timer
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.logging.file_sink import FileLogSink
from vmmio import TrapCode, VMMIOController, VmmioStatus


def test_hal_01_stream_transport_uses_fixed_buffers():
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
    driver = DummyDriver(FB_URI_HAL_STDOUT)
    driver.bind_buffer_pool(pool)
    try:
        rx = pool.buffer(0)
        tx = pool.buffer(1)
        assert pool.map_for_io(tx.buffer_id) == HalBufferMapStatus.MAPPED
        tx_view = pool.view(tx, 0, 10)
        tx_view[:] = b"out-1out-2"
        pool.unmap_after_io(tx.buffer_id)
        assert driver.is_supported(WasiIpcCmd.STREAM_WRITE_BUFFER) == 1
        assert driver.is_supported(0xFFFF) == 0
        assert driver.feed_stdin(b"in-1in-2") == 8
        assert pool.map_for_io(rx.buffer_id) == HalBufferMapStatus.MAPPED
        assert (
            driver.dispatch(
                WasiIpcCmd.STREAM_READ_BUFFER,
                ReadOnlyFlatMapView(
                    [(ARG_BUFFER_HANDLE, rx.buffer_id), (ARG_OFFSET, 0), (ARG_MAX_LEN, 32)]
                ),
            )
            == 8
        )
        assert bytes(pool.view(rx, 0, 8)) == b"in-1in-2"
        pool.unmap_after_io(rx.buffer_id)
        assert pool.map_for_io(tx.buffer_id) == HalBufferMapStatus.MAPPED
        assert (
            driver.dispatch(
                WasiIpcCmd.STREAM_WRITE_BUFFER,
                ReadOnlyFlatMapView(
                    [(ARG_BUFFER_HANDLE, tx.buffer_id), (ARG_OFFSET, 0), (ARG_LENGTH, 10)]
                ),
            )
            == 10
        )
        assert driver.drain_stdout() == b"out-1out-2"
        pool.unmap_after_io(tx.buffer_id)
    finally:
        pool.close_all()
        driver.transport.close()


def test_hal_03_timer_monotonic_ns():
    timer = Timer()
    t1 = timer.get_now_ns()
    time.sleep(0.001)
    t2 = timer.get_now_ns()
    assert t2 > t1


def test_hal_04_hal_buffer_pool_maps_fixed_slots():
    scheduler = Scheduler()
    task_id = scheduler.spawn("test_task")
    scheduler.current_task = scheduler.get_task(task_id)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    try:
        handles = [pool.buffer(i) for i in range(FB_CONF_HAL_MAX_BUFFERS)]
        assert len(handles) == FB_CONF_HAL_MAX_BUFFERS
        for handle in handles:
            assert handle.capacity == FB_CONF_HAL_BUFFER_SIZE
    finally:
        pool.close_all()


def test_hal_05_hal_buffer_slice_bounds_and_guest_mapping():
    scheduler = Scheduler()
    owner_id = scheduler.spawn("owner")
    other_id = scheduler.spawn("other")
    scheduler.current_task = scheduler.get_task(owner_id)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    try:
        scheduler.current_task = scheduler.get_task(other_id)
        scheduler.current_task = scheduler.get_task(owner_id)
        h = pool.buffer(0)
        assert pool.map_for_io(h.buffer_id) == HalBufferMapStatus.MAPPED
        status, _ = vmmio.access(h.virtual_address, is_write=False)
        assert status == VmmioStatus.OK_PHYSICAL
        view = pool.view(h, 0, 16)
        assert len(view) == 16
        assert pool.can_view(h, 0, 16)
        scheduler.current_task = scheduler.get_task(other_id)
        assert pool.map_for_io(h.buffer_id) == HalBufferMapStatus.BUSY
        assert not pool.can_view(h, 0, 16)
        with expect_assertion("DYNAMIC buffer is not mapped for this guest"):
            pool.view(h, 0, 16)
        scheduler.current_task = scheduler.get_task(owner_id)
        pool.unmap_after_io(h.buffer_id)
        assert not pool.can_view(h, 0, 16)
        assert pool.map_for_io(h.buffer_id) == HalBufferMapStatus.MAPPED
        pool.unmap_after_io(h.buffer_id)
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
    from tier3_platform.drivers.wasi.context import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_ipc_guest")
        sysv.scheduler.current_task = runtime_task
        buffer_handle = sysv.pool.buffer(0)
        assert sysv.pool.map_for_io(buffer_handle.buffer_id) == HalBufferMapStatus.MAPPED
        buffer_view = sysv.pool.view(buffer_handle, 0, 128)
        buffer_view[:] = b"x" * 128
        sysv.start_hal_driver(
            DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
        )
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        response = engine.send_ipc_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            ReadOnlyFlatMapView(
                [(ARG_BUFFER_HANDLE, buffer_handle.buffer_id), (ARG_LENGTH, 128), (ARG_OFFSET, 0)]
            ),
        )
        assert response.response_code == 0
        assert response.value == 128
        stdio_task = sysv.hal_task_for("fireball://hal/stdout/0")
        assert stdio_task is not None
        assert stdio_task.processed_count == 1
        assert stdio_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_BUFFER
        sysv.pool.unmap_after_io(buffer_handle.buffer_id)
    finally:
        sysv.shutdown()


def test_hal_command_response_separates_status_and_u64_value():
    """A 64-bit HAL value must not be stored in the u32 response status."""
    from tier3_platform.drivers.wasi.context import Wasi03pEngine

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_clock_guest")
        sysv.scheduler.current_task = runtime_task
        sysv.start_hal_driver(DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport))
        engine = Wasi03pEngine(sysv)

        response = engine.send_ipc_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.CLOCK_GET_NOW,
            ReadOnlyFlatMapView(()),
        )

        assert response.response_code == 0
        assert response.value > 0xFFFF_FFFF
        assert engine.dispatch_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.CLOCK_GET_NOW,
            ReadOnlyFlatMapView(()),
        ) > 0xFFFF_FFFF
    finally:
        sysv.shutdown()


def test_wasi_dummy_fd_write_updates_stdout_and_count():
    context = UvwasiReferenceContext()
    memory = bytearray(96)
    payload = b"out"
    memory[32 : 32 + len(payload)] = payload
    memory[0:8] = (32).to_bytes(4, "little") + len(payload).to_bytes(4, "little")

    assert context.fd_write(1, memory, 0, 1, 64) == WasiErrno.SUCCESS
    assert bytes(context.stdout_buffer) == payload
    assert int.from_bytes(memory[64:68], "little") == len(payload)


def test_wasi_dummy_fd_seek_writes_new_offset():
    context = UvwasiReferenceContext()
    memory = bytearray(32)

    assert context.fd_seek(3, 4, WasiWhence.SET, memory, 8) == WasiErrno.SUCCESS
    assert int.from_bytes(memory[8:16], "little") == 4


def test_wasi_dummy_clock_time_get_writes_timestamp():
    context = UvwasiReferenceContext()
    memory = bytearray(16)

    assert context.clock_time_get(1, 0, memory, 0) == WasiErrno.SUCCESS
    assert int.from_bytes(memory[0:8], "little") > 0


def test_hal_15_file_log_sink_receives_internal_logs():
    """System logs go to the injected file sink, never to guest stdout."""
    backing = io.BytesIO()
    sink = FileLogSink(backing)
    sysv = System(logger_sink=sink)
    try:
        sysv.start_hal_driver(
            DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
        )

        sysv.dictionary.register(0x300, "TEST_LOG: v=%d")
        assert sysv.logger.log_event(LogLevel.INFO, 0x300, 7) == "QUEUED"
        assert sysv.logger.flush() == 1
        assert sysv.transport.write(memoryview(b"guest-out\n")) == 10

        assert backing.getvalue().endswith(b"TEST_LOG: v=7\n")
        assert sink.bytes_written == len(backing.getvalue())
        assert sysv.transport.drain_output() == b"guest-out\n"
    finally:
        sysv.shutdown()


if __name__ == "__main__":
    test_hal_01_stream_transport_uses_fixed_buffers()
    test_hal_02_dummy_stdio_driver_streams_stdin_and_stdout()
    test_hal_03_timer_monotonic_ns()
    test_hal_04_hal_buffer_pool_maps_fixed_slots()
    test_hal_05_hal_buffer_slice_bounds_and_guest_mapping()
    test_hal_task_ipc_communication()
    test_wasi_dummy_fd_write_updates_stdout_and_count()
    test_wasi_dummy_fd_seek_writes_new_offset()
    test_wasi_dummy_clock_time_get_writes_timestamp()
    test_hal_15_file_log_sink_receives_internal_logs()
    print("[PASS] All 10 HAL Drivers & HalBufferPool tests passed.")
