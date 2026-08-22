"""
docs/components/tier2_runtime/concepts/vmmio_concept.py
Reference Concept Implementation: vMMIO Virtual Bus & Fast Address Dispatch
- FastAddressCheck: Instant boundary checking with bitmask/range
- Static Flat Map dispatch to registered virtual peripheral handlers
- Deterministic trap generation on unaligned access or memory out-of-bounds
"""

from typing import Callable, Any


class TrapCode:
    MEMORY_OUT_OF_BOUNDS = "TRAP_MEMORY_OUT_OF_BOUNDS"
    UNALIGNED_ACCESS = "TRAP_UNALIGNED_ACCESS"
    UNAUTHORIZED_ACCESS = "TRAP_UNAUTHORIZED_ACCESS"


class VMMIOEntry:
    def __init__(self, base_addr: int, size: int,
                 read_fn: Callable[[int, int], int],
                 write_fn: Callable[[int, int, int], None],
                 name: str):
        self.base_addr = base_addr
        self.size = size
        self.read_fn = read_fn
        self.write_fn = write_fn
        self.name = name

    def contains(self, addr: int) -> bool:
        return self.base_addr <= addr < (self.base_addr + self.size)


class VMMIOBus:
    MMIO_BASE = 0x4000_0000
    MMIO_LIMIT = 0x6000_0000

    def __init__(self):
        self.devices: list[VMMIOEntry] = []

    def register_device(self, base_addr: int, size: int,
                        read_fn: Callable[[int, int], int],
                        write_fn: Callable[[int, int, int], None],
                        name: str = ""):
        assert self.is_mmio_range(base_addr), "Device base address must be in MMIO space"
        assert self.is_mmio_range(base_addr + size - 1), "Device end address must be in MMIO space"
        entry = VMMIOEntry(base_addr, size, read_fn, write_fn, name)
        self.devices.append(entry)

    @classmethod
    def is_mmio_range(cls, addr: int) -> bool:
        """FastAddressCheck: Fast range check for MMIO window."""
        return cls.MMIO_BASE <= addr < cls.MMIO_LIMIT

    def read(self, addr: int, size: int) -> tuple[str, int]:
        """
        Executes vMMIO read.
        Returns (status_code, value_or_trap).
        """
        # 1. Alignment check
        if addr % size != 0:
            return (TrapCode.UNALIGNED_ACCESS, 0)

        # 2. Fast MMIO Range Check
        if not self.is_mmio_range(addr):
            return (TrapCode.MEMORY_OUT_OF_BOUNDS, 0)

        # 3. Handler lookup
        for dev in self.devices:
            if dev.contains(addr):
                offset = addr - dev.base_addr
                val = dev.read_fn(offset, size)
                return ("OK", val)

        # Unregistered peripheral address in MMIO window
        return (TrapCode.MEMORY_OUT_OF_BOUNDS, 0)

    def write(self, addr: int, val: int, size: int) -> tuple[str, str]:
        """
        Executes vMMIO write.
        Returns (status_code, detail_message).
        """
        # 1. Alignment check
        if addr % size != 0:
            return (TrapCode.UNALIGNED_ACCESS, "Unaligned memory write")

        # 2. Fast MMIO Range Check
        if not self.is_mmio_range(addr):
            return (TrapCode.MEMORY_OUT_OF_BOUNDS, "Write address outside MMIO range")

        # 3. Handler lookup
        for dev in self.devices:
            if dev.contains(addr):
                offset = addr - dev.base_addr
                dev.write_fn(offset, val, size)
                return ("OK", "Write dispatched")

        return (TrapCode.MEMORY_OUT_OF_BOUNDS, "Unmapped MMIO write")


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================

def test_vmmio_gpio_read_write():
    bus = VMMIOBus()
    gpio_regs = {0x00: 0, 0x04: 0}  # 0x00: DATA, 0x04: DIR

    def gpio_read(offset: int, size: int) -> int:
        return gpio_regs.get(offset, 0)

    def gpio_write(offset: int, val: int, size: int):
        gpio_regs[offset] = val

    bus.register_device(0x4000_1000, 0x100, gpio_read, gpio_write, name="GPIO_PORTA")

    # Step 1: Write DATA register
    st, msg = bus.write(0x4000_1000, 0xAA, size=4)
    assert st == "OK"
    assert gpio_regs[0x00] == 0xAA

    # Step 2: Read DATA register
    st, val = bus.read(0x4000_1000, size=4)
    assert st == "OK"
    assert val == 0xAA


def test_vmmio_traps():
    bus = VMMIOBus()

    # 1. Unaligned access trap (addr 0x4000_0001 with 4-byte size)
    st, _ = bus.read(0x4000_0001, size=4)
    assert st == TrapCode.UNALIGNED_ACCESS

    # 2. Out-of-bounds trap (RAM address 0x2000_0000 sent to MMIO bus)
    st, _ = bus.read(0x2000_0000, size=4)
    assert st == TrapCode.MEMORY_OUT_OF_BOUNDS

    # 3. Unmapped MMIO address trap (0x4500_0000 has no registered device)
    st, _ = bus.write(0x4500_0000, 0x1234, size=4)
    assert st == TrapCode.MEMORY_OUT_OF_BOUNDS


if __name__ == "__main__":
    test_vmmio_gpio_read_write()
    test_vmmio_traps()
    print("[PASS] All vMMIO concept tests passed successfully.")
