from __future__ import annotations

"""
Unit tests for Tier 3 Platform: HAL Drivers & ShmPool
Traceability: platform_hal_test_spec.md
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
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from hal import (
    FB_CONF_HAL_BUFFER_SIZE,
    FB_CONF_HAL_MAX_BUFFERS,
    ShmBufferPool,
    ShmTrap,
    Timer,
    UartTransport,
)
from system import (
    System,
)
from system_containers import (
    FlatMapView,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_hal_01_uart_transport_is_real_pipe():
    t = UartTransport()
    try:
        assert t.write(b"fireball\n") == 9
        assert t.drain() == b"fireball\n"
        assert t.drain() == b""
    finally:
        t.close()


def test_hal_02_timer_monotonic_ns():
    timer = Timer()
    t1 = timer.get_now_ns()
    time.sleep(0.001)
    t2 = timer.get_now_ns()
    assert t2 > t1


def test_hal_03_shm_pool_rejects_oversized():
    pool = ShmBufferPool()
    try:
        try:
            pool.acquire_buffer(1, size=FB_CONF_HAL_BUFFER_SIZE + 1)
            raise AssertionError("expected ValueError for oversized acquire_buffer")
        except ValueError:
            pass
        handles = [pool.acquire_buffer(1, size=32) for _ in range(FB_CONF_HAL_MAX_BUFFERS)]
        assert len(handles) == FB_CONF_HAL_MAX_BUFFERS
    finally:
        pool.close_all()


def test_hal_04_shm_slice_bounds_and_ownership():
    pool = ShmBufferPool()
    try:
        h = pool.acquire_buffer(task_id=1, size=16)
        view = pool.view(1, h, 0, 16)
        assert len(view) == 16
        try:
            pool.view(2, h, 0, 16)
            raise AssertionError("expected ShmTrap: task 2 does not own handle")
        except ShmTrap:
            pass
    finally:
        pool.close_all()


# ===========================================================================
# 5. Tier 1 Logging & Recovery (system_logging_test_spec.md)
# ===========================================================================


def test_hal_task_ipc_communication():
    """HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from hal import ARG_LENGTH, ARG_OFFSET
    from wasi import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        sysv.spawn_hal_task()
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        nwritten = engine.send_ipc_command(
            "fireball://device/uart/0",
            WasiIpcCmd.STREAM_WRITE_SHM,
            FlatMapView([(ARG_LENGTH, 128), (ARG_OFFSET, 0)]),
        )
        assert nwritten == 128
        assert sysv.hal_task.processed_count == 1
        assert sysv.hal_task.last_handled_uri == "fireball://device/uart/0"
        assert sysv.hal_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_SHM
    finally:
        sysv.shutdown()


if __name__ == "__main__":
    test_hal_01_uart_transport_is_real_pipe()
    test_hal_02_timer_monotonic_ns()
    test_hal_03_shm_pool_rejects_oversized()
    test_hal_04_shm_slice_bounds_and_ownership()
    test_hal_task_ipc_communication()
    print("[PASS] All 5 HAL Drivers & ShmPool tests passed.")
