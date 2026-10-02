"""vMMIO contracts from runtime_vmmio_test_spec.md; assertions inspect addresses and effects."""

from __future__ import annotations

from pathlib import Path

import pytest
from scheduler import Scheduler
from vmmio import TrapCode, VMMIOController, VmmioStatus


def _controller(guest_ram_size: int = 6003) -> VMMIOController:
    scheduler = Scheduler()
    scheduler.current_task = scheduler.get_task(scheduler.spawn("owner"))
    return VMMIOController(guest_ram_size=guest_ram_size, scheduler=scheduler)


def test_vmmio_01_ram_bypasses_page_table_and_tlb() -> None:
    """TEST-VMMIO-01 / GOTCHA-VMMIO-01: Valid RAM accesses preserve every TLB entry."""
    ctrl = _controller()
    ctrl.map_passthrough_page(vpn=0xF0000, phys_page=7)
    assert ctrl.access(0xF000_0003, False) == (VmmioStatus.OK_PHYSICAL, 0x7003)
    before = tuple((slot.vpn, slot.pte) for slot in ctrl.tlb)
    counters = (ctrl.tlb_hits, ctrl.tlb_misses)
    for address in (0, 1, 4095, 6002):
        for is_write in (False, True):
            assert ctrl.access(address, is_write) == (VmmioStatus.OK_GUEST_RAM, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == counters
    assert tuple((slot.vpn, slot.pte) for slot in ctrl.tlb) == before
    assert ctrl.ptes.view().find(0xF0000) is before[ctrl.tlb_index(0xF0000)][1]


@pytest.mark.parametrize("ram_size", (4096, 6003))
def test_vmmio_02_ram_exact_boundary_without_power_of_two_assumption(ram_size: int) -> None:
    """TEST-VMMIO-02: The final byte is accessible; size and later addresses are rejected."""
    ctrl = _controller(ram_size)
    for is_write in (False, True):
        assert ctrl.access(ram_size - 1, is_write) == (VmmioStatus.OK_GUEST_RAM, 0)
        assert ctrl.access(ram_size, is_write) == (TrapCode.OUT_OF_BOUNDS, 0)
        assert ctrl.access(ram_size + 1, is_write) == (TrapCode.OUT_OF_BOUNDS, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (0, 0)


@pytest.mark.parametrize("address", (0x1000, 0x2000, 0x10001, 0x7FFF_FFFF))
def test_vmmio_03_out_of_bounds_ram_never_wraps(address: int) -> None:
    """TEST-VMMIO-03: Addresses that a RAM-size mask would fold must trap."""
    ctrl = _controller(4096)
    for is_write in (False, True):
        assert ctrl.access(address, is_write) == (TrapCode.OUT_OF_BOUNDS, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (0, 0)


def test_vmmio_10_11_static_handler_receives_address_fields_on_miss_and_hit() -> None:
    """TEST-VMMIO-10, TEST-VMMIO-11: Cold and warm device accesses deliver the exact command fields."""
    ctrl = _controller()
    calls: list[tuple[int, int, bool]] = []
    ctrl.map_static_device(
        0xC1234, handler=lambda metadata, offset, write: calls.append((metadata, offset, write))
    )
    assert ctrl.access(0xC123_4005, False) == (VmmioStatus.OK_STATIC_DEVICE, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (0, 1)
    assert ctrl.access(0xC123_4017, True) == (VmmioStatus.OK_STATIC_DEVICE, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (1, 1)
    assert calls == [(0x123, 5, False), (0x123, 0x17, True)]


@pytest.mark.parametrize("function_code", (8, 9, 10, 11))
def test_vmmio_12_undefined_function_code_traps(function_code: int) -> None:
    """TEST-VMMIO-12: Non-RAM undefined FCs return their specific trap without an address."""
    ctrl = _controller()
    for is_write in (False, True):
        assert ctrl.access(function_code << 28, is_write) == (TrapCode.UNDEFINED_FC, 0)


def test_vmmio_13_dynamic_unregistered_page_traps() -> None:
    """TEST-VMMIO-13: Missing FC=13 PTE is distinct from an undefined FC."""
    ctrl = _controller()
    assert ctrl.access(0xD000_0000, False) == (TrapCode.UNREGISTERED_PAGE, 0)
    assert ctrl.access(0xD000_0000, True) == (TrapCode.UNREGISTERED_PAGE, 0)


def test_vmmio_14_15_interleaved_function_codes_keep_separate_tlb_slots() -> None:
    """TEST-VMMIO-14, TEST-VMMIO-15 / GOTCHA-VMMIO-02: Equal low VPNs across FCs cannot thrash."""
    ctrl = _controller()
    ctrl.map_static_device(0xC0000)
    ctrl.map_shm_page(0xE0000, phys_page=2, owner_id=ctrl.scheduler.current_task_id)
    ctrl.map_passthrough_page(0xF0000, phys_page=7)
    assert len({ctrl.tlb_index(vpn) for vpn in (0xC0000, 0xE0000, 0xF0000)}) == 3
    for _ in range(10):
        assert ctrl.access(0xC000_0003, False) == (VmmioStatus.OK_STATIC_DEVICE, 0)
        assert ctrl.access(0xE000_0003, False) == (VmmioStatus.OK_PHYSICAL, 0x2003)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (18, 2)


def test_vmmio_16_all_registered_pages_resolve_and_hot_working_set_hits() -> None:
    """TEST-VMMIO-16: Each of 32 mapped pages resolves to its independently specified physical page."""
    ctrl = _controller()
    for page in range(32):
        ctrl.map_shm_page(
            0xE0000 + page, phys_page=100 + page, owner_id=ctrl.scheduler.current_task_id
        )
    for page in range(32):
        assert ctrl.access(0xE000_0000 + page * 4096 + 19, False) == (
            VmmioStatus.OK_PHYSICAL,
            (100 + page) * 4096 + 19,
        )
    misses_before = ctrl.tlb_misses
    hits_before = ctrl.tlb_hits
    for _ in range(3):
        for page in range(8):
            assert ctrl.access(0xE000_0000 + page * 4096 + 19, True) == (
                VmmioStatus.OK_PHYSICAL,
                (100 + page) * 4096 + 19,
            )
    assert ctrl.tlb_misses == misses_before
    assert ctrl.tlb_hits == hits_before + 24


def test_vmmio_17_read_only_shm_permission_is_enforced_on_tlb_hit() -> None:
    """TEST-VMMIO-17: Warm translation never bypasses a read-only SHM PTE."""
    ctrl = _controller()
    ctrl.map_shm_page(0xE0000, phys_page=2, owner_id=ctrl.scheduler.current_task_id)
    pte = ctrl.ptes.view().find(0xE0000)
    assert pte is not None
    pte.write = False
    assert ctrl.access(0xE000_0007, False) == (VmmioStatus.OK_PHYSICAL, 0x2007)
    assert ctrl.access(0xE000_0007, True) == (TrapCode.ACCESS_VIOLATION, 0)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (1, 1)
    assert not pte.write


def test_vmmio_18_collision_rewalks_correct_pte_instead_of_reusing_wrong_translation() -> None:
    """TEST-VMMIO-18: A/B/A TLB collision preserves physical identity and always rewalks."""
    ctrl = _controller()
    ctrl.map_shm_page(0xE0000, phys_page=2, owner_id=ctrl.scheduler.current_task_id)
    ctrl.map_shm_page(0xE0021, phys_page=7, owner_id=ctrl.scheduler.current_task_id)
    assert ctrl.tlb_index(0xE0000) == ctrl.tlb_index(0xE0021)
    for address, expected in ((0xE000_0003, 0x2003), (0xE002_1003, 0x7003), (0xE000_0003, 0x2003)):
        assert ctrl.access(address, False) == (VmmioStatus.OK_PHYSICAL, expected)
    assert (ctrl.tlb_hits, ctrl.tlb_misses) == (0, 3)


@pytest.mark.parametrize("address", (0xC000_0000, 0xC000_2000))
def test_vmmio_19_30_legacy_syscall_and_vdma_control_pages_are_unregistered(address: int) -> None:
    """TEST-VMMIO-19, TEST-VMMIO-30: Removed device doorbells have no registered dispatch path."""
    ctrl = _controller()
    assert ctrl.access(address, True, value=1) == (TrapCode.UNREGISTERED_PAGE, 0)
    assert ctrl.ptes.view().find(address >> 12) is None


@pytest.mark.parametrize("revoke", (False, True))
def test_vmmio_20_to_23_unmap_or_revoke_removes_pte_and_warm_translation(revoke: bool) -> None:
    """TEST-VMMIO-20, TEST-VMMIO-21, TEST-VMMIO-22, TEST-VMMIO-23 / GOTCHA-VMMIO-03: Both former owner and peer are denied after revoke."""
    ctrl = _controller()
    owner = ctrl.scheduler.current_task
    peer = ctrl.scheduler.get_task(ctrl.scheduler.spawn("peer"))
    ctrl.map_shm_page(0xE0000, phys_page=2, owner_id=owner.task_id)
    assert ctrl.access(0xE000_0009, True) == (VmmioStatus.OK_PHYSICAL, 0x2009)
    slot = ctrl.tlb[ctrl.tlb_index(0xE0000)]
    assert slot.vpn == 0xE0000 and slot.pte is not None
    if revoke:
        ctrl.revoke_shm_owner(0xE0000)
    else:
        ctrl.unmap_shm_page(0xE0000)
    assert ctrl.ptes.view().find(0xE0000) is None
    assert slot.pte is None
    misses_before = ctrl.tlb_misses
    hits_before = ctrl.tlb_hits
    for task in (owner, peer):
        ctrl.scheduler.current_task = task
        for is_write in (False, True):
            assert ctrl.access(0xE000_0009, is_write) == (TrapCode.UNREGISTERED_PAGE, 0)
    assert ctrl.tlb_hits == hits_before
    assert ctrl.tlb_misses == misses_before + 4


def test_vmmio_25_passthrough_resolves_physical_page_and_offset() -> None:
    """TEST-VMMIO-25: FC=15 translation preserves the full offset on cold and warm access."""
    ctrl = _controller()
    ctrl.map_passthrough_page(0xF0000, phys_page=3)
    for is_write in (False, True):
        assert ctrl.access(0xF000_0127, is_write) == (VmmioStatus.OK_PHYSICAL, 0x3127)


def test_vmmio_28_access_width_cannot_cross_ram_or_shm_mapping() -> None:
    """TEST-VMMIO-28: Whole-access range is checked before any usable translation is returned."""
    ctrl = _controller(6003)
    for is_write in (False, True):
        assert ctrl.access(5999, is_write, access_size=4) == (VmmioStatus.OK_GUEST_RAM, 0)
        assert ctrl.access(6000, is_write, access_size=4) == (TrapCode.OUT_OF_BOUNDS, 0)
    ctrl.map_shm_page(
        0xE0000, physical_addr=0x2003, mapping_size=17, owner_id=ctrl.scheduler.current_task_id
    )
    for is_write in (False, True):
        assert ctrl.access(0xE000_000D, is_write, access_size=4) == (
            VmmioStatus.OK_PHYSICAL,
            0x2010,
        )
        assert ctrl.access(0xE000_000E, is_write, access_size=4) == (TrapCode.OUT_OF_BOUNDS, 0)
        assert ctrl.access(0xE000_0011, is_write) == (TrapCode.OUT_OF_BOUNDS, 0)
    ctrl.map_passthrough_page(0xF0000, phys_page=7)
    assert ctrl.access(0xF000_0FFC, False, access_size=4) == (VmmioStatus.OK_PHYSICAL, 0x7FFC)
    assert ctrl.access(0xF000_0FFD, False, access_size=4) == (TrapCode.OUT_OF_BOUNDS, 0)


@pytest.mark.parametrize("function_code", (13, 14))
def test_vmmio_29_32_cached_owned_mapping_rejects_nonowner_read_and_write(
    function_code: int,
) -> None:
    """TEST-VMMIO-29, TEST-VMMIO-32: A warm DYNAMIC/SHM mapping never yields a physical address to a peer."""
    ctrl = _controller()
    owner = ctrl.scheduler.current_task
    peer = ctrl.scheduler.get_task(ctrl.scheduler.spawn("peer"))
    vpn = function_code << 16
    if function_code == 13:
        ctrl.map_dynamic_page(
            vpn, phys_page=2, owner_id=owner.task_id, storage=memoryview(bytearray(256))
        )
    else:
        ctrl.map_shm_page(vpn, phys_page=2, owner_id=owner.task_id)
    assert ctrl.access(vpn << 12, False) == (VmmioStatus.OK_PHYSICAL, 0x2000)
    pte = ctrl.ptes.view().find(vpn)
    ctrl.scheduler.current_task = peer
    for is_write in (False, True):
        assert ctrl.access(vpn << 12, is_write) == (TrapCode.OWNER_MISMATCH, 0)
    assert ctrl.ptes.view().find(vpn) is pte
    assert pte.owner_id == owner.task_id
    ctrl.scheduler.current_task = owner
    assert ctrl.access(vpn << 12, True) == (VmmioStatus.OK_PHYSICAL, 0x2000)


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(Path(__file__).resolve())]))
