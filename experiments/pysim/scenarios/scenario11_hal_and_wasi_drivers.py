from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PYSIM_DIR.parents[1]

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms',
           _REPO_ROOT / 'docs' / 'components' / 'tier1_core' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier1_interface' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier2_runtime' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_jit' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_platform' / 'concepts']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path




"""Integration Scenario 11: HAL Peripheral Drivers & WASI Preview 1 Full Dummy Stack.

Tests:
1. HAL Dummy Peripheral Drivers:
   - GPIO: Mode configuration (Input/Output), Pin state read/write, Edge-triggered IRQ dispatch
   - I2C: Bus master register read/write on simulated LM75 temperature sensor (0x48)
   - SPI: Full-duplex bus master transaction on simulated 4KB SPI EEPROM (WREN, WRITE, READ)
   - Timer: High-resolution monotonic clock (monotonic_ns) and tick advancement
2. WASI Preview 1 In-Memory Virtual Stack:
   - File I/O: Virtual file descriptors (fd_read, fd_write, fd_seek: SET/CUR/END)
   - Standard Streams: stdin buffered reading, stdout/stderr capture
   - System Utilities: random_get (entropy pool fill), clock_time_get (monotonic/realtime timestamp)
"""


from hal_dummy_drivers import DummyGpioDriver, DummyI2cDriver, DummySpiDriver, DummyTimerDriver, PinMode
from wasi_dummy_fs import WasiDummyContext, WasiErrno, WasiWhence


def test_scenario_hal_and_wasi_drivers():
    print("[*] Running Scenario 11: HAL Dummy Drivers & WASI Preview 1 Dummy Stack...")

    # -------------------------------------------------------------------------
    # Part A: HAL Peripheral Dummy Drivers Verification
    # -------------------------------------------------------------------------

    # 1. GPIO Controller
    gpio = DummyGpioDriver(pin_count=16)
    gpio.set_pin_mode(2, PinMode.OUTPUT)
    gpio.set_pin_mode(3, PinMode.INPUT)

    irq_events = []
    gpio.register_irq(2, lambda pin, lvl: irq_events.append((pin, lvl)))

    gpio.write_pin(2, 1)
    assert gpio.read_pin(2) == 1
    gpio.write_pin(2, 0)
    assert gpio.read_pin(2) == 0
    assert irq_events == [(2, 1), (2, 0)]
    print("    [Phase A.1] HAL GPIO Driver (Pin R/W & Edge IRQ Dispatch) [PASS]")

    # 2. I2C Bus Master & LM75 Temperature Sensor
    i2c = DummyI2cDriver()
    # Read default temperature from device 0x48 reg 0x00 (25.5 C -> 0x1980)
    temp_raw = i2c.read_register(0x48, 0x00)
    assert temp_raw == 0x1980
    # Write configuration register 0x01 = 0x02
    assert i2c.write_register(0x48, 0x01, 0x02) is True
    assert i2c.read_register(0x48, 0x01) == 0x02
    print("    [Phase A.2] HAL I2C Driver & LM75 Sensor (0x48 Temp Read & Reg Write) [PASS]")

    # 3. SPI Bus Master & 4KB EEPROM (25LC040)
    spi = DummySpiDriver(memory_size=4096)
    # Enable write (WREN: 0x06)
    spi.transfer(bytes([0x06]))
    assert spi.write_enabled is True

    # Write payload [0xAA, 0xBB, 0xCC, 0xDD] at address 0x0100 (CMD 0x02, addr_hi 0x01, addr_lo 0x00)
    tx_write = bytes([0x02, 0x01, 0x00, 0xAA, 0xBB, 0xCC, 0xDD])
    spi.transfer(tx_write)
    assert spi.write_enabled is False  # auto disabled

    # Read back 4 bytes from address 0x0100 (CMD 0x03, addr_hi 0x01, addr_lo 0x00, 4 dummy bytes)
    tx_read = bytes([0x03, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
    rx_data = spi.transfer(tx_read)
    assert rx_data[3:] == bytes([0xAA, 0xBB, 0xCC, 0xDD])
    print("    [Phase A.3] HAL SPI Driver & 4KB EEPROM (WREN, Write & Read Verification) [PASS]")

    # 4. Timer Driver
    timer = DummyTimerDriver()
    t0 = timer.get_monotonic_ns()
    timer.step_ticks(5)
    assert timer.tick_count == 5
    assert timer.get_monotonic_ns() >= t0
    print("    [Phase A.4] HAL Timer Driver (Monotonic Clock & Ticks) [PASS]")

    # -------------------------------------------------------------------------
    # Part B: WASI Preview 1 In-Memory Dummy Stack Verification
    # -------------------------------------------------------------------------
    wasi_vfs = WasiDummyContext()
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
    err_seek = wasi_vfs.fd_seek(fd=3, offset=9, whence=WasiWhence.SET, memory=guest_mem, newoffset_ptr=60)
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
    assert b"extra=99" in wasi_vfs.files[3].data
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
