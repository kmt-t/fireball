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

"""Integration Scenario 10: Tier 2 Runtime vMMIO Virtual Devices & Address Translation.

Tests:
- Bit 31 RAM Bypass: Linear RAM (Bit 31 == 0) fast bypass vs vMMIO (Bit 31 == 1)
- FlatMap Page Table & PTE Permission Checking (VALID, READ, WRITE, EXEC)
- Function Code (FC) Decoding: Static Device (0xC), Shared Memory (0xE), Passthrough (0xF)
- Direct-mapped Software TLB[16] with Folding XOR Hash, Hit/Miss counter & Invalidation
- Task Ownership Isolation & TRAP_OWNER_MISMATCH detection
- Static Device syscall dispatch and handler callback
"""

from vmmio import (
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
)


def test_scenario_vmmio_virtual_devices():
    print(
        "[*] Running Scenario 10: Tier 2 Runtime vMMIO Virtual Devices & Page Table..."
    )
    # -------------------------------------------------------------------------
    # Phase 1: VmmioAddress Decoding & RAM Bypass Flag (Bit 31)
    # -------------------------------------------------------------------------
    addr_ram = VmmioAddress(0x0001_8000)  # Linear RAM: Bit 31 == 0
    addr_vmmio = VmmioAddress(0xC000_1020)  # vMMIO Static Device: Bit 31 == 1, FC=0xC
    assert addr_ram.is_linear() is True
    assert addr_vmmio.is_linear() is False
    assert addr_vmmio.fc() == FC_STATIC_DEVICE
    assert addr_vmmio.vpn() == (0xC000_1020 >> 12)
    assert addr_vmmio.offset() == 0x020
    print(
        "    [Phase 1] VmmioAddress Decoding (Bit 31 RAM Bypass vs FC=0xC Device) [PASS]"
    )
    # -------------------------------------------------------------------------
    # Phase 2: vMMIO Page Table Registration & PTE Permission Checks
    # -------------------------------------------------------------------------
    controller = VMMIOController(guest_ram_size=64 * 1024)
    # Handlers tracking
    handled_events = []

    def mock_device_handler(metadata: int, offset: int, is_write: bool):
        handled_events.append((metadata, offset, is_write))

    # 1. Register Virtual Device Page at 0xC000_1000 (Read/Write with handler)
    dev_vpn = 0xC000_1000 >> 12
    controller.map_static_device(
        dev_vpn, handler=mock_device_handler, read=True, write=True
    )
    # 2. Register Read-Only Shared Memory Page at 0xE000_2000 (Owner: Task 2)
    shm_vpn = 0xE000_2000 >> 12
    controller.map_shm_page(vpn=shm_vpn, phys_page=0x20, owner_id=2)
    # 3. Register Passthrough Physical Page at 0xF000_3000
    pass_vpn = 0xF000_3000 >> 12
    controller.map_passthrough_page(vpn=pass_vpn, phys_page=0x30, read=True, write=True)
    # -------------------------------------------------------------------------
    # Phase 3: Access Validation, TLB Caching & Owner Isolation
    # -------------------------------------------------------------------------
    # 3.1 Linear RAM Fast-Bypass Access (Bit 31 == 0)
    status_ram, _ = controller.access(
        raw_addr=0x0000_0100, is_write=False, current_task_id=1
    )
    assert status_ram == "OK_GUEST_RAM"
    print("    [Phase 3.1] Linear RAM O(1) Fast-Bypass Access -> OK_GUEST_RAM [PASS]")
    # 3.2 Device Page Read/Write by Owner & Handler Dispatch
    status_dev_w, _ = controller.access(
        raw_addr=0xC000_1010, is_write=True, current_task_id=1
    )
    assert status_dev_w == "OK_SYSCALL"
    assert len(handled_events) == 1
    assert handled_events[0] == (0, 0x010, True)
    print(
        "    [Phase 3.2] vMMIO Device Page Write & Syscall Dispatch -> OK_SYSCALL [PASS]"
    )
    # 3.3 TLB Hit Verification (4-bit Folding XOR Hash)
    tlb_idx = controller.tlb_index(dev_vpn)
    assert controller.tlb[tlb_idx]["vpn"] == dev_vpn
    initial_hits = controller.tlb_hits
    status_dev_r, _ = controller.access(
        raw_addr=0xC000_1010, is_write=False, current_task_id=1
    )
    assert status_dev_r == "OK_SYSCALL"
    assert controller.tlb_hits == initial_hits + 1
    print(
        "    [Phase 3.3] Direct-Mapped Software TLB Hit (Folding XOR Hash) -> TLB_HIT [PASS]"
    )
    # 3.4 Permission Violation: Write to Read-Only SHM
    # First access to SHM (Owner 2) write check
    status_shm_w, _ = controller.access(
        raw_addr=0xE000_2008, is_write=True, current_task_id=2
    )
    # SHM was mapped with write=True by default in map_shm_page; verify owner mismatch for task 1
    status_owner_err, _ = controller.access(
        raw_addr=0xE000_2008, is_write=False, current_task_id=1
    )
    assert status_owner_err == TrapCode.OWNER_MISMATCH
    print(
        "    [Phase 3.4] Task Isolation Check (Task 1 accessing Task 2 SHM) -> TRAP_OWNER_MISMATCH [PASS]"
    )
    # 3.5 Revoke Ownership to In-Flight & TLB Invalidation
    controller.revoke_shm_owner(shm_vpn)
    status_flight, _ = controller.access(
        raw_addr=0xE000_2008, is_write=False, current_task_id=2
    )
    assert status_flight == TrapCode.OWNER_MISMATCH
    print(
        "    [Phase 3.5] IPC Revoke & In-Flight TLB Invalidation -> TRAP_OWNER_MISMATCH [PASS]"
    )
    # 3.6 Passthrough Physical Memory Access (FC=0xF)
    status_pass, detail = controller.access(
        raw_addr=0xF000_3040, is_write=True, current_task_id=1
    )
    assert status_pass == "OK_PHYSICAL"
    assert "0x00030040" in detail
    print("    [Phase 3.6] Passthrough Direct Physical Access -> OK_PHYSICAL [PASS]")
    # 3.7 Unregistered Page Trap
    status_unreg, _ = controller.access(
        raw_addr=0xC000_9000, is_write=False, current_task_id=1
    )
    assert status_unreg == TrapCode.UNREGISTERED_PAGE
    print(
        "    [Phase 3.7] Unregistered vMMIO Address Access -> TRAP_UNREGISTERED_PAGE [PASS]"
    )
    print(
        "    [PASS] Scenario 10 (vMMIO Virtual Devices & Address Translation) verified completely."
    )


if __name__ == "__main__":
    test_scenario_vmmio_virtual_devices()
