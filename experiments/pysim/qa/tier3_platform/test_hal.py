from __future__ import annotations

"""
Unit tests for Tier 3 Platform: HAL Drivers & HalBufferPool
Traceability: hal_dispatch_test_spec.md / platform_driver_test_spec.md
"""

import io
import time
from pathlib import Path

import pytest

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from ipc_router import FB_URI_HAL_STDOUT
from qa.shared.fixtures.uvwasi_reference import UvwasiReferenceContext, WasiErrno, WasiWhence
from qa.shared.helpers import expect_assertion
from scheduler import ChannelAction, Scheduler
from system import (
    System,
)
from system_containers import (
    ReadOnlyFlatMapView,
)
from tier2_runtime.hal.dispatch import (
    FB_CONF_HAL_BUFFER_SIZE,
    FB_CONF_HAL_MAX_BUFFERS,
    HalBufferMapStatus,
    HalBufferPool,
    WasiIpcCmd,
)
from tier2_runtime.observability.logger import (
    LogDictionary,
    LogLevel,
    LogResult,
    decode_log_records,
)
from tier2_runtime.vmmio.controller import TrapCode, VMMIOController, VmmioStatus
from tier3_platform.drivers.hal.dummy import DummyDriver, Timer
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.logging.file_sink import FileLogSink


def test_hal_01_stream_transport_uses_fixed_buffers():
    t = StreamTransport()
    try:
        assert t.write(b"fireball\n") == 9
        assert t.drain_output() == b"fireball\n"
        assert t.drain_output() == b""
    finally:
        t.close()


def test_hal_02_dummy_stdio_driver_streams_stdin_and_stdout():
    from tier2_runtime.hal.dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_MAX_LEN, ARG_OFFSET

    scheduler = Scheduler()
    task_id = scheduler.spawn("stdio_guest")
    scheduler.current_task = scheduler.get_task(task_id)
    assert scheduler.current_task is not None
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    driver = DummyDriver()
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


def test_hal_15_timer_pollables_are_deadline_checked_and_reusable():
    sysv = System()
    timer_uri = "fireball://hal/timer/0"
    try:
        runtime_task = sysv.start_runtime_task(name="timer_poll_guest")
        sysv.scheduler.current_task = runtime_task
        timer_driver = DummyDriver()
        sysv.start_hal_driver(timer_driver, timer_uri)
        from tier3_platform.drivers.wasi.context import Wasi03pEngine

        engine = Wasi03pEngine(sysv)
        handle = engine.clock_subscribe(timer_uri, 100_000_000)
        assert handle != 0
        assert engine.poll_check(timer_uri, handle) is False
        peer_ran_ns: list[int] = []

        def ready_peer():
            yield (ChannelAction.YIELD, None)
            yield (ChannelAction.YIELD, None)
            peer_ran_ns.append(time.monotonic_ns())
            yield (ChannelAction.YIELD, None)

        sysv.scheduler.spawn("timer_wait_peer", ready_peer(), role=runtime_task.role)
        deadline_ns = timer_driver.timer.wakeup_ns(handle)
        assert engine.poll_wait(timer_uri, handle) is True
        assert len(peer_ran_ns) == 1 and peer_ran_ns[0] < deadline_ns
        assert engine.poll_check(timer_uri, handle) is True
        assert engine.poll_drop(timer_uri, handle) == 0
        replacement = engine.clock_subscribe(timer_uri, 0)
        assert replacement != handle
        with expect_assertion("stale HAL timer pollable handle"):
            engine.poll_check(timer_uri, handle)
    finally:
        sysv.shutdown()


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
        status, _ = vmmio.access(h.virtual_address, is_write=False)
        assert status == VmmioStatus.OWNER_MISMATCH
        assert not pool.can_view(h, 0, 16)
        with expect_assertion("DYNAMIC buffer is not mapped for this guest"):
            pool.view(h, 0, 16)
        # The HAL endpoint uses the validated fixed-pool backing, while guest
        # vMMIO access remains restricted to the mapped guest owner.
        driver_view = pool.view_for_driver(h.buffer_id, 0, 4)
        driver_view[:] = b"HAL!"
        scheduler.current_task = scheduler.get_task(owner_id)
        assert pool.view(h, 0, 4).tobytes() == b"HAL!"
        pool.unmap_after_io(h.buffer_id)
        assert not pool.can_view(h, 0, 16)
        assert pool.map_for_io(h.buffer_id) == HalBufferMapStatus.MAPPED
        pool.unmap_after_io(h.buffer_id)
        status, _ = vmmio.access(h.virtual_address, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
    finally:
        pool.close_all()


@pytest.mark.parametrize(
    "rejection",
    (
        "other-slot",
        "negative-id",
        "past-id",
        "negative-offset",
        "negative-length",
        "past-offset",
        "past-end",
        "oversized-length",
        "unmapped-handle",
        "wrong-unmap",
    ),
)
def test_hal_06_rejection_preserves_mapping_and_all_slot_bytes(rejection: str) -> None:
    """TEST-HAL-06 / GOTCHA-HAL-01: Reject bad I/O without corrupting the live or adjacent slots."""
    scheduler = Scheduler()
    owner_id = scheduler.spawn("owner")
    scheduler.current_task = scheduler.get_task(owner_id)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    handles = tuple(pool.buffer(index) for index in range(FB_CONF_HAL_MAX_BUFFERS))
    try:
        for index, handle in enumerate(handles):
            assert pool.map_for_io(handle.buffer_id) == HalBufferMapStatus.MAPPED
            pool.view(handle, 0, handle.capacity)[:] = bytes(
                (offset + index * 29) % 256 for offset in range(handle.capacity)
            )
            pool.unmap_after_io(handle.buffer_id)
        before = tuple(bytes(handle._storage) for handle in handles)
        current, other = handles[:2]
        assert pool.map_for_io(current.buffer_id) == HalBufferMapStatus.MAPPED

        def assert_preserved() -> None:
            assert tuple(bytes(handle._storage) for handle in handles) == before
            assert pool._mapped_buffer_id == current.buffer_id
            assert pool._mapped_task_id == owner_id
            assert pool.can_view(current, 0, current.capacity)
            assert pool.view(current, 0, current.capacity).tobytes() == before[0]
            for handle in handles:
                status, _ = vmmio.access(handle.virtual_address, is_write=False)
                expected = (
                    VmmioStatus.OK_PHYSICAL if handle is current else TrapCode.UNREGISTERED_PAGE
                )
                assert status == expected

        if rejection == "other-slot":
            assert pool.map_for_io(other.buffer_id) == HalBufferMapStatus.BUSY
        elif rejection in ("negative-id", "past-id"):
            invalid_id = -1 if rejection == "negative-id" else FB_CONF_HAL_MAX_BUFFERS
            with expect_assertion():
                pool.map_for_io(invalid_id)
            assert_preserved()
            with expect_assertion():
                pool.buffer(invalid_id)
            assert_preserved()
            with expect_assertion():
                pool.view_for_driver(invalid_id, 0, 1)
        elif rejection == "unmapped-handle":
            assert not pool.can_view(other, 0, 1)
            with expect_assertion("not mapped for this operation"):
                pool.view(other, 0, 1)
        elif rejection == "wrong-unmap":
            with expect_assertion("mapping does not match"):
                pool.unmap_after_io(other.buffer_id)
        else:
            invalid_ranges = {
                "negative-offset": (-1, 1),
                "negative-length": (0, -1),
                "past-offset": (current.capacity + 1, 0),
                "past-end": (current.capacity, 1),
                "oversized-length": (0, current.capacity + 1),
            }
            offset, length = invalid_ranges[rejection]
            assert not pool.can_view(current, offset, length)
            with expect_assertion("escapes fixed buffer"):
                pool.view(current, offset, length)
            assert_preserved()
            with expect_assertion():
                pool.view_for_driver(current.buffer_id, offset, length)
        assert_preserved()

        # A valid edge slice must still reach the same live HAL backing after rejection.
        pool.view(current, current.capacity - 3, 3)[:] = b"HAL"
        assert pool.view_for_driver(current.buffer_id, current.capacity - 3, 3).tobytes() == b"HAL"
        assert tuple(bytes(handle._storage) for handle in handles) == (
            before[0][:-3] + b"HAL",
            *before[1:],
        )
        pool.unmap_after_io(current.buffer_id)
        status, _ = vmmio.access(current.virtual_address, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
        assert pool.map_for_io(other.buffer_id) == HalBufferMapStatus.MAPPED
        assert pool.view(other, 0, other.capacity).tobytes() == before[1]
        pool.unmap_after_io(other.buffer_id)
        status, _ = vmmio.access(other.virtual_address, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
    finally:
        pool.close_all()


# ===========================================================================
# 5. Tier 2 Logging & Recovery (runtime_logging_test_spec.md)
# ===========================================================================


@pytest.mark.parametrize(
    "offset, payload",
    [
        pytest.param(0, b"x" * 128, id="offset-zero"),
        pytest.param(17, bytes(range(32)), id="offset-17"),
    ],
)
def test_hal_task_ipc_communication(offset: int, payload: bytes):
    """TEST-HAL-01/02/16: IPC stream writes deliver exactly the selected fixed-buffer slice."""
    from tier2_runtime.hal.dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET
    from tier3_platform.drivers.wasi.context import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_ipc_guest")
        sysv.scheduler.current_task = runtime_task
        buffer_handle = sysv.pool.buffer(0)
        assert sysv.pool.map_for_io(buffer_handle.buffer_id) == HalBufferMapStatus.MAPPED
        buffer_view = sysv.pool.view(buffer_handle, 0, buffer_handle.capacity)
        buffer_view[:] = b"\xa5" * buffer_handle.capacity
        buffer_view[offset : offset + len(payload)] = payload
        original_buffer = bytes(buffer_view)
        driver = DummyDriver(transport=sysv.transport)
        sysv.start_hal_driver(driver, FB_URI_HAL_STDOUT)
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        response = engine.send_ipc_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            ReadOnlyFlatMapView(
                [
                    (ARG_BUFFER_HANDLE, buffer_handle.buffer_id),
                    (ARG_LENGTH, len(payload)),
                    (ARG_OFFSET, offset),
                ]
            ),
        )
        assert response.response_code == 0
        assert response.value == len(payload)
        stdio_task = sysv.hal_task_for("fireball://hal/stdout/0")
        assert stdio_task is not None
        assert stdio_task.processed_count == 1
        assert stdio_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_BUFFER
        assert stdio_task.driver is driver
        assert driver.drain_stdout() == payload
        assert driver.drain_stdout() == b"", "one IPC command must not duplicate output"
        assert bytes(buffer_view) == original_buffer, "stream-write must preserve its source slot"
        sysv.pool.unmap_after_io(buffer_handle.buffer_id)
        assert not sysv.pool.can_view(buffer_handle, offset, len(payload))
        status, _ = sysv.vmmio.access(buffer_handle.virtual_address, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
    finally:
        sysv.shutdown()


def test_hal_command_response_separates_status_and_u64_value():
    """A 64-bit HAL value must not be stored in the u32 response status."""
    from tier3_platform.drivers.wasi.context import Wasi03pEngine

    sysv = System()
    try:
        runtime_task = sysv.start_runtime_task(name="hal_clock_guest")
        sysv.scheduler.current_task = runtime_task
        sysv.start_hal_driver(DummyDriver(transport=sysv.transport), FB_URI_HAL_STDOUT)
        engine = Wasi03pEngine(sysv)

        response = engine.send_ipc_command(
            "fireball://hal/stdout/0",
            WasiIpcCmd.CLOCK_GET_NOW,
            ReadOnlyFlatMapView(()),
        )

        assert response.response_code == 0
        assert response.value > 0xFFFF_FFFF
        assert (
            engine.dispatch_command(
                "fireball://hal/stdout/0",
                WasiIpcCmd.CLOCK_GET_NOW,
                ReadOnlyFlatMapView(()),
            )
            > 0xFFFF_FFFF
        )
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
    log_dictionary = LogDictionary(entries=((0x300, "TEST_LOG: v=%d"),))
    sysv = System(printk_sink=sink, log_dictionary=log_dictionary)
    try:
        sysv.start_hal_driver(DummyDriver(transport=sysv.transport), FB_URI_HAL_STDOUT)

        assert sysv.logger.log_event(LogLevel.INFO, 0x300, 7) == LogResult.SUCCESS
        assert sysv.logger.flush() == 1
        assert sysv.transport.write(memoryview(b"guest-out\n")) == 10

        records = decode_log_records(backing.getvalue(), sysv.dictionary)
        assert len(records) == 1
        assert records[0] == "[INFO] TEST_LOG: v=7"
        assert sink.bytes_written == len(backing.getvalue())
        assert sysv.transport.drain_output() == b"guest-out\n"
    finally:
        sysv.shutdown()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
