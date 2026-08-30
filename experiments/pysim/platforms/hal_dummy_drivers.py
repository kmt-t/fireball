"""
experiments/pysim/hal_dummy_drivers.py
Comprehensive HAL Dummy Drivers for Fireball hardware abstraction testing.
Provides deterministic dummy peripheral drivers:
1. DummyGpioDriver: 16-pin GPIO controller with input/output modes and edge IRQ
2. DummyI2cDriver: I2C bus master with simulated LM75 temperature sensor (0x48)
3. DummySpiDriver: SPI bus master with simulated 4KB SPI EEPROM/Flash (WREN, WRITE, READ)
4. DummyTimerDriver: High-resolution hardware timer with periodic tick and alarms
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

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

import time
from collections.abc import Callable


class PinMode:
    INPUT = 0
    OUTPUT = 1
    PULLUP = 2
    PULLDOWN = 3


class DummyGpioDriver:
    """Simulates a 16-pin micro-controller GPIO port."""

    def __init__(self, pin_count: int = 16):
        self.pin_count = pin_count
        self.modes = [PinMode.INPUT] * pin_count
        self.levels = [0] * pin_count
        self.irq_callbacks: dict[int, Callable[[int, int], None]] = {}

    def set_pin_mode(self, pin: int, mode: int) -> bool:
        if not (0 <= pin < self.pin_count):
            return False
        self.modes[pin] = mode
        return True

    def write_pin(self, pin: int, level: int) -> bool:
        if not (0 <= pin < self.pin_count) or self.modes[pin] != PinMode.OUTPUT:
            return False
        old_level = self.levels[pin]
        self.levels[pin] = 1 if level else 0
        # Trigger IRQ on edge if registered
        if old_level != self.levels[pin] and pin in self.irq_callbacks:
            self.irq_callbacks[pin](pin, self.levels[pin])
        return True

    def read_pin(self, pin: int) -> int:
        if not (0 <= pin < self.pin_count):
            return 0
        return self.levels[pin]

    def register_irq(self, pin: int, callback: Callable[[int, int], None]):
        self.irq_callbacks[pin] = callback


class DummyI2cDriver:
    """Simulates an I2C bus controller with attached I2C devices."""

    def __init__(self):
        self.devices: dict[int, dict[int, int]] = {
            # Device 0x48: LM75 Temperature Sensor
            # Register 0x00: Temperature = 25.5 C (0x1980 in 16-bit format)
            # Register 0x01: Configuration = 0x00
            0x48: {0x00: 0x1980, 0x01: 0x00}
        }

    def write_register(self, dev_addr: int, reg_addr: int, value: int) -> bool:
        if dev_addr not in self.devices:
            return False
        self.devices[dev_addr][reg_addr] = value & 0xFFFF
        return True

    def read_register(self, dev_addr: int, reg_addr: int) -> int:
        if dev_addr not in self.devices or reg_addr not in self.devices[dev_addr]:
            return 0xFFFF  # NACK / error
        return self.devices[dev_addr][reg_addr]


class DummySpiDriver:
    """Simulates an SPI master communicating with a 4KB SPI EEPROM (25LC040)."""

    def __init__(self, memory_size: int = 4096):
        self.memory = bytearray(memory_size)
        self.write_enabled = False

    def transfer(self, tx_data: bytes) -> bytes:
        """Executes a full-duplex SPI transaction."""
        if not tx_data:
            return b""
        cmd = tx_data[0]
        rx = bytearray(len(tx_data))
        if cmd == 0x06:  # WREN (Write Enable)
            self.write_enabled = True
        elif cmd == 0x04:  # WRDI (Write Disable)
            self.write_enabled = False
        elif cmd == 0x03 and len(tx_data) >= 3:  # READ: [0x03, addr_hi, addr_lo, dummy...]
            addr = (tx_data[1] << 8) | tx_data[2]
            length = len(tx_data) - 3
            for i in range(length):
                read_idx = (addr + i) % len(self.memory)
                rx[3 + i] = self.memory[read_idx]
        elif (
            cmd == 0x02 and len(tx_data) >= 3 and self.write_enabled
        ):  # WRITE: [0x02, addr_hi, addr_lo, data...]
            addr = (tx_data[1] << 8) | tx_data[2]
            payload = tx_data[3:]
            for i, b in enumerate(payload):
                write_idx = (addr + i) % len(self.memory)
                self.memory[write_idx] = b

            self.write_enabled = False  # Auto-disable after write
        return bytes(rx)


class DummyTimerDriver:
    """Simulates a hardware periodic timer and high-precision monotonic clock."""

    def __init__(self):
        self.start_time_ns = time.monotonic_ns()
        self.tick_count = 0

    def get_monotonic_ns(self) -> int:
        return time.monotonic_ns() - self.start_time_ns

    def step_ticks(self, count: int = 1) -> int:
        self.tick_count += count
        return self.tick_count
