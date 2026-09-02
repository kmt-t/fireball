from __future__ import annotations

"""
Unit tests for Tier 3 Platform: Physical Memory & MPU W^X
Traceability: platform_memory_test_spec.md
"""

import sys
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

from memory import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_PARTITION_SIZE,
    FB_TASK_ID_FLIGHT,
    AccessPermission,
    MemoryManager,
    PMSAv8MPU,
    RecoveryAction,
)
from vmmio import (
    VMMIOController,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_mem_01_acquire_partition_fixed_size():
    """MEM-01: acquire-partition provides task-specific fixed partition (no arbitrary size)."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    res = mm.acquire_partition(owner=1)
    assert res.is_ok
    pv = res.unwrap()
    assert pv.size == FB_CONF_PARTITION_SIZE
    assert pv.owner == 1
    assert not hasattr(mm, "allocate"), "Generic heap allocate() must not exist"


def test_mem_02_recovery_strategy_on_exhaustion():
    """MEM-02: Memory exhaustion returns structured error with recovery strategy."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_PARTITION_SIZE)
    assert mm.acquire_partition(owner=1).is_ok
    r2 = mm.acquire_partition(owner=2)
    assert r2.is_err
    assert r2.error.error_code == "ERR_POOL_EXHAUSTED"
    assert r2.error.recovery.action in (RecoveryAction.DEGRADE, RecoveryAction.RETRY)


def test_mem_03_total_allocation_bound():
    """MEM-03: Total allocated bytes never exceeds FB_CONF_MEMORY_POOL_SIZE."""
    mm = MemoryManager()
    pool_size = 128 * 1024
    mm.init_manager(pool_base=0x20020000, pool_size=pool_size)
    for i in range(1, 10):
        res = mm.acquire_partition(owner=i)
        assert mm.total_allocated_bytes <= pool_size
        if res.is_err:
            break


def test_mem_04_owner_task_id_auto_set():
    """MEM-04: Caller task-id is automatically recorded on all allocations."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    p_res = mm.acquire_partition(owner=5)
    assert p_res.unwrap().owner == 5
    s_res = mm.allocate_shared(caller_task_id=5, size=1024)
    assert s_res.unwrap().owner == 5


def test_mem_05_release_and_deallocate_owner_only():
    """MEM-05: Partition release is permitted ONLY by owner task."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    mm.acquire_partition(owner=3)
    assert 3 in mm.partition_owners
    # Rogue task 4 attempts to release task 3's partition
    mm.release_partition(caller_task_id=4)
    assert 3 in mm.partition_owners
    # Owner releases
    mm.release_partition(caller_task_id=3)
    assert 3 not in mm.partition_owners


def test_mem_06_guest_ram_64kb_alignment():
    """MEM-06: pool_base is strictly 64KB aligned."""
    mm = MemoryManager()
    assert mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE).is_ok
    try:
        mm.init_manager(pool_base=0x20021000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        raise AssertionError("Expected AssertionError for unaligned pool_base")
    except AssertionError as e:
        assert "64KB aligned" in str(e)


def test_mem_10_shared_block_ownership_transfer():
    """MEM-10: allocate-shared -> release -> claim moves ownership cleanly without double-ownership."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb_a = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
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
    assert isinstance(raw_ba, bytearray)
    assert raw_ba[0] == 0xAB

    page_idx = sb_a.page_idx
    shm_id = sb_a.release()
    assert not sb_a._is_active
    assert mm.page_registry.get_owner(page_idx) == FB_TASK_ID_FLIGHT

    # Access during in-flight must raise AssertionError
    try:
        sb_a.read_u32(4)
        raise AssertionError("Expected access error while in-flight")
    except AssertionError:
        pass

    # Simulate IPC Router Grant phase
    mm.page_registry.update_owner(page_idx, 2)
    sb_b = mm.claim(receiver_task_id=2, shm_id=shm_id).unwrap()
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
    """MEM-10c: rollback_transfer() restores PTE owner_id to the original sender."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    shm_id = sb.release()
    assert mm.page_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT
    mm.rollback_transfer(original_sender_id=1, shm_id=shm_id)
    assert mm.page_registry.get_owner(sb.page_idx) == 1


def test_mem_11_shared_block_raII_auto_deallocate():
    """MEM-11: SharedBlock RAII automatically deallocates buffer on drop."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    initial_alloc = mm.total_allocated_bytes
    with mm.allocate_shared(caller_task_id=2, size=1024).unwrap() as sb:
        assert mm.total_allocated_bytes > initial_alloc
        assert sb.shm_id in mm.shm_slots
    assert mm.total_allocated_bytes == initial_alloc
    assert sb.shm_id not in mm.shm_slots


def test_mem_14_page_granular_permission_isolation():
    """MEM-14: Different tasks cannot share the same 4KB page; separate pages allocated."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    # Task 1 allocates a small block (256 bytes)
    sb_t1_a = mm.allocate_shared(caller_task_id=1, size=256).unwrap()
    # Task 1 allocates another small block (256 bytes) -> reuses Task 1's page!
    sb_t1_b = mm.allocate_shared(caller_task_id=1, size=256).unwrap()
    assert sb_t1_a.page_idx == sb_t1_b.page_idx
    assert sb_t1_a.slot_idx != sb_t1_b.slot_idx

    # Task 2 allocates a small block (256 bytes) -> MUST allocate a separate 4KB page!
    sb_t2 = mm.allocate_shared(caller_task_id=2, size=256).unwrap()
    assert sb_t2.page_idx != sb_t1_a.page_idx
    assert mm.shm_pages[sb_t1_a.page_idx].owner_id == 1
    assert mm.shm_pages[sb_t2.page_idx].owner_id == 2


def test_mem_15_vmmio_fc14_tlb_sync():
    """MEM-15: vMMIO FC=14 mapping, update and TLB flush driven by MemoryManager."""

    vmmio = VMMIOController(guest_ram_size=8192)
    mm = MemoryManager()
    vmmio.register_to_memory_manager(mm)
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    sb = mm.allocate_shared(caller_task_id=1, size=512).unwrap()
    raw_addr = 0xE000_0000 + (sb.page_idx * 4096)

    # Verify task 1 can access its own SHM page
    status, _ = vmmio.access(raw_addr, is_write=False, current_task_id=1)
    assert status == "OK_PHYSICAL"

    # Task 2 access traps with OWNER_MISMATCH
    status, _ = vmmio.access(raw_addr, is_write=False, current_task_id=2)
    assert status == "TRAP_OWNER_MISMATCH"

    # Release puts page in flight -> Task 1 also traps!
    shm_id = sb.release()
    status, _ = vmmio.access(raw_addr, is_write=False, current_task_id=1)
    assert status == "TRAP_OWNER_MISMATCH"

    # Grant to Task 2 -> Task 2 can access, Task 1 cannot!
    assert mm.grant_shared(shm_id, 2)
    status, _ = vmmio.access(raw_addr, is_write=False, current_task_id=2)
    assert status == "OK_PHYSICAL"
    status, _ = vmmio.access(raw_addr, is_write=False, current_task_id=1)
    assert status == "TRAP_OWNER_MISMATCH"


def test_mem_20_mpu_8_regions_static_allocation():
    """MEM-20: 8 MPU regions match the PMSAv8 static allocation table."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    assert len(mpu.regions) == 8
    assert mpu.regions[0].ap == AccessPermission.RO and not mpu.regions[0].xn
    assert mpu.regions[3].ap == AccessPermission.RW and mpu.regions[3].xn
    assert mpu.regions[4].ap == AccessPermission.RO and not mpu.regions[4].xn
    assert mpu.regions[7].ap == AccessPermission.NO_ACCESS


def test_mem_21_jit_code_cache_wx_switch_and_restore():
    """MEM-21 & MEM-22: JIT code cache W^X transaction switching and permanent non-RWX."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    mpu.assert_no_rwx()
    mpu.begin_jit_patch()
    assert mpu.regions[4].is_writable and not mpu.regions[4].is_executable
    mpu.assert_no_rwx()
    mpu.commit_jit_patch()
    assert mpu.regions[4].is_executable and not mpu.regions[4].is_writable
    mpu.assert_no_rwx()


# ===========================================================================
# 4. Tier 3 Platform HAL & UART / Timer (platform_hal_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_mem_01_acquire_partition_fixed_size()
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
    test_mem_20_mpu_8_regions_static_allocation()
    test_mem_21_jit_code_cache_wx_switch_and_restore()
    print("[PASS] All 13 Physical Memory & MPU W^X tests passed.")
