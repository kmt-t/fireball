"""
experiments/pysim/platforms/memory.py
COOS Memory Manager & PMSAv8 MPU simulation.
- Consolidated physical memory pool and fixed-size partition leasing
- Typed slot pools with zero dynamic void* heap
- RAII SharedBlock zero-copy ownership transfer linked with vMMIO FC=14 PTEs
- Cortex-M33 PMSAv8 8-region MPU allocation and JIT W^X transaction switching
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Generic, TypeVar

from system_containers import StaticFlatMap

T = TypeVar("T")

# Configuration & Constants (FB_CONF_*)
FB_CONF_MEMORY_POOL_SIZE = 2 * 1024 * 1024  # 2MB physical pool
FB_CONF_PARTITION_SIZE = 64 * 1024  # 64KB fixed partition per task
FB_CONF_MAX_TASKS = 16
FB_CONF_MAX_SHM_PAGES = 32
FB_PAGE_SIZE = 4096  # 4KB SHM page size
FB_WASM_PAGE_SIZE = 65536  # 64KB WASM page size
FB_TASK_ID_FLIGHT = 0xFF  # Flight sentinel during IPC transfer (8-bit PTE owner_id compliant)
FB_TASK_ID_KERNEL = 0x00


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
class ShmSlot:
    """One allocated shared-memory page's bookkeeping record."""

    page_idx: int
    slot_idx: int
    size: int
    owner: int
    base_address: int
    allocated: bool


@dataclass
class VMMIOPTE:
    """vMMIO FC=14 Page Table Entry representation."""

    page_idx: int
    owner_id: int
    physical_addr: int
    is_valid: bool = True


_FB_CONF_MAX_VMMIO_PAGES = FB_CONF_MEMORY_POOL_SIZE // FB_PAGE_SIZE


class VMMIOPTERegistry:
    """
    vMMIO Tier 3 PTE Registry mock for memory manager integration.
    `page_idx` ranges over the physical pool's fixed page count
    (FB_CONF_MEMORY_POOL_SIZE / FB_PAGE_SIZE), so a fixed-size array indexed
    directly by page_idx is the direct fit -- not a dict.
    """

    def __init__(self):
        self.ptes: list[VMMIOPTE | None] = [None] * _FB_CONF_MAX_VMMIO_PAGES

    def register_page(self, page_idx: int, owner_id: int, physical_addr: int):
        self.ptes[page_idx] = VMMIOPTE(
            page_idx=page_idx,
            owner_id=owner_id,
            physical_addr=physical_addr,
            is_valid=True,
        )

    def update_owner(self, page_idx: int, new_owner_id: int) -> bool:
        pte = self.ptes[page_idx]
        if pte is None or not pte.is_valid:
            return False
        pte.owner_id = new_owner_id
        return True

    def get_owner(self, page_idx: int) -> int | None:
        pte = self.ptes[page_idx]
        return pte.owner_id if pte is not None and pte.is_valid else None

    def unregister_page(self, page_idx: int):
        pte = self.ptes[page_idx]
        if pte is not None:
            pte.is_valid = False


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
        self._manager.vmmio_registry.update_owner(self.page_idx, FB_TASK_ID_FLIGHT)
        return self.shm_id

    def drop(self):
        """RAII drop handler: automatically deallocates physical buffer if still owned."""
        if self._is_active:
            self._is_active = False
            self._manager._deallocate_shared_slot(self.page_idx, self.slot_idx, self.owner)
        elif self._is_in_flight:
            pass

    def __del__(self):
        self.drop()

    def __enter__(self) -> SharedBlock:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.drop()


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

    def __init__(self, pool_base: int = 0x20020000):
        self.regions: list[MPURegion] = []
        self.dsb_count = 0
        self.isb_count = 0
        self.patch_in_progress = False
        self._setup_static_regions(pool_base)

    def _setup_static_regions(self, pool_base: int):
        self.regions = [
            MPURegion(
                0,
                "Flash_KernelCode",
                0x00000000,
                0x0007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            MPURegion(
                1,
                "Kernel_DataBSS",
                0x20000000,
                0x20007FE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                2,
                "Kernel_PoolHeap",
                0x20008000,
                0x2001FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                3,
                "Guest_WasmRAM",
                pool_base,
                pool_base + 0x000FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                4,
                "JIT_CodeCache",
                0x20040000,
                0x2007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            MPURegion(
                5,
                "Peripheral_MMIO",
                0x40000000,
                0x4003FFE0,
                AccessPermission.RW,
                xn=True,
                is_device=True,
            ),
            MPURegion(6, "Shared_Memory", 0x20080000, 0x200BFFE0, AccessPermission.RW, xn=True),
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
        assert not self.patch_in_progress, "Nested JIT patch transaction is invalid"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RW
        r4.xn = True
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = True

    def commit_jit_patch(self):
        assert self.patch_in_progress, "Cannot commit without begin_jit_patch"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RO
        r4.xn = False
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = False

    def assert_no_rwx(self):
        for r in self.regions:
            if r.enabled:
                assert not (r.is_writable and r.is_executable), (
                    f"Invariant violation: Region {r.region_no} ({r.name}) has RWX permissions"
                )


class MemoryManager:
    """Tier 3 Consolidated Physical Memory Manager (platform_memory.md)."""

    def __init__(self):
        self.pool_base: int = 0
        self.pool_size: int = 0
        self.total_allocated_bytes: int = 0
        self.vmmio_registry = VMMIOPTERegistry()
        self.mpu: PMSAv8MPU | None = None
        # Both bounded by real fixed capacities (max concurrent tasks; max
        # concurrent SHM pages), so a StaticFlatMap -- fixed-capacity, no
        # dynamic reallocation -- fits directly; a dict would allow either
        # to silently grow past the hardware limit it represents.
        self.partition_owners: StaticFlatMap[int, PartitionView] = StaticFlatMap(
            capacity=FB_CONF_MAX_TASKS
        )
        self.shm_slots: StaticFlatMap[int, ShmSlot] = StaticFlatMap(
            capacity=_FB_CONF_MAX_VMMIO_PAGES
        )

    def init_manager(self, pool_base: int, pool_size: int) -> Result[bool]:
        assert pool_base % FB_WASM_PAGE_SIZE == 0, (
            f"pool_base 0x{pool_base:X} must be 64KB aligned (WasmPageAlignment)"
        )
        self.pool_base = pool_base
        self.pool_size = pool_size
        self.total_allocated_bytes = 0
        self.mpu = PMSAv8MPU(pool_base)
        return Result(value=True)

    def acquire_partition(self, owner: int) -> Result[PartitionView]:
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
                    RecoveryStrategy(RecoveryAction.DEGRADE, "Physical memory pool exhausted"),
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
        self.partition_owners.insert(owner, pv)
        self.total_allocated_bytes += FB_CONF_PARTITION_SIZE
        return Result(value=pv)

    def release_partition(self, caller_task_id: int):
        if caller_task_id not in self.partition_owners:
            return
        self.partition_owners.remove(caller_task_id)
        self.total_allocated_bytes -= FB_CONF_PARTITION_SIZE

    def allocate_shared(self, caller_task_id: int, size: int) -> Result[SharedBlock]:
        assert caller_task_id != 0, "Shared block must be owned by an explicit task"
        if size <= 0 or size > FB_PAGE_SIZE:
            return Result(
                error=MemoryErrorResult(
                    "ERR_INVALID_SIZE",
                    RecoveryStrategy(RecoveryAction.RETRY, "Requested SHM size out of bounds"),
                )
            )

        if self.total_allocated_bytes + FB_PAGE_SIZE > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    "ERR_SHM_EXHAUSTED",
                    RecoveryStrategy(RecoveryAction.DEGRADE, "No free SHM pages in physical pool"),
                )
            )

        page_idx = len(self.shm_slots)
        slot_idx = 0
        shm_id = (page_idx << 8) | slot_idx
        base_addr = 0x20080000 + (page_idx * FB_PAGE_SIZE)
        self.vmmio_registry.register_page(page_idx, caller_task_id, base_addr)
        self.shm_slots.insert(
            shm_id,
            ShmSlot(
                page_idx=page_idx,
                slot_idx=slot_idx,
                size=size,
                owner=caller_task_id,
                base_address=base_addr,
                allocated=True,
            ),
        )
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
        slot = self.shm_slots.find(shm_id)
        if slot is None or not slot.allocated:
            return Result(
                error=MemoryErrorResult(
                    "ERR_INVALID_SHM_ID",
                    RecoveryStrategy(RecoveryAction.RETRY, "Invalid or deallocated SHM ID"),
                )
            )

        page_idx = slot.page_idx
        current_owner = self.vmmio_registry.get_owner(page_idx)
        if current_owner != receiver_task_id:
            return Result(
                error=MemoryErrorResult(
                    "ERR_GRANT_NOT_COMPLETED",
                    RecoveryStrategy(RecoveryAction.RETRY, "Grant phase incomplete in vMMIO PTE"),
                )
            )

        slot.owner = receiver_task_id
        sb = SharedBlock(
            shm_id=shm_id,
            page_idx=page_idx,
            slot_idx=slot.slot_idx,
            size=slot.size,
            owner=receiver_task_id,
            base_address=slot.base_address,
            manager=self,
        )
        return Result(value=sb)

    def rollback_transfer(self, original_sender_id: int, shm_id: int):
        """Restores a shared block's original owner in the vMMIO PTE."""
        slot = self.shm_slots.find(shm_id)
        if slot is not None:
            self.vmmio_registry.update_owner(slot.page_idx, original_sender_id)
            slot.owner = original_sender_id

    def deallocate(self, caller_task_id: int, addr: int):
        """Deallocate local static partition or slot. Owner enforced."""
        owners_view = self.partition_owners.view()
        for owner, pv in list(zip(owners_view.keys, owners_view.values, strict=False)):
            if pv.base_address == addr:
                if owner == caller_task_id:
                    self.release_partition(caller_task_id)
                return

    def _deallocate_shared_slot(self, page_idx: int, slot_idx: int, owner: int):
        shm_id = (page_idx << 8) | slot_idx
        if shm_id in self.shm_slots:
            self.shm_slots.remove(shm_id)
            self.vmmio_registry.unregister_page(page_idx)
            self.total_allocated_bytes -= FB_PAGE_SIZE
