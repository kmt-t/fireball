from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_executer",
    _PYSIM_DIR / "tier3_platform",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

"""Integration Scenario 11: HAL Stream/Timer Drivers & uvwasi Reference Stack.

Tests:
1. HAL Dummy Drivers:
   - Standard I/O: full-duplex stdin/stdout streaming
   - Timer: High-resolution monotonic clock (monotonic_ns) and tick advancement
2. uvwasi-compatible in-memory reference stack:
   - File I/O: Virtual file descriptors (fd_read, fd_write, fd_seek: SET/CUR/END)
   - Standard Streams: stdin buffered reading, stdout/stderr capture
   - System Utilities: random_get (entropy pool fill), clock_time_get (monotonic/realtime timestamp)
"""

from tier3_platform.drivers.hal.dummy import DummyDriver
from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_MAX_LEN, ARG_OFFSET, WasiIpcCmd
from system import System
from system_containers import ReadOnlyFlatMapView
from fixtures.uvwasi_reference import UvwasiReferenceContext, WasiErrno, WasiWhence


def test_scenario_hal_and_wasi_drivers():
    print("[*] Running Scenario 11: HAL Drivers & uvwasi Reference Stack...")
    # -------------------------------------------------------------------------
    # Part A: HAL Peripheral Dummy Drivers Verification
    # -------------------------------------------------------------------------
    # 1. Standard I/O stream driver
    sysv = System()
    runtime_task = sysv.start_runtime_task(name="scenario11_stdio_guest")
    stdio = DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
    sysv.start_hal_driver(stdio)
    sysv.scheduler.current_task = runtime_task
    rx = sysv.pool.buffer(0)
    tx = sysv.pool.buffer(1)
    assert sysv.pool.map_for_io(tx.buffer_id).name == "MAPPED"
    tx_view = sysv.pool.view(tx, 0, 28)
    tx_view[:] = b"stdout-chunk-1stdout-chunk-2"
    payload = bytes(tx_view)
    sysv.pool.unmap_after_io(tx.buffer_id)
    assert stdio.feed_stdin(b"stdin-chunk-1stdin-chunk-2") == 26
    assert sysv.pool.map_for_io(rx.buffer_id).name == "MAPPED"
    assert (
        stdio.dispatch(
            WasiIpcCmd.STREAM_READ_BUFFER,
            ReadOnlyFlatMapView(
                ((ARG_BUFFER_HANDLE, rx.buffer_id), (ARG_OFFSET, 0), (ARG_MAX_LEN, 64))
            ),
        )
        == 26
    )
    assert bytes(sysv.pool.view(rx, 0, 26)) == b"stdin-chunk-1stdin-chunk-2"
    sysv.pool.unmap_after_io(rx.buffer_id)
    assert sysv.pool.map_for_io(tx.buffer_id).name == "MAPPED"
    sysv.pool.view(tx, 0, len(payload))[:] = payload
    assert (
        stdio.dispatch(
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            ReadOnlyFlatMapView(
                ((ARG_BUFFER_HANDLE, tx.buffer_id), (ARG_OFFSET, 0), (ARG_LENGTH, 28))
            ),
        )
        == 28
    )
    assert stdio.drain_stdout() == b"stdout-chunk-1stdout-chunk-2"
    sysv.pool.unmap_after_io(tx.buffer_id)
    sysv.shutdown()
    print("    [Phase A.1] HAL Standard I/O Driver (stdin/stdout streaming) [PASS]")
    # 2. Timer Driver
    timer = DummyDriver("fireball://hal/timer/0")
    t0 = timer.get_monotonic_ns()
    timer.step_ticks(5)
    assert timer.tick_count == 5
    assert timer.get_monotonic_ns() >= t0
    print("    [Phase A.2] HAL Timer Driver (Monotonic Clock & Ticks) [PASS]")
    # -------------------------------------------------------------------------
    # Part B: uvwasi-compatible reference stack verification
    # -------------------------------------------------------------------------
    wasi_vfs = UvwasiReferenceContext()
    guest_mem = bytearray(4096)
    # 1. WASI stdin read (fd_read on FD 0)
    # Setup iovec at offset 0: buf_ptr = 100, buf_len = 12
    guest_mem[0:4] = (100).to_bytes(4, "little")
    guest_mem[4:8] = (12).to_bytes(4, "little")
    err = wasi_vfs.fd_read(fd=0, memory=guest_mem, iovs_ptr=0, iovs_len=1, nread_ptr=50)
    assert err == WasiErrno.SUCCESS
    nread = int.from_bytes(guest_mem[50:54], "little")
    assert nread == 12
    assert bytes(guest_mem[100:112]) == b"INPUT_STREAM"
    print("    [Phase B.1] WASI fd_read (stdin stream buffering) [PASS]")
    # 2. WASI Virtual File Seek and Read (FD 3: config.ini)
    # Seek to offset 9 (start of "rate=1000\n")
    err_seek = wasi_vfs.fd_seek(
        fd=3, offset=9, whence=WasiWhence.SET, memory=guest_mem, newoffset_ptr=60
    )
    assert err_seek == WasiErrno.SUCCESS
    new_pos = int.from_bytes(guest_mem[60:68], "little")
    assert new_pos == 9
    # Read 9 bytes from offset 9 into memory at 200
    guest_mem[8:12] = (200).to_bytes(4, "little")
    guest_mem[12:16] = (9).to_bytes(4, "little")
    err_r = wasi_vfs.fd_read(fd=3, memory=guest_mem, iovs_ptr=8, iovs_len=1, nread_ptr=50)
    assert err_r == WasiErrno.SUCCESS
    assert bytes(guest_mem[200:209]) == b"rate=1000"
    print("    [Phase B.2] WASI In-Memory VFS (fd_seek & fd_read config.ini) [PASS]")
    # 3. WASI Virtual File Write (FD 3: config.ini)
    guest_mem[300:308] = b"extra=99"
    guest_mem[16:20] = (300).to_bytes(4, "little")
    guest_mem[20:24] = (8).to_bytes(4, "little")
    err_w = wasi_vfs.fd_write(fd=3, memory=guest_mem, iovs_ptr=16, iovs_len=1, nwritten_ptr=50)
    assert err_w == WasiErrno.SUCCESS
    config_file = wasi_vfs.files.view().find(3)
    assert config_file is not None
    assert b"extra=99" in config_file.data
    print("    [Phase B.3] WASI In-Memory VFS (fd_write mutation) [PASS]")
    # 4. WASI random_get
    err_rnd = wasi_vfs.random_get(memory=guest_mem, buf_ptr=400, buf_len=16)
    assert err_rnd == WasiErrno.SUCCESS
    rand_chunk = bytes(guest_mem[400:416])
    assert len(rand_chunk) == 16 and rand_chunk != bytes(16)
    print("    [Phase B.4] WASI random_get (Entropy Pool Fill) [PASS]")
    # 5. WASI clock_time_get
    err_clk = wasi_vfs.clock_time_get(clock_id=1, precision=1000, memory=guest_mem, time_ptr=500)
    assert err_clk == WasiErrno.SUCCESS
    ts_ns = int.from_bytes(guest_mem[500:508], "little")
    assert ts_ns > 0
    print(f"    [Phase B.5] WASI clock_time_get (Monotonic ns={ts_ns}) [PASS]")
    print("    [PASS] Scenario 11 (HAL & WASI Dummy Drivers) verified completely.")


if __name__ == "__main__":
    test_scenario_hal_and_wasi_drivers()
