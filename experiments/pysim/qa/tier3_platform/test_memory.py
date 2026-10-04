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


from memory_interface import PageMappingCallbacks
from qa.shared.helpers import expect_assertion
from scheduler import Scheduler
from tier2_runtime.memory.manager import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_TASK_HEAP_SIZES,
    FB_TASK_ID_FLIGHT,
    MemoryErrorCode,
    MemoryManager,
    MemoryReasonCode,
    RecoveryAction,
)
from tier2_runtime.vmmio.controller import (
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


def test_mem_08_claim_rejection_preserves_live_storage():
    """TEST-MEM-08: Invalid and dropped IDs fail without changing live storage or ownership."""
    for dropped_id in (False, True):
        manager, scheduler = _make_memory_manager(1, 2)
        assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
        block = manager.allocate_shared(size=64).unwrap()
        contents = bytes(range(64))
        block.write_bytes(0, memoryview(contents))
        if dropped_id:
            dropped = manager.allocate_shared(size=32).unwrap()
            rejected_id = dropped.shm_id
            dropped.drop()
        else:
            rejected_id = 0x9999
        slot = manager.shm_slots.view().find(block.shm_id)
        assert slot is not None
        slot_before = (slot.owner, slot.allocated, slot.size, slot.base_address)
        pages_before = tuple(
            (page.owner_id, page.allocated, page.allocated_bytes, page.physical_addr)
            for page in manager.shm_pages
        )
        registry_before = tuple(
            (
                manager.page_registry.get_owner(page.page_idx),
                manager.page_registry.get_generation(page.page_idx),
            )
            for page in manager.shm_pages
        )
        storage_before = bytes(manager.shm_storage)
        counts_before = (manager.total_allocated_bytes, manager.shm_allocated_bytes)
        scheduler.current_task = scheduler.get_task(2)

        result = manager.claim(rejected_id)

        assert result.is_err and result.value is None
        assert result.error is not None
        assert result.error.error_code == MemoryErrorCode.INVALID_SHM_ID
        assert result.error.recovery.action == RecoveryAction.RETRY
        assert result.error.recovery.reason_code == MemoryReasonCode.INVALID_OR_DEALLOCATED_SHM_ID
        assert manager.shm_slots.view().find(rejected_id) is None
        assert tuple(manager.shm_slots.view().keys) == (block.shm_id,)
        assert manager.shm_slots.view().find(block.shm_id) is slot
        assert (slot.owner, slot.allocated, slot.size, slot.base_address) == slot_before
        assert (
            tuple(
                (page.owner_id, page.allocated, page.allocated_bytes, page.physical_addr)
                for page in manager.shm_pages
            )
            == pages_before
        )
        assert (
            tuple(
                (
                    manager.page_registry.get_owner(page.page_idx),
                    manager.page_registry.get_generation(page.page_idx),
                )
                for page in manager.shm_pages
            )
            == registry_before
        )
        assert bytes(manager.shm_storage) == storage_before
        assert (manager.total_allocated_bytes, manager.shm_allocated_bytes) == counts_before
        scheduler.current_task = scheduler.get_task(1)
        assert block.read_bytes(0, 64) == contents
        address = block.get_address()
        valid_id = block.release()
        scheduler.current_task = scheduler.get_task(2)
        assert manager.grant_shared(valid_id)
        claimed = manager.claim(valid_id).unwrap()
        assert claimed.get_owner() == 2
        assert claimed.get_address() == address
        assert claimed.read_bytes(0, 64) == contents
        claimed.drop()


def test_mem_10d_owner_notifications_match_each_lifecycle_boundary():
    """TEST-MEM-10d (Tier 1): Notify old/new owners once, and map only reusable owner views."""
    manager, scheduler = _make_memory_manager(1, 2)
    assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    mapped: list[tuple[int, int, int, int]] = []
    changed: list[tuple[int, int, int, int]] = []
    unmapped: list[tuple[int, int]] = []

    def on_map(page_idx: int, address: int, owner_id: int, size: int) -> None:
        assert manager.page_registry.get_owner(page_idx) == owner_id
        mapped.append((page_idx, address, owner_id, size))

    def on_changed(page_idx: int, address: int, previous_id: int, new_id: int) -> None:
        # The contract requires notification after the owner ledger is updated.
        assert manager.page_registry.get_owner(page_idx) == new_id
        changed.append((page_idx, address, previous_id, new_id))

    def on_unmap(page_idx: int, address: int) -> None:
        unmapped.append((page_idx, address))

    manager.register_page_mapping_callbacks(PageMappingCallbacks(on_map, on_changed, on_unmap))
    block = manager.allocate_shared(size=64).unwrap()
    block.write_bytes(0, memoryview(bytes(range(64))))
    page_idx, address = block.page_idx, block.get_address()
    assert mapped == [(page_idx, address, 1, 64)]
    assert changed == [] and unmapped == []

    shm_id = block.release()
    assert changed == [(page_idx, address, 1, FB_TASK_ID_FLIGHT)]
    assert mapped == [(page_idx, address, 1, 64)]
    scheduler.current_task = scheduler.get_task(2)
    assert manager.grant_shared(shm_id)
    expected_changes = [
        (page_idx, address, 1, FB_TASK_ID_FLIGHT),
        (page_idx, address, FB_TASK_ID_FLIGHT, 2),
    ]
    assert changed == expected_changes
    assert mapped == [(page_idx, address, 1, 64)]
    assert manager.grant_shared(shm_id)
    assert changed == expected_changes, "unchanged owners must not emit a change notification"
    received = manager.claim(shm_id).unwrap()
    assert received.read_bytes(0, 64) == bytes(range(64))
    assert changed == expected_changes
    assert mapped == [(page_idx, address, 1, 64), (page_idx, address, 2, 64)]

    assert received.release() == shm_id
    expected_changes.append((page_idx, address, 2, FB_TASK_ID_FLIGHT))
    assert changed == expected_changes
    assert mapped == [(page_idx, address, 1, 64), (page_idx, address, 2, 64)]
    manager.rollback_transfer(shm_id)
    expected_changes.append((page_idx, address, FB_TASK_ID_FLIGHT, 2))
    assert changed == expected_changes
    assert mapped == [
        (page_idx, address, 1, 64),
        (page_idx, address, 2, 64),
        (page_idx, address, 2, 64),
    ]
    restored = manager.claim(shm_id).unwrap()
    assert restored.get_owner() == 2
    assert restored.get_address() == address
    assert restored.read_bytes(0, 64) == bytes(range(64))
    assert changed == expected_changes
    assert mapped == [(page_idx, address, 1, 64)] + [(page_idx, address, 2, 64)] * 3
    restored.drop()
    assert unmapped == [(page_idx, address)]
    assert changed == expected_changes
    assert manager.page_registry.get_owner(page_idx) is None


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
    assert raw_ba.readonly
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
    with expect_assertion("inactive or in-flight"):
        sb_a.read_u8(0)


def test_mem_10_entry_writer_matches_wire_layout() -> None:
    """TEST-IPCR-27: writerを規定の全byte列で検査し、隣接領域の保存も確認する。"""
    mm, _ = _make_memory_manager(1)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    block = mm.allocate_shared(size=32).unwrap()
    block.write_bytes(0, bytes.fromhex("a5") * 32)
    block.write_entry(1, key=0x12345678, val=0x90ABCDEF)
    assert block.read_u64(1) == 0x1234567890ABCDEF
    assert (
        block.read_bytes(0, 32)
        == bytes.fromhex("a5") * 8
        + bytes.fromhex("ef cd ab 90 78 56 34 12")
        + bytes.fromhex("a5") * 16
    )


def test_mem_10_entry_reader_decodes_independent_wire_layout() -> None:
    """TEST-IPCR-27: 製品writerを使わず、規定byte列を直接投入してreaderを検査する。"""
    mm, _ = _make_memory_manager(1)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    block = mm.allocate_shared(size=32).unwrap()
    wire = (
        bytes.fromhex("a5") * 16
        + bytes.fromhex("21 43 65 87 98 ba dc fe")
        + bytes.fromhex("a5") * 8
    )
    block.write_bytes(0, wire)
    assert block.read_entry(2) == (0xFEDCBA98, 0x87654321)
    assert block.read_bytes(0, 32) == wire


def test_mem_10d_resource_revoke_invalidates_old_handle():
    """TEST-MEM-10d: RESOURCE revoke permanently invalidates sender capabilities."""
    mm, scheduler = _make_memory_manager(1, 2)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sender_block = mm.allocate_shared(size=32).unwrap()
    sender_block.write_bytes(0, b"resource payload")
    snapshot = sender_block.get_bytearray()
    shm_id = sender_block.shm_id

    assert mm.revoke_shared(shm_id)
    with expect_assertion("revoked or transferred"):
        sender_block.read_u8(0)
    with expect_assertion("revoked or transferred"):
        sender_block.get_bytearray()
    assert snapshot.tobytes() == b"resource payload" + b"\x00" * 16

    scheduler.current_task = scheduler.get_task(2)
    assert scheduler.current_task is not None
    assert mm.grant_shared(shm_id)
    receiver_block = mm.claim(shm_id).unwrap()
    assert receiver_block.read_bytes(0, 16) == b"resource payload"
    scheduler.current_task = scheduler.get_task(1)
    with expect_assertion("revoked or transferred"):
        sender_block.write_u8(0, 0)
    scheduler.current_task = scheduler.get_task(2)
    assert receiver_block.read_bytes(0, 16) == b"resource payload"


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
    from tier2_runtime.memory.manager import FB_CONF_SHM_SIM_BASE, FB_CONF_SHM_SIZE, FB_PAGE_SIZE

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


def test_reacquire_released_partition_does_not_overlap_live_task(monkeypatch) -> None:
    """TEST-MEM-01/05: 固定2スロット構成で対象heapだけを返却・再初期化する。"""
    import tier2_runtime.memory.manager as memory_module

    # Select a compile-time configuration in the test, without changing the
    # product default of one VM or adding a runtime configuration backdoor.
    monkeypatch.setattr(memory_module, "FB_CONF_TASK_HEAP_SIZES", (4096, 4096))
    manager, scheduler = _make_memory_manager(1, 2)
    assert manager.init_manager(0x10000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    target = scheduler.get_task(1)
    protected = scheduler.get_task(2)
    assert target is not None and protected is not None
    with scheduler.task_context(target):
        original = manager.acquire_task_heap().unwrap()
        original.data[:] = b"\xa5" * original.size
    with scheduler.task_context(protected):
        other = manager.acquire_task_heap().unwrap()
        other.data[:] = b"\x5a" * other.size
    other_before = bytes(other.data)
    other_before_metadata = (other.owner, other.base_address, other.size)
    for _ in range(4):
        with scheduler.task_context(target):
            manager.release_task_heap()
            assert manager.partition_owners.view().find(protected.task_id) is other
            replacement = manager.acquire_task_heap().unwrap()
            assert replacement.base_address == original.base_address
            assert replacement.base_address + replacement.size <= other.base_address
            assert replacement.owner == target.task_id
            assert bytes(replacement.data) == bytes(replacement.size)
            replacement.data[0] = 0xA5
        assert bytes(other.data) == other_before
        assert (other.owner, other.base_address, other.size) == other_before_metadata
        assert manager.partition_owners.view().find(protected.task_id) is other
        assert manager.total_allocated_bytes == original.size + other.size


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
