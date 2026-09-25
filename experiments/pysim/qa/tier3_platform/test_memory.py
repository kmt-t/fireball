from __future__ import annotations

"""
Unit tests for the memory-manager software contract and virtual mapping notifications. ARMv8-M MPU/W^X is TBD.
Traceability: system_memory_test_spec.md / runtime_memory_test_spec.md
"""

from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from helpers import expect_assertion
from memory import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_TASK_HEAP_SIZES,
    FB_TASK_ID_FLIGHT,
    MemoryErrorCode,
    MemoryManager,
    RecoveryAction,
)
from scheduler import Scheduler
from vmmio import (
    TrapCode,
    VMMIOController,
    VmmioStatus,
)


def _make_memory_manager(*task_ids: int) -> tuple[MemoryManager, Scheduler]:
    """Create a manager whose scheduler owns all test task identities."""
    ids = task_ids or (1,)
    scheduler = Scheduler()
    for task_id in ids:
        scheduler.spawn(f"test_task_{task_id}", task_id=task_id)
    scheduler.current_task = scheduler.get_task(ids[0])
    assert scheduler.current_task is not None
    return MemoryManager(scheduler), scheduler


def test_mem_01_acquire_task_heap_fixed_size():
    """TEST-MEM-01: acquire-task-heap provides task-specific fixed partition (no arbitrary size)."""
    mm, _ = _make_memory_manager(1)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    res = mm.acquire_task_heap()
    assert res.is_ok
    pv = res.unwrap()
    assert pv.size == FB_CONF_TASK_HEAP_SIZES[0]
    assert pv.owner == 1
    assert not hasattr(mm, "allocate"), "Generic heap allocate() must not exist"


def test_mem_02_recovery_strategy_on_exhaustion():
    """TEST-MEM-02: Memory exhaustion returns structured error with recovery strategy."""
    mm, scheduler = _make_memory_manager(1, 2)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_TASK_HEAP_SIZES[0])
    assert mm.acquire_task_heap().is_ok
    scheduler.current_task = scheduler.get_task(2)
    assert scheduler.current_task is not None
    r2 = mm.acquire_task_heap()
    assert r2.is_err
    assert r2.error.error_code == MemoryErrorCode.POOL_EXHAUSTED
    assert r2.error.recovery.action in (RecoveryAction.DEGRADE, RecoveryAction.RETRY)


def test_mem_03_total_allocation_bound():
    """TEST-MEM-03: Total allocated bytes never exceeds FB_CONF_MEMORY_POOL_SIZE."""
    mm, scheduler = _make_memory_manager(*range(1, 10))
    pool_size = FB_CONF_MEMORY_POOL_SIZE
    mm.init_manager(pool_base=0x00010000, pool_size=pool_size)
    allocation_failed = False
    for i in range(1, 10):
        scheduler.current_task = scheduler.get_task(i)
        assert scheduler.current_task is not None
        res = mm.acquire_task_heap()
        assert mm.total_allocated_bytes <= pool_size
        if res.is_err:
            allocation_failed = True
            break
    assert allocation_failed, "allocation must fail at the configured pool boundary"


def test_mem_04_owner_task_id_auto_set():
    """TEST-MEM-04: Caller task-id is automatically recorded on all allocations."""
    mm, sched = _make_memory_manager(5)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    p_res = mm.acquire_task_heap()
    assert p_res.unwrap().owner == 5
    s_res = mm.allocate_shared(size=1024)
    assert s_res.unwrap().owner == 5


def test_mem_05_release_and_deallocate_owner_only():
    """TEST-MEM-05: Partition release is permitted ONLY by owner task."""
    mm, scheduler = _make_memory_manager(3, 4)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    scheduler.current_task = scheduler.get_task(3)
    assert scheduler.current_task is not None
    mm.acquire_task_heap()
    assert mm.partition_owners.view().find(3) is not None
    # Rogue task 4 attempts to release task 3's partition
    scheduler.current_task = scheduler.get_task(4)
    mm.release_task_heap()
    assert mm.partition_owners.view().find(3) is not None
    # Owner releases
    scheduler.current_task = scheduler.get_task(3)
    mm.release_task_heap()
    assert mm.partition_owners.view().find(3) is None


def test_mem_06_guest_ram_64kb_alignment():
    """TEST-MEM-06: pool_base is strictly 64KB aligned."""
    mm, _ = _make_memory_manager()
    assert mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE).is_ok
    with expect_assertion("64KB aligned"):
        mm.init_manager(pool_base=0x00011000, pool_size=FB_CONF_MEMORY_POOL_SIZE)


def test_mem_10_shared_block_ownership_transfer():
    """TEST-MEM-10: allocate-shared -> release -> claim moves ownership cleanly without double-ownership."""
    mm, sched = _make_memory_manager(1, 2)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb_a = mm.allocate_shared(size=1024).unwrap()
    assert sb_a.get_owner() == 1

    # Verify bytearray accessors on active SharedBlock
    sb_a.write_u8(0, 0xAB)
    assert sb_a.read_u8(0) == 0xAB

    sb_a.write_u16(2, 0x1234)
    assert sb_a.read_u16(2) == 0x1234

    sb_a.write_u32(4, 0xCAFEBABE)
    assert sb_a.read_u32(4) == 0xCAFEBABE

    sb_a.write_i32(8, -42)
    assert sb_a.read_i32(8) == -42

    sb_a.write_bytes(16, b"Hello Fireball SHM")
    assert sb_a.read_bytes(16, 18) == b"Hello Fireball SHM"

    sb_a.write_kv(40, 0x1000, 0x2000)
    assert sb_a.read_kv(40) == (0x1000, 0x2000)

    # uint64_t array accessors (treating shared block as uint64_t[])
    assert sb_a.u64_capacity() == 128
    sb_a.write_u64(10, 0x1122334455667788)
    assert sb_a.read_u64(10) == 0x1122334455667788

    sb_a.write_entry(11, key=0x12345678, val=0x9ABCDEF0)
    assert sb_a.read_entry(11) == (0x12345678, 0x9ABCDEF0)

    # Underlying bytearray direct accessor
    raw_ba = sb_a.get_bytearray()
    assert isinstance(raw_ba, memoryview)
    assert raw_ba[0] == 0xAB

    page_idx = sb_a.page_idx
    shm_id = sb_a.release()
    assert not sb_a._is_active
    assert mm.page_registry.get_owner(page_idx) == FB_TASK_ID_FLIGHT

    # Access during in-flight must raise AssertionError
    with expect_assertion():
        sb_a.read_u32(4)

    # Simulate IPC Router Grant phase
    mm.page_registry.update_owner(page_idx, 2)
    sched.current_task = sched.get_task(2)
    assert sched.current_task is not None
    sb_b = mm.claim(shm_id).unwrap()
    assert sb_b.get_owner() == 2
    assert sb_b._is_active
    assert mm.page_registry.get_owner(page_idx) == 2

    # Receiver can read everything sender wrote into the uint64_t array!
    assert sb_b.read_u32(4) == 0xCAFEBABE
    assert sb_b.read_bytes(16, 18) == b"Hello Fireball SHM"
    assert sb_b.read_kv(40) == (0x1000, 0x2000)
    assert sb_b.read_u64(10) == 0x1122334455667788
    assert sb_b.read_entry(11) == (0x12345678, 0x9ABCDEF0)


def test_mem_10c_rollback_transfer_restores_owner_id():
    """TEST-MEM-10c: rollback_transfer() restores PTE owner_id to the original sender."""
    mm, scheduler = _make_memory_manager(1, 2)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    vmmio.register_to_memory_manager(mm)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(size=1024).unwrap()
    raw_addr = 0xE000_0000 + (sb.page_idx * 4096)
    assert vmmio.access(raw_addr, is_write=False)[0] == VmmioStatus.OK_PHYSICAL
    shm_id = sb.release()
    assert mm.page_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT
    assert vmmio.access(raw_addr, is_write=False)[0] == TrapCode.UNREGISTERED_PAGE
    scheduler.current_task = scheduler.get_task(1)
    assert scheduler.current_task is not None
    mm.rollback_transfer(shm_id=shm_id)
    assert mm.page_registry.get_owner(sb.page_idx) == 1
    assert vmmio.access(raw_addr, is_write=False)[0] == VmmioStatus.OK_PHYSICAL


def test_mem_11_shared_block_raII_auto_deallocate():
    """TEST-MEM-11: SharedBlock RAII automatically deallocates buffer on drop."""
    mm, sched = _make_memory_manager(2)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    initial_alloc = mm.total_allocated_bytes
    with mm.allocate_shared(size=1024).unwrap() as sb:
        assert mm.total_allocated_bytes > initial_alloc
    assert mm.shm_slots.view().find(sb.shm_id) is None
    assert mm.total_allocated_bytes == initial_alloc
    assert mm.shm_slots.view().find(sb.shm_id) is None


def test_mem_14_page_granular_permission_isolation():
    """TEST-MEM-14: Different tasks cannot share the same 4KB page; separate pages allocated."""
    mm, sched = _make_memory_manager(1, 2)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    # Task 1 allocates a small block (256 bytes)
    sb_t1_a = mm.allocate_shared(size=256).unwrap()
    # Task 1 allocates another small block (256 bytes) -> a separate page is
    # required so page-granular ownership transfer cannot split a page.
    sb_t1_b = mm.allocate_shared(size=256).unwrap()
    assert sb_t1_a.page_idx != sb_t1_b.page_idx
    assert sb_t1_a.slot_idx == sb_t1_b.slot_idx == 0

    # Task 2 allocates a small block (256 bytes) -> MUST allocate a separate 4KB page!
    sched.current_task = sched.get_task(2)
    assert sched.current_task is not None
    sb_t2 = mm.allocate_shared(size=256).unwrap()
    assert sb_t2.page_idx != sb_t1_a.page_idx
    assert mm.shm_pages[sb_t1_a.page_idx].owner_id == 1
    assert mm.shm_pages[sb_t2.page_idx].owner_id == 2


def test_mem_15_vmmio_fc14_tlb_sync():
    """TEST-MEM-15: ownership changes unmap FC=14 until claim remaps the page."""

    mm, sched = _make_memory_manager(1, 2)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=sched)
    vmmio.register_to_memory_manager(mm)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    sb = mm.allocate_shared(size=512).unwrap()
    raw_addr = 0xE000_0000 + (sb.page_idx * 4096)

    # Verify task 1 can access its own SHM page
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == VmmioStatus.OK_PHYSICAL

    # Task 2 access traps on the mapped page's owner check.
    sched.current_task = sched.get_task(2)
    assert sched.current_task is not None
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == TrapCode.OWNER_MISMATCH

    # Release puts page in flight -> Task 1 also traps!
    sched.current_task = sched.get_task(1)
    assert sched.current_task is not None
    shm_id = sb.release()
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == TrapCode.UNREGISTERED_PAGE

    # Grant changes ownership and therefore unmaps the page again.
    sched.current_task = sched.get_task(2)
    assert mm.grant_shared(shm_id)
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == TrapCode.UNREGISTERED_PAGE

    # Claim establishes the receiver's active view and remaps the page.
    claimed = mm.claim(shm_id).unwrap()
    assert claimed.get_owner() == 2
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == VmmioStatus.OK_PHYSICAL

    # The old owner remains isolated by the remapped PTE owner check.
    sched.current_task = sched.get_task(1)
    assert sched.current_task is not None
    status, _ = vmmio.access(raw_addr, is_write=False)
    assert status == TrapCode.OWNER_MISMATCH


def test_mem_16_virtual_page_reservation_is_independent_of_shm_backing():
    """A 4KB virtual slot maps only the requested bytes from the 1KB SHM pool."""
    from memory import FB_CONF_SHM_SIM_BASE, FB_CONF_SHM_SIZE, FB_PAGE_SIZE

    mm, scheduler = _make_memory_manager(1)
    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    vmmio.register_to_memory_manager(mm)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    first = mm.allocate_shared(300).unwrap()
    second = mm.allocate_shared(300).unwrap()
    third = mm.allocate_shared(300).unwrap()
    assert first.page_idx != second.page_idx
    assert second.page_idx != third.page_idx
    assert first.page_idx != third.page_idx
    assert first.base_address == FB_CONF_SHM_SIM_BASE
    assert second.base_address == FB_CONF_SHM_SIM_BASE + 300
    assert third.base_address == FB_CONF_SHM_SIM_BASE + 600
    assert mm.shm_allocated_bytes == 900
    assert mm.total_allocated_bytes == 900

    first.write_u8(0, 0xA5)
    second.write_u8(0, 0x5A)
    assert mm.shm_storage[0] == 0xA5
    assert mm.shm_storage[300] == 0x5A

    first_virtual = 0xE000_0000 + first.page_idx * FB_PAGE_SIZE
    status, physical_addr = vmmio.access(first_virtual + 299, is_write=False)
    assert status == VmmioStatus.OK_PHYSICAL
    assert physical_addr == first.base_address + 299
    status, _ = vmmio.access(first_virtual + 300, is_write=False)
    assert status == VmmioStatus.OUT_OF_BOUNDS

    exhausted = mm.allocate_shared(FB_CONF_SHM_SIZE - 900 + 1)
    assert exhausted.is_err
    first.drop()
    reused = mm.allocate_shared(256).unwrap()
    assert reused.base_address == FB_CONF_SHM_SIM_BASE
    assert mm.shm_allocated_bytes == 856


# ===========================================================================
# 4. HAL & UART / Timer (hal_dispatch_test_spec.md / platform_driver_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_mem_01_acquire_task_heap_fixed_size()
    test_mem_02_recovery_strategy_on_exhaustion()
    test_mem_03_total_allocation_bound()
    test_mem_04_owner_task_id_auto_set()
    test_mem_05_release_and_deallocate_owner_only()
    test_mem_06_guest_ram_64kb_alignment()
    test_mem_10_shared_block_ownership_transfer()
    test_mem_10c_rollback_transfer_restores_owner_id()
    test_mem_11_shared_block_raII_auto_deallocate()
    test_mem_14_page_granular_permission_isolation()
    test_mem_15_vmmio_fc14_tlb_sync()
    test_mem_16_virtual_page_reservation_is_independent_of_shm_backing()
    print("[PASS] All physical-memory software-contract tests passed.")
