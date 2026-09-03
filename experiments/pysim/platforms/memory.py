"""
experiments/pysim/platforms/memory.py
COOS Memory Manager & PMSAv8 MPU simulation.
- Consolidated physical memory pool and fixed-size partition leasing
- Typed slot pools with zero dynamic void* heap
- RAII SharedBlock zero-copy ownership transfer linked with page table listeners
- Cortex-M33 PMSAv8 8-region MPU allocation and JIT W^X transaction switching
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from types import TracebackType
from typing import Generic, TypeVar

from system_containers import MutableFlatMapStorage

T = TypeVar("T")


@dataclass(slots=True)
class PageMappingCallbacks:
    """Decoupled callbacks for external page table / MMU listeners."""

    on_map_page: Callable[
        [int, int, int], None
    ]  # (page_idx: int, phys_addr: int, owner_id: int) -> None
    on_update_owner: Callable[
        [int, int, int], None
    ]  # (page_idx: int, phys_addr: int, new_owner_id: int) -> None
    on_revoke: Callable[[int, int], None]  # (page_idx: int, phys_addr: int) -> None
    on_unmap_page: Callable[[int, int], None]  # (page_idx: int, phys_addr: int) -> None


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
class ShmPageInfo:
    """4KB Physical SHM Page bookkeeping for page-granular permission isolation."""

    page_idx: int
    owner_id: int
    allocated: bool = False
    allocated_bytes: int = 0
    slot_count: int = 0
    data: bytearray = field(default_factory=lambda: bytearray(FB_PAGE_SIZE))


@dataclass
class ShmSlot:
    """One allocated shared-memory page's bookkeeping record."""

    page_idx: int
    slot_idx: int
    size: int
    owner: int
    base_address: int
    allocated: bool
    data: bytearray = field(default_factory=bytearray)


@dataclass
class ShmPagePTE:
    """Shared memory page table entry representation."""

    page_idx: int
    owner_id: int
    physical_addr: int
    is_valid: bool = True


_FB_CONF_MAX_SHM_PHYS_PAGES = FB_CONF_MEMORY_POOL_SIZE // FB_PAGE_SIZE


class ShmPageRegistry:
    """
    Shared memory page table registry for physical memory manager.
    `page_idx` ranges over the physical pool's fixed page count
    (FB_CONF_MEMORY_POOL_SIZE / FB_PAGE_SIZE), so a fixed-size array indexed
    directly by page_idx is the direct fit -- not a dict.
    """

    def __init__(self):
        self.ptes: list[ShmPagePTE | None] = [None] * _FB_CONF_MAX_SHM_PHYS_PAGES

    def register_page(self, page_idx: int, owner_id: int, physical_addr: int) -> None:
        self.ptes[page_idx] = ShmPagePTE(
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

    def unregister_page(self, page_idx: int) -> None:
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
        data: bytearray | None = None,
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
        self.data: bytearray = data if data is not None else bytearray(size)

    def get_address(self) -> int:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        return self.base_address

    def get_size(self) -> int:
        return self.size

    def get_owner(self) -> int:
        return self.owner

    def _check_access(self, offset: int, length: int = 1) -> None:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        assert not self._is_in_flight, "Cannot access in-flight SharedBlock"
        assert 0 <= offset and offset + length <= self.size, (
            f"Access out of bounds: offset {offset} + len {length} > size {self.size}"
        )

    def get_bytearray(self) -> bytearray:
        """Returns the underlying shared memory bytearray."""
        assert self._is_active and not self._is_in_flight, (
            "Cannot access inactive or in-flight SharedBlock bytearray"
        )
        return self.data

    def read_u8(self, offset: int) -> int:
        self._check_access(offset, 1)
        return self.data[offset]

    def write_u8(self, offset: int, val: int) -> None:
        self._check_access(offset, 1)
        self.data[offset] = val & 0xFF

    def read_u16(self, offset: int) -> int:
        self._check_access(offset, 2)
        return struct.unpack_from("<H", self.data, offset)[0]

    def write_u16(self, offset: int, val: int) -> None:
        self._check_access(offset, 2)
        struct.pack_into("<H", self.data, offset, val & 0xFFFF)

    def read_u32(self, offset: int) -> int:
        self._check_access(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def write_u32(self, offset: int, val: int) -> None:
        self._check_access(offset, 4)
        struct.pack_into("<I", self.data, offset, val & 0xFFFFFFFF)

    def read_i32(self, offset: int) -> int:
        self._check_access(offset, 4)
        return struct.unpack_from("<i", self.data, offset)[0]

    def write_i32(self, offset: int, val: int) -> None:
        self._check_access(offset, 4)
        struct.pack_into("<i", self.data, offset, val)

    def read_bytes(self, offset: int, length: int) -> bytes:
        self._check_access(offset, length)
        return bytes(self.data[offset : offset + length])

    def write_bytes(self, offset: int, src: bytes | bytearray) -> None:
        self._check_access(offset, len(src))
        self.data[offset : offset + len(src)] = src

    def read_kv(self, offset: int) -> tuple[int, int]:
        """Reads 64-bit kv_pair (uint32 key, uint32 value) from bytearray."""
        self._check_access(offset, 8)
        return struct.unpack_from("<II", self.data, offset)

    def write_kv(self, offset: int, key: int, val: int) -> None:
        """Writes 64-bit kv_pair (uint32 key, uint32 value) into bytearray."""
        self._check_access(offset, 8)
        struct.pack_into("<II", self.data, offset, key & 0xFFFFFFFF, val & 0xFFFFFFFF)

    def u64_capacity(self) -> int:
        """Returns the number of uint64_t elements available in this shared memory array."""
        return self.size // 8

    def read_u64(self, index: int) -> int:
        """Reads a 64-bit unsigned integer (uint64_t) from the shared memory array at element index."""
        offset = index * 8
        self._check_access(offset, 8)
        return struct.unpack_from("<Q", self.data, offset)[0]

    def write_u64(self, index: int, val: int) -> None:
        """Writes a 64-bit unsigned integer (uint64_t) to the shared memory array at element index."""
        offset = index * 8
        self._check_access(offset, 8)
        struct.pack_into("<Q", self.data, offset, val & 0xFFFFFFFFFFFFFFFF)

    def read_entry(self, index: int) -> tuple[int, int]:
        """
        Reads one kv_pair (uint32 key, uint32 value) from the uint64_t array at element index.
        In uint64_t layout: key in upper 32 bits, value in lower 32 bits.
        """
        raw = self.read_u64(index)
        key = (raw >> 32) & 0xFFFFFFFF
        val = raw & 0xFFFFFFFF
        return (key, val)

    def write_entry(self, index: int, key: int, val: int) -> None:
        """
        Writes one kv_pair (uint32 key, uint32 value) into the uint64_t array at element index.
        In uint64_t layout: packs key into upper 32 bits and value into lower 32 bits.
        """
        packed = ((key & 0xFFFFFFFF) << 32) | (val & 0xFFFFFFFF)
        self.write_u64(index, packed)

    def release(self) -> int:
        """Revoke sender access and prepare for transfer (marks FLIGHT)."""
        assert self._is_active, "Cannot release inactive SharedBlock"
        if self._manager is not None:
            self._is_active = False
            self._is_in_flight = True
            self._manager.page_registry.update_owner(self.page_idx, FB_TASK_ID_FLIGHT)
            if self.page_idx < len(self._manager.shm_pages):
                self._manager.shm_pages[self.page_idx].owner_id = FB_TASK_ID_FLIGHT
            if self._manager._page_mapping_callbacks is not None:
                self._manager._page_mapping_callbacks.on_revoke(self.page_idx, self.base_address)
        return self.shm_id

    def drop(self) -> None:
        """RAII drop handler: automatically deallocates physical buffer if still owned."""
        if self._is_active:
            self._is_active = False
            if self._manager is not None:
                self._manager._deallocate_shared_slot(self.page_idx, self.slot_idx, self.owner)
        elif self._is_in_flight:
            pass

    def __del__(self):
        self.drop()

    def __enter__(self) -> SharedBlock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
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

    def _setup_static_regions(self, pool_base: int) -> None:
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

    def begin_jit_patch(self) -> None:
        assert not self.patch_in_progress, "Nested JIT patch transaction is invalid"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RW
        r4.xn = True
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = True

    def commit_jit_patch(self) -> None:
        assert self.patch_in_progress, "Cannot commit without begin_jit_patch"
        r4 = self.regions[4]
        r4.ap = AccessPermission.RO
        r4.xn = False
        self.dsb_count += 1
        self.isb_count += 1
        self.patch_in_progress = False

    def assert_no_rwx(self) -> None:
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
        self.page_registry = ShmPageRegistry()
        self._page_mapping_callbacks: PageMappingCallbacks | None = None
        self.mpu: PMSAv8MPU | None = None
        self.partition_owners: MutableFlatMapStorage[int, PartitionView] = MutableFlatMapStorage(
            capacity=FB_CONF_MAX_TASKS
        )
        self.shm_slots: MutableFlatMapStorage[int, ShmSlot] = MutableFlatMapStorage(
            capacity=_FB_CONF_MAX_SHM_PHYS_PAGES
        )
        # Page-granular permission isolation: each 4KB physical page tracks its exclusive owner_id
        self.shm_pages: list[ShmPageInfo] = [
            ShmPageInfo(page_idx=i, owner_id=0, allocated=False, allocated_bytes=0, slot_count=0)
            for i in range(_FB_CONF_MAX_SHM_PHYS_PAGES)
        ]

    def register_page_mapping_callbacks(
        self,
        callbacks: PageMappingCallbacks,
    ) -> None:
        """Registers external page table / MMU listener callbacks for SHM page events."""
        self._page_mapping_callbacks = callbacks

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

    def release_partition(self, caller_task_id: int) -> None:
        if caller_task_id not in self.partition_owners:
            return
        self.partition_owners.remove(caller_task_id)
        self.total_allocated_bytes -= FB_CONF_PARTITION_SIZE

    def allocate_shared(
        self,
        caller_task_id: int,
        size: int,
    ) -> Result[SharedBlock]:
        assert caller_task_id != 0, "Shared block must be owned by an explicit task"
        if size <= 0 or size > FB_PAGE_SIZE:
            return Result(
                error=MemoryErrorResult(
                    "ERR_INVALID_SIZE",
                    RecoveryStrategy(RecoveryAction.RETRY, "Requested SHM size out of bounds"),
                )
            )

        # Page-granular isolation: find an existing page owned by caller_task_id with enough space,
        # OR find an unallocated page to reserve exclusively for caller_task_id.
        target_page: ShmPageInfo | None = None
        for page in self.shm_pages:
            if page.allocated and page.owner_id == caller_task_id:
                if FB_PAGE_SIZE - page.allocated_bytes >= size:
                    target_page = page
                    break

        if target_page is None:
            # Need a new 4KB page
            if self.total_allocated_bytes + FB_PAGE_SIZE > self.pool_size:
                return Result(
                    error=MemoryErrorResult(
                        "ERR_SHM_EXHAUSTED",
                        RecoveryStrategy(
                            RecoveryAction.DEGRADE, "No free SHM pages in physical pool"
                        ),
                    )
                )
            for page in self.shm_pages:
                if not page.allocated:
                    target_page = page
                    break

            if target_page is None:
                return Result(
                    error=MemoryErrorResult(
                        "ERR_SHM_EXHAUSTED",
                        RecoveryStrategy(RecoveryAction.DEGRADE, "All SHM page slots exhausted"),
                    )
                )

            # Initialize new page exclusively for caller_task_id
            target_page.allocated = True
            target_page.owner_id = caller_task_id
            target_page.allocated_bytes = 0
            target_page.slot_count = 0
            self.total_allocated_bytes += FB_PAGE_SIZE

            # Register in page registry and notify listener
            base_addr = 0x20080000 + (target_page.page_idx * FB_PAGE_SIZE)
            self.page_registry.register_page(target_page.page_idx, caller_task_id, base_addr)
            if self._page_mapping_callbacks is not None:
                self._page_mapping_callbacks.on_map_page(
                    target_page.page_idx, base_addr, caller_task_id
                )

        # Allocate slot inside target_page
        slot_idx = target_page.slot_count
        slot_offset = target_page.allocated_bytes
        target_page.slot_count += 1
        target_page.allocated_bytes += size

        shm_id = (target_page.page_idx << 8) | slot_idx
        base_addr = 0x20080000 + (target_page.page_idx * FB_PAGE_SIZE) + slot_offset
        slot_data = bytearray(size)
        self.shm_slots.insert(
            shm_id,
            ShmSlot(
                page_idx=target_page.page_idx,
                slot_idx=slot_idx,
                size=size,
                owner=caller_task_id,
                base_address=base_addr,
                allocated=True,
                data=slot_data,
            ),
        )

        sb = SharedBlock(
            shm_id=shm_id,
            page_idx=target_page.page_idx,
            slot_idx=slot_idx,
            size=size,
            owner=caller_task_id,
            base_address=base_addr,
            manager=self,
            data=slot_data,
        )
        return Result(value=sb)

    def grant_shared(self, shm_id: int, new_owner_task_id: int) -> bool:
        """Grants in-flight SHM block to the receiver task in page table (Grant phase)."""
        slot = self.shm_slots.find(shm_id)
        if slot is None or not slot.allocated:
            return False
        slot.owner = new_owner_task_id
        if slot.page_idx < len(self.shm_pages):
            self.shm_pages[slot.page_idx].owner_id = new_owner_task_id
        self.page_registry.update_owner(slot.page_idx, new_owner_task_id)
        if self._page_mapping_callbacks is not None:
            self._page_mapping_callbacks.on_update_owner(
                slot.page_idx, slot.base_address, new_owner_task_id
            )
        return True

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
        current_owner = self.page_registry.get_owner(page_idx)
        if current_owner != receiver_task_id:
            return Result(
                error=MemoryErrorResult(
                    "ERR_GRANT_NOT_COMPLETED",
                    RecoveryStrategy(RecoveryAction.RETRY, "Grant phase incomplete in page table"),
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
            data=slot.data,
        )
        return Result(value=sb)

    def rollback_transfer(self, original_sender_id: int, shm_id: int) -> None:
        """Restores a shared block's original owner in the page table."""
        slot = self.shm_slots.find(shm_id)
        if slot is not None:
            slot.owner = original_sender_id
            if slot.page_idx < len(self.shm_pages):
                self.shm_pages[slot.page_idx].owner_id = original_sender_id
            self.page_registry.update_owner(slot.page_idx, original_sender_id)
            if self._page_mapping_callbacks is not None:
                self._page_mapping_callbacks.on_update_owner(
                    slot.page_idx, slot.base_address, original_sender_id
                )

    def deallocate(self, caller_task_id: int, addr: int) -> None:
        """Deallocate local static partition or slot. Owner enforced."""
        owners_view = self.partition_owners.view()
        for owner, pv in list(zip(owners_view.keys, owners_view.values, strict=False)):
            if pv.base_address == addr:
                if owner == caller_task_id:
                    self.release_partition(caller_task_id)
                return

    def _deallocate_shared_slot(self, page_idx: int, slot_idx: int, owner: int) -> None:
        shm_id = (page_idx << 8) | slot_idx
        if shm_id in self.shm_slots:
            self.shm_slots.remove(shm_id)

            # Check if any slots in this page remain allocated
            page = self.shm_pages[page_idx] if page_idx < len(self.shm_pages) else None
            has_remaining = False
            for s in self.shm_slots.view().values:
                if s.page_idx == page_idx:
                    has_remaining = True
                    break

            if not has_remaining and page is not None:
                page.allocated = False
                page.allocated_bytes = 0
                page.slot_count = 0
                page.owner_id = 0
                self.page_registry.unregister_page(page_idx)
                if self._page_mapping_callbacks is not None:
                    base_addr = 0x20080000 + (page_idx * FB_PAGE_SIZE)
                    self._page_mapping_callbacks.on_unmap_page(page_idx, base_addr)
                self.total_allocated_bytes -= FB_PAGE_SIZE
