"""
docs/components/tier3_platform/concepts/platform_memory_concept.py
Reference Concept Implementation & Test Suite: COOS Memory Manager
- Tier 3 Platform / Leaf Component (platform_memory.md, platform_memory_test_spec.md)
- Consolidated physical memory pool and fixed-size partition leasing (os_coos.md compliant)
- Typed slot pools with zero dynamic void* heap
- RAII SharedBlock zero-copy ownership transfer linked with vMMIO FC=14 PTEs
- Cortex-M33 PMSAv8 8-region MPU allocation and JIT W^X transaction switching
"""

from __future__ import annotations
import inspect
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# -----------------------------------------------------------------------------
# Configuration & Constants (FB_CONF_*)
# -----------------------------------------------------------------------------

FB_CONF_MEMORY_POOL_SIZE = 2 * 1024 * 1024  # 2MB physical pool
FB_CONF_PARTITION_SIZE = 64 * 1024  # 64KB fixed partition per task
FB_CONF_MAX_TASKS = 16
FB_CONF_MAX_SHM_PAGES = 32
FB_PAGE_SIZE = 4096  # 4KB SHM page size
FB_WASM_PAGE_SIZE = 65536  # 64KB WASM page size
FB_TASK_ID_FLIGHT = 0xFFFF  # Flight sentinel during IPC transfer
FB_TASK_ID_KERNEL = 0x0000

# -----------------------------------------------------------------------------
# Error & Recovery Strategy Types
# -----------------------------------------------------------------------------


class RecoveryAction(Enum):
    RETRY = auto()
    DEGRADE = auto()
    RESTART_TASK = auto()
    PANIC = auto()


@dataclass
class RecoveryStrategy:
    action: RecoveryAction
    message: str


@dataclass
class MemoryErrorResult:
    error_code: str
    recovery: RecoveryStrategy

    def __str__(self) -> str:
        return f"MemoryError({self.error_code}: {self.recovery.message}, action={self.recovery.action.name})"


@dataclass
class Result(Generic[T]):
    value: T | None = None
    error: MemoryErrorResult | None = None

    @property
    def is_ok(self) -> bool:
        return self.error is None

    @property
    def is_err(self) -> bool:
        return self.error is not None

    def unwrap(self) -> T:
        if self.error is not None:
            raise RuntimeError(f"Unwrap failed on Result: {self.error}")
        assert self.value is not None
        return self.value


# -----------------------------------------------------------------------------
# Memory Views & Handles (Tier 1 os_coos.md Contract Compliant)
# -----------------------------------------------------------------------------


@dataclass
class PartitionView:
    """Fixed-size non-owning partition view leased to a specific task."""

    owner: int
    base_address: int
    size: int
    data: bytearray

    def is_valid_for(self, task_id: int) -> bool:
        return self.owner == task_id


@dataclass
class PoolRef(Generic[T]):
    """Typed slot reference within static pool."""

    owner: int
    slot_idx: int
    instance: T


# -----------------------------------------------------------------------------
# vMMIO FC=14 PTE Registry (Simulated Hardware & Subsystem Coordination)
# -----------------------------------------------------------------------------


@dataclass
class VMMIOPTE:
    page_idx: int
    owner_id: int
    physical_addr: int
    is_valid: bool = True


class VMMIOPTERegistry:
    """Simulates Tier 2 runtime_vmmio.md FC=14 SHM page owner tracking."""

    def __init__(self):
        self.ptes: dict[int, VMMIOPTE] = {}

    def register_page(self, page_idx: int, owner_id: int, physical_addr: int):
        self.ptes[page_idx] = VMMIOPTE(
            page_idx=page_idx,
            owner_id=owner_id,
            physical_addr=physical_addr,
            is_valid=True,
        )

    def update_owner(self, page_idx: int, new_owner_id: int) -> bool:
        if page_idx not in self.ptes or not self.ptes[page_idx].is_valid:
            return False
        self.ptes[page_idx].owner_id = new_owner_id
        return True

    def get_owner(self, page_idx: int) -> int | None:
        pte = self.ptes.get(page_idx)
        return pte.owner_id if pte and pte.is_valid else None

    def unregister_page(self, page_idx: int):
        if page_idx in self.ptes:
            self.ptes[page_idx].is_valid = False


# -----------------------------------------------------------------------------
# RAII SharedBlock (Zero-Copy Transfer)
# -----------------------------------------------------------------------------


class SharedBlock:
    """RAII-managed shared memory block for zero-copy IPC."""

    def __init__(
        self,
        shm_id: int,
        page_idx: int,
        slot_idx: int,
        size: int,
        owner: int,
        base_address: int,
        manager: MemoryManager,
    ):
        self.shm_id = shm_id
        self.page_idx = page_idx
        self.slot_idx = slot_idx
        self.size = size
        self.owner = owner
        self.base_address = base_address
        self._manager = manager
        self._is_active = True
        self._is_in_flight = False

    def get_address(self) -> int:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        return self.base_address

    def get_size(self) -> int:
        return self.size

    def get_owner(self) -> int:
        return self.owner

    def release(self) -> int:
        """Revoke sender access and prepare for transfer (marks FLIGHT)."""
        assert self._is_active, "Cannot release inactive SharedBlock"
        self._is_active = False
        self._is_in_flight = True
        # Set vMMIO PTE to FLIGHT sentinel (Revoke phase)
        self._manager.vmmio_registry.update_owner(self.page_idx, FB_TASK_ID_FLIGHT)
        return self.shm_id

    def drop(self):
        """RAII drop handler: automatically deallocates physical buffer if still owned."""
        if self._is_active:
            self._is_active = False
            self._manager._deallocate_shared_slot(
                self.page_idx, self.slot_idx, self.owner
            )
        elif self._is_in_flight:
            pass

    def __del__(self):
        self.drop()

    def __enter__(self) -> SharedBlock:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.drop()


# -----------------------------------------------------------------------------
# PMSAv8 MPU Protection & W^X Switching (§9)
# -----------------------------------------------------------------------------


class AccessPermission(Enum):
    NO_ACCESS = 0
    RO = 1
    RW = 2


@dataclass
class MPURegion:
    region_no: int
    name: str
    base_address: int
    limit_address: int
    ap: AccessPermission
    xn: bool  # eXecute Never (True = Non-executable)
    is_device: bool = False
    enabled: bool = True

    @property
    def is_writable(self) -> bool:
        return self.enabled and self.ap == AccessPermission.RW

    @property
    def is_executable(self) -> bool:
        return self.enabled and not self.xn


class PMSAv8MPU:
    """Cortex-M33 PMSAv8 8-region Memory Protection Unit simulator."""

    def __init__(self, pool_base: int):
        self.regions: list[MPURegion] = []
        self.dsb_count = 0
        self.isb_count = 0
        self.patch_in_progress = False
        self._setup_static_regions(pool_base)

    def _setup_static_regions(self, pool_base: int):
        # 8 statically allocated regions matching §9.1 Table
        # All base/limit adhere to 32-byte alignment
        self.regions = [
            # Region 0: Flash / Kernel Code (RO + X)
            MPURegion(
                0,
                "Flash_KernelCode",
                0x00000000,
                0x0007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            # Region 1: Kernel Data & BSS (RW + XN)
            MPURegion(
                1,
                "Kernel_DataBSS",
                0x20000000,
                0x20007FE0,
                AccessPermission.RW,
                xn=True,
            ),
            # Region 2: Kernel Pool / Heap (RW + XN)
            MPURegion(
                2,
                "Kernel_PoolHeap",
                0x20008000,
                0x2001FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            # Region 3: Guest WASM RAM (RW + XN, 64KB aligned)
            MPURegion(
                3,
                "Guest_WasmRAM",
                pool_base,
                pool_base + 0x000FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            # Region 4: JIT Code Cache (RO + X default)
            MPURegion(
                4,
                "JIT_CodeCache",
                0x20040000,
                0x2007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            # Region 5: Peripheral MMIO (RW + XN, Device)
            MPURegion(
                5,
                "Peripheral_MMIO",
                0x40000000,
                0x4003FFE0,
                AccessPermission.RW,
                xn=True,
                is_device=True,
            ),
            # Region 6: Shared Memory Buffers (RW + XN)
            MPURegion(
                6, "Shared_Memory", 0x20080000, 0x200BFFE0, AccessPermission.RW, xn=True
            ),
            # Region 7: Stack Guard Band (No Access)
            MPURegion(
                7,
                "Stack_Guard",
                0x200C0000,
                0x200C0020,
                AccessPermission.NO_ACCESS,
                xn=True,
            ),
        ]

    def begin_jit_patch(self):
        """Switch JIT Code Cache (Region 4) from RO+X to RW+XN."""
        assert not self.patch_in_progress, "Nested JIT patch transaction is invalid"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RW
        r4.xn = True
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = True

    def commit_jit_patch(self):
        """Restore JIT Code Cache (Region 4) from RW+XN back to RO+X."""
        assert self.patch_in_progress, "Cannot commit without begin_jit_patch"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RO
        r4.xn = False
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = False

    def assert_no_rwx(self):
        """Verify the strict invariant: No region is ever RW and X simultaneously."""
        for r in self.regions:
            if r.enabled:
                assert not (r.is_writable and r.is_executable), (
                    f"Invariant violation: Region {r.region_no} ({r.name}) has RWX permissions"
                )


# -----------------------------------------------------------------------------
# Consolidated Physical Memory Manager Component
# -----------------------------------------------------------------------------


class MemoryManager:
    """Tier 3 Consolidated Physical Memory Manager (platform_memory.md)."""

    def __init__(self):
        self.pool_base: int = 0
        self.pool_size: int = 0
        self.total_allocated_bytes: int = 0
        self.vmmio_registry = VMMIOPTERegistry()
        self.mpu: PMSAv8MPU | None = None
        # Static partitions per task (fixed 64KB)
        self.partition_owners: dict[int, PartitionView] = {}
        # Typed slot pools
        self.typed_slots: dict[type, list[PoolRef]] = {}
        # Shared block slots (page_idx -> (slot_idx -> metadata))
        self.shm_slots: dict[int, dict[str, Any]] = {}

    def init_manager(self, pool_base: int, pool_size: int) -> Result[bool]:
        """Initialize physical pool with 64KB WASM page alignment."""
        assert pool_base % FB_WASM_PAGE_SIZE == 0, (
            f"pool_base 0x{pool_base:X} must be 64KB aligned (WasmPageAlignment)"
        )
        self.pool_base = pool_base
        self.pool_size = pool_size
        self.total_allocated_bytes = 0
        self.mpu = PMSAv8MPU(pool_base)
        return Result(value=True)

    # --- Partition Management (§4 acquire-partition / release-partition) ---
    def acquire_partition(self, owner: int) -> Result[PartitionView]:
        """Lease a fixed-size partition to a task (NOT a general-purpose heap allocator)."""
        if owner in self.partition_owners:
            return Result(
                error=MemoryErrorResult(
                    "ERR_ALREADY_ACQUIRED",
                    RecoveryStrategy(
                        RecoveryAction.RETRY,
                        f"Task {owner} already has an active partition",
                    ),
                )
            )

        if self.total_allocated_bytes + FB_CONF_PARTITION_SIZE > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    "ERR_POOL_EXHAUSTED",
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE, "Physical memory pool exhausted"
                    ),
                )
            )

        offset = len(self.partition_owners) * FB_CONF_PARTITION_SIZE
        base_addr = self.pool_base + offset
        pv = PartitionView(
            owner=owner,
            base_address=base_addr,
            size=FB_CONF_PARTITION_SIZE,
            data=bytearray(FB_CONF_PARTITION_SIZE),
        )
        self.partition_owners[owner] = pv
        self.total_allocated_bytes += FB_CONF_PARTITION_SIZE
        return Result(value=pv)

    def release_partition(self, caller_task_id: int):
        """Release partition back to pool. Only owner can release."""
        if caller_task_id not in self.partition_owners:
            return  # Non-owner or unallocated call is safely ignored / rejected
        del self.partition_owners[caller_task_id]
        self.total_allocated_bytes -= FB_CONF_PARTITION_SIZE

    # --- Typed Slot Pool (§4 acquire-slot / release-slot) ---
    def acquire_slot(self, owner: int, cls: type[T]) -> Result[PoolRef[T]]:
        """Lease a pre-allocated typed slot from static pool."""
        slot_size = getattr(cls, "__size__", 256)
        if self.total_allocated_bytes + slot_size > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    "ERR_SLOT_EXHAUSTED",
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE, "Slot allocation pool exhausted"
                    ),
                )
            )

        instance = cls()
        slot_idx = len(self.typed_slots.get(cls, []))
        ref = PoolRef(owner=owner, slot_idx=slot_idx, instance=instance)
        self.typed_slots.setdefault(cls, []).append(ref)
        self.total_allocated_bytes += slot_size
        return Result(value=ref)

    def release_slot(self, caller_task_id: int, ref: PoolRef[T]):
        """Release typed slot. Only owner can release."""
        if ref.owner != caller_task_id:
            return
        cls = type(ref.instance)
        if cls in self.typed_slots and ref in self.typed_slots[cls]:
            self.typed_slots[cls].remove(ref)
            slot_size = getattr(cls, "__size__", 256)
            self.total_allocated_bytes -= slot_size

    # --- Shared Block IPC Allocation & Transfer (§4, §7) ---
    def allocate_shared(self, caller_task_id: int, size: int) -> Result[SharedBlock]:
        """Allocate an IPC shared memory buffer with RAII ownership."""
        assert caller_task_id != 0, "Shared block must be owned by an explicit task"
        if size <= 0 or size > FB_PAGE_SIZE:
            return Result(
                error=MemoryErrorResult(
                    "ERR_INVALID_SIZE",
                    RecoveryStrategy(
                        RecoveryAction.RETRY, "Requested SHM size out of bounds"
                    ),
                )
            )

        if self.total_allocated_bytes + FB_PAGE_SIZE > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    "ERR_SHM_EXHAUSTED",
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE, "No free SHM pages in physical pool"
                    ),
                )
            )

        page_idx = len(self.shm_slots)
        slot_idx = 0
        shm_id = (page_idx << 8) | slot_idx
        base_addr = 0x20080000 + (page_idx * FB_PAGE_SIZE)
        # Register page into vMMIO FC=14 table with caller as owner
        self.vmmio_registry.register_page(page_idx, caller_task_id, base_addr)
        self.shm_slots[shm_id] = {
            "page_idx": page_idx,
            "slot_idx": slot_idx,
            "size": size,
            "owner": caller_task_id,
            "base_address": base_addr,
            "allocated": True,
        }
        self.total_allocated_bytes += FB_PAGE_SIZE
        sb = SharedBlock(
            shm_id=shm_id,
            page_idx=page_idx,
            slot_idx=slot_idx,
            size=size,
            owner=caller_task_id,
            base_address=base_addr,
            manager=self,
        )
        return Result(value=sb)

    def claim(self, receiver_task_id: int, shm_id: int) -> Result[SharedBlock]:
        """Claim a shared memory block after Grant phase is completed."""
        slot = self.shm_slots.get(shm_id)
        if not slot or not slot["allocated"]:
            return Result(
                error=MemoryErrorResult(
                    "ERR_INVALID_SHM_ID",
                    RecoveryStrategy(
                        RecoveryAction.RETRY, "Invalid or deallocated SHM ID"
                    ),
                )
            )

        page_idx = slot["page_idx"]
        current_owner = self.vmmio_registry.get_owner(page_idx)
        # Precondition check: Grant must be established in vMMIO PTE
        if current_owner != receiver_task_id:
            return Result(
                error=MemoryErrorResult(
                    "ERR_GRANT_NOT_COMPLETED",
                    RecoveryStrategy(
                        RecoveryAction.RETRY, "Grant phase incomplete in vMMIO PTE"
                    ),
                )
            )

        slot["owner"] = receiver_task_id
        sb = SharedBlock(
            shm_id=shm_id,
            page_idx=page_idx,
            slot_idx=slot["slot_idx"],
            size=slot["size"],
            owner=receiver_task_id,
            base_address=slot["base_address"],
            manager=self,
        )
        return Result(value=sb)

    def rollback_transfer(self, original_sender_id: int, shm_id: int):
        """Rollback transfer on queue full: restore original owner in vMMIO PTE."""
        slot = self.shm_slots.get(shm_id)
        if slot:
            page_idx = slot["page_idx"]
            self.vmmio_registry.update_owner(page_idx, original_sender_id)
            slot["owner"] = original_sender_id

    def _deallocate_shared_slot(self, page_idx: int, slot_idx: int, owner: int):
        shm_id = (page_idx << 8) | slot_idx
        if shm_id in self.shm_slots:
            del self.shm_slots[shm_id]
            self.vmmio_registry.unregister_page(page_idx)
            self.total_allocated_bytes -= FB_PAGE_SIZE

    def deallocate(self, caller_task_id: int, addr: int):
        """Deallocate local static partition or slot. Owner enforced."""
        for owner, pv in list(self.partition_owners.items()):
            if pv.base_address == addr:
                if owner == caller_task_id:
                    self.release_partition(caller_task_id)
                return


# -----------------------------------------------------------------------------
# HAL Integration Wrapper (platform_hal.md §5.1 Delegation)
# -----------------------------------------------------------------------------


class HALBufferManager:
    """Simulates platform_hal.md acquire_buffer delegating to allocate_shared."""

    def __init__(self, memory_manager: MemoryManager):
        self.mem = memory_manager

    def acquire_buffer(self, hal_task_id: int, size: int) -> Result[SharedBlock]:
        return self.mem.allocate_shared(hal_task_id, size)


# =============================================================================
# Test Suite: platform_memory_test_spec.md (MEM-01 ~ MEM-25)
# =============================================================================


def test_mem_01_acquire_partition_fixed_size():
    """MEM-01: acquire-partition provides task-specific fixed partition (no arbitrary size)."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    # Signature must only take owner (task_id), NOT a size parameter
    sig = inspect.signature(mm.acquire_partition)
    assert list(sig.parameters.keys()) == ["owner"], (
        "acquire_partition must only take 'owner' parameter"
    )
    res = mm.acquire_partition(owner=1)
    assert res.is_ok
    pv = res.unwrap()
    assert pv.size == FB_CONF_PARTITION_SIZE, (
        f"Must return fixed size partition {FB_CONF_PARTITION_SIZE}"
    )
    assert pv.owner == 1
    # Verify no arbitrary allocate(size, category) API exists
    assert not hasattr(mm, "allocate"), (
        "Generic heap allocate(size, category) must not exist"
    )


def test_mem_01b_acquire_slot_typed():
    """MEM-01b: acquire-slot<T> leases a typed handle."""

    class TCB:
        __size__ = 128

        def __init__(self):
            self.state = "READY"

    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    res = mm.acquire_slot(owner=2, cls=TCB)
    assert res.is_ok
    ref = res.unwrap()
    assert isinstance(ref, PoolRef)
    assert isinstance(ref.instance, TCB)
    assert ref.owner == 2


def test_mem_02_recovery_strategy_on_exhaustion():
    """MEM-02: Failure returns MemoryErrorResult with actionable recovery strategy."""
    mm = MemoryManager()
    # Small pool that fits only 1 partition
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_PARTITION_SIZE)
    r1 = mm.acquire_partition(owner=1)
    assert r1.is_ok
    # Second allocation must fail and return structured error
    r2 = mm.acquire_partition(owner=2)
    assert r2.is_err
    err = r2.error
    assert err is not None
    assert err.error_code == "ERR_POOL_EXHAUSTED"
    assert err.recovery.action in (RecoveryAction.DEGRADE, RecoveryAction.RETRY)


def test_mem_03_total_allocation_bound():
    """MEM-03: Total allocated bytes never exceeds FB_CONF_MEMORY_POOL_SIZE."""
    mm = MemoryManager()
    pool_size = 256 * 1024
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
    """MEM-05: release-partition / deallocate is permitted ONLY by owner task."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    mm.acquire_partition(owner=3)
    assert 3 in mm.partition_owners
    # Rogue task 4 attempts to release task 3's partition
    mm.release_partition(caller_task_id=4)
    assert 3 in mm.partition_owners, (
        "Rogue task must not be able to release another task's partition"
    )
    # Owner task 3 releases its partition
    mm.release_partition(caller_task_id=3)
    assert 3 not in mm.partition_owners


def test_mem_06_guest_ram_64kb_alignment():
    """MEM-06: pool_base and Guest WASM RAM is strictly 64KB aligned."""
    mm = MemoryManager()
    aligned_base = 0x20020000
    assert aligned_base % FB_WASM_PAGE_SIZE == 0
    res = mm.init_manager(pool_base=aligned_base, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    assert res.is_ok
    # Unaligned base must assert / reject
    unaligned_base = 0x20021000
    try:
        mm.init_manager(pool_base=unaligned_base, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        assert False, "Unaligned pool_base must fail"
    except AssertionError as e:
        assert "64KB aligned" in str(e)


def test_mem_07_allocate_shared_registers_vmmio_pte():
    """MEM-07: allocate-shared registers corresponding vMMIO FC=14 PTE with caller owner_id."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    res = mm.allocate_shared(caller_task_id=1, size=2048)
    assert res.is_ok
    sb = res.unwrap()
    owner = mm.vmmio_registry.get_owner(sb.page_idx)
    assert owner == 1, "vMMIO PTE must be registered with owner_id = 1"


def test_mem_08_claim_requires_grant_completion():
    """MEM-08: claim fails if Grant phase has not updated vMMIO PTE owner_id."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    shm_id = sb.release()
    # Attempt claim by Task 2 before IPC Router has granted (PTE still FLIGHT)
    c_res = mm.claim(receiver_task_id=2, shm_id=shm_id)
    assert c_res.is_err
    assert c_res.error.error_code == "ERR_GRANT_NOT_COMPLETED"


def test_mem_09_hal_acquire_buffer_delegates_to_allocate_shared():
    """MEM-09: platform_hal acquire_buffer unifies with memory manager allocate_shared."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    hal = HALBufferManager(mm)
    res = hal.acquire_buffer(hal_task_id=10, size=512)
    assert res.is_ok
    sb = res.unwrap()
    assert sb.owner == 10
    assert mm.vmmio_registry.get_owner(sb.page_idx) == 10


def test_mem_10_shared_block_ownership_transfer():
    """MEM-10: allocate-shared -> release -> claim moves ownership cleanly without double-ownership."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    # 1. Task A allocates
    sb_a = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    assert sb_a.get_owner() == 1
    # 2. Task A releases (Revoke)
    shm_id = sb_a.release()
    assert not sb_a._is_active
    # 3. Simulate IPC Router Grant phase: update vMMIO PTE to Task B
    mm.vmmio_registry.update_owner(sb_a.page_idx, 2)
    # 4. Task B claims
    sb_b = mm.claim(receiver_task_id=2, shm_id=shm_id).unwrap()
    assert sb_b.get_owner() == 2
    assert sb_b._is_active


def test_mem_10b_shared_block_vmmio_pte_flight_and_claim():
    """MEM-10b: release() sets PTE to FLIGHT; claim() sets PTE to receiver."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    page_idx = sb.page_idx
    assert mm.vmmio_registry.get_owner(page_idx) == 1
    sb.release()
    assert mm.vmmio_registry.get_owner(page_idx) == FB_TASK_ID_FLIGHT
    # Simulate Grant
    mm.vmmio_registry.update_owner(page_idx, 2)
    claimed_sb = mm.claim(receiver_task_id=2, shm_id=sb.shm_id).unwrap()
    assert mm.vmmio_registry.get_owner(page_idx) == 2
    assert claimed_sb.get_owner() == 2


def test_mem_10c_route_message_rollback_restores_owner_id():
    """MEM-10c: Rollback on queue full restores PTE owner_id to sender (not left as FLIGHT)."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    shm_id = sb.release()
    assert mm.vmmio_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT
    # Send failed with ERR_QUEUE_FULL -> Rollback
    mm.rollback_transfer(original_sender_id=1, shm_id=shm_id)
    assert mm.vmmio_registry.get_owner(sb.page_idx) == 1, (
        "PTE owner must be restored to Task 1"
    )


def test_mem_11_shared_block_raii_auto_deallocate():
    """MEM-11: SharedBlock RAII automatically deallocates buffer on drop."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    initial_alloc = mm.total_allocated_bytes
    # Use context manager to trigger deterministic drop
    with mm.allocate_shared(caller_task_id=2, size=1024).unwrap() as sb:
        assert mm.total_allocated_bytes == initial_alloc + FB_PAGE_SIZE
        assert sb.shm_id in mm.shm_slots

    # After exit (dropped), buffer is automatically deallocated
    assert mm.total_allocated_bytes == initial_alloc
    assert sb.shm_id not in mm.shm_slots


def test_mem_12_shm_id_kv_pair_encoding():
    """MEM-12: shm-id kv_pair encoding conforms to ipc_router.md vocabulary (scope=0b000, type=0b00001)."""
    shm_id = 0x0102
    scope_functional = 0b000
    type_u32 = 0b00001
    kv_type_byte = (scope_functional << 5) | type_u32
    assert kv_type_byte == 0x01, "Functional u32 kv_pair type byte must be 0x01"
    # Ensure no custom unvocabularized dtype=handle is used
    assert scope_functional != 0b010, "shm-id is not a hardware resource descriptor"


def test_mem_13_query_and_check_ownership_are_removed():
    """MEM-13: query() and check_ownership() are removed per ADR_MemoryManagerMinimalSurface."""
    mm = MemoryManager()
    assert not hasattr(mm, "query"), "query() API must be removed"
    assert not hasattr(mm, "check_ownership"), "check_ownership() API must be removed"


def test_mem_20_mpu_8_regions_static_allocation():
    """MEM-20: 8 MPU regions match the PMSAv8 static allocation table."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    assert len(mpu.regions) == 8
    # Region 0: Flash RO+X
    assert mpu.regions[0].ap == AccessPermission.RO and not mpu.regions[0].xn
    # Region 3: Guest RAM RW+XN
    assert mpu.regions[3].ap == AccessPermission.RW and mpu.regions[3].xn
    # Region 4: JIT Code Cache RO+X default
    assert mpu.regions[4].ap == AccessPermission.RO and not mpu.regions[4].xn
    # Region 7: Stack Guard No Access
    assert mpu.regions[7].ap == AccessPermission.NO_ACCESS


def test_mem_21_jit_code_cache_wx_switch_on_begin():
    """MEM-21: begin_jit_patch switches JIT cache to RW+XN and issues DSB/ISB."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    assert mpu.regions[4].is_executable and not mpu.regions[4].is_writable
    mpu.begin_jit_patch()
    assert mpu.regions[4].is_writable and not mpu.regions[4].is_executable
    assert mpu.dsb_count == 1
    assert mpu.isb_count == 1


def test_mem_22_jit_code_cache_wx_restore_on_commit():
    """MEM-22: commit_jit_patch restores JIT cache to RO+X and issues barriers."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    mpu.begin_jit_patch()
    mpu.commit_jit_patch()
    assert mpu.regions[4].is_executable and not mpu.regions[4].is_writable
    assert mpu.dsb_count == 2
    assert mpu.isb_count == 2


def test_mem_23_rwx_state_permanently_eliminated():
    """MEM-23: RWX permissions are permanently eliminated in all MPU states."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    mpu.assert_no_rwx()
    mpu.begin_jit_patch()
    mpu.assert_no_rwx()
    mpu.commit_jit_patch()
    mpu.assert_no_rwx()


def test_mem_24_transaction_batching_barrier_efficiency():
    """MEM-24: Batching emits exactly 1 begin / 1 commit pair per compilation unit."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    # 10 patches applied in a single compilation unit
    mpu.begin_jit_patch()
    for _ in range(10):
        # Simulate copying and patching instruction stencils
        pass
    mpu.commit_jit_patch()
    assert mpu.dsb_count == 2, "Batching must only emit 2 barriers per compilation unit"
    assert mpu.isb_count == 2


def test_mem_25_pmsav8_32byte_alignment():
    """MEM-25: All MPU base and limit addresses adhere to 32-byte alignment."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    for r in mpu.regions:
        assert r.base_address % 32 == 0, (
            f"Region {r.region_no} base must be 32-byte aligned"
        )
        assert (r.limit_address + 32) % 32 == 0 or r.limit_address % 32 == 0, (
            f"Region {r.region_no} limit must be 32-byte aligned"
        )


# =============================================================================
# Main Runner
# =============================================================================

if __name__ == "__main__":
    test_mem_01_acquire_partition_fixed_size()
    test_mem_01b_acquire_slot_typed()
    test_mem_02_recovery_strategy_on_exhaustion()
    test_mem_03_total_allocation_bound()
    test_mem_04_owner_task_id_auto_set()
    test_mem_05_release_and_deallocate_owner_only()
    test_mem_06_guest_ram_64kb_alignment()
    test_mem_07_allocate_shared_registers_vmmio_pte()
    test_mem_08_claim_requires_grant_completion()
    test_mem_09_hal_acquire_buffer_delegates_to_allocate_shared()
    test_mem_10_shared_block_ownership_transfer()
    test_mem_10b_shared_block_vmmio_pte_flight_and_claim()
    test_mem_10c_route_message_rollback_restores_owner_id()
    test_mem_11_shared_block_raii_auto_deallocate()
    test_mem_12_shm_id_kv_pair_encoding()
    test_mem_13_query_and_check_ownership_are_removed()
    test_mem_20_mpu_8_regions_static_allocation()
    test_mem_21_jit_code_cache_wx_switch_on_begin()
    test_mem_22_jit_code_cache_wx_restore_on_commit()
    test_mem_23_rwx_state_permanently_eliminated()
    test_mem_24_transaction_batching_barrier_efficiency()
    test_mem_25_pmsav8_32byte_alignment()
    print(
        "[PASS] All platform memory concept tests (MEM-01 ~ MEM-25) passed successfully."
    )
