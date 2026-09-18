"""
experiments/pysim/tier2_runtime/memory.py
COOS Memory Manager & PMSAv8 MPU simulation.
- Consolidated physical memory pool and fixed-size partition leasing
- Typed slot pools with zero dynamic void* heap
- RAII SharedBlock zero-copy ownership transfer linked with page table listeners
- Cortex-M33 PMSAv8 8-region MPU allocation and JIT W^X transaction switching
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from types import TracebackType
from typing import Generic, TypeVar

from memory_interface import PageMappingCallbacks
from scheduler import Scheduler
from system_containers import MutableFlatMapStorage, StaticVector

T = TypeVar("T")


# Configuration & Constants (FB_CONF_*)
FB_CONF_MEMORY_POOL_SIZE = 23552  # system_config.md: sum of all sub-pools (bytes)
FB_CONF_TASK_HEAP_SIZES = (
    4096,
)  # system_config.md FB_CONF_TASK_HEAP_SIZES: per-VM-slot ROM size table
FB_CONF_MAX_TASKS = 16
FB_CONF_MAX_SHM_PAGES = 32
FB_PAGE_SIZE = 4096  # 4KB SHM page size
FB_CONF_SHM_SIZE = 1024  # Physical SHM backing budget; virtual page slots are separate.
FB_WASM_PAGE_SIZE = 65536  # 64KB WASM page size
# system_config.md "PMSAv8 MPU 物理アドレスマップ" 節: Region 6 (Shared Memory Buffers) の基点
FB_CONF_MPU_R6_SHARED_MEMORY_BASE = 0x2008_0000
FB_TASK_ID_FLIGHT = 0xFF  # Flight sentinel during IPC transfer (8-bit PTE owner_id compliant)
FB_TASK_ID_KERNEL = 0x00


class RecoveryAction(Enum):
    RETRY = auto()
    DEGRADE = auto()
    RESTART_TASK = auto()
    PANIC = auto()


class MemoryErrorCode(IntEnum):
    ALREADY_ACQUIRED = 1
    POOL_EXHAUSTED = 2
    INVALID_SIZE = 3
    SHM_EXHAUSTED = 4
    INVALID_SHM_ID = 5
    GRANT_NOT_COMPLETED = 6


class MemoryReasonCode(IntEnum):
    TASK_PARTITION_ALREADY_ACQUIRED = 1
    PHYSICAL_MEMORY_POOL_EXHAUSTED = 2
    REQUESTED_SHM_SIZE_OUT_OF_BOUNDS = 3
    NO_FREE_SHM_PAGES = 4
    ALL_SHM_PAGE_SLOTS_EXHAUSTED = 5
    INVALID_OR_DEALLOCATED_SHM_ID = 6
    GRANT_PHASE_INCOMPLETE = 7


@dataclass(slots=True)
class RecoveryStrategy:
    action: RecoveryAction
    reason_code: MemoryReasonCode


@dataclass(slots=True)
class MemoryErrorResult:
    error_code: MemoryErrorCode
    recovery: RecoveryStrategy

    def __str__(self) -> str:
        return f"MemoryError({self.error_code.name}:{self.recovery.reason_code.name}, action={self.recovery.action.name})"


@dataclass(slots=True)
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
        assert self.error is None, self.error
        assert self.value is not None
        return self.value


@dataclass(slots=True)
class PartitionView:
    """Fixed-size non-owning partition view leased to a specific task."""

    owner: int
    base_address: int
    size: int
    data: bytearray

    def is_valid_for(self, task_id: int) -> bool:
        return self.owner == task_id


@dataclass(slots=True)
class ShmPageInfo:
    """One 4KB virtual SHM reservation and its bounded physical backing."""

    page_idx: int
    owner_id: int
    allocated: bool = False
    allocated_bytes: int = 0
    slot_count: int = 0
    physical_addr: int = 0


@dataclass(slots=True)
class ShmSlot:
    """One allocated shared-memory block's bookkeeping record."""

    page_idx: int
    slot_idx: int
    size: int
    owner: int
    base_address: int
    allocated: bool
    data: memoryview = field(default_factory=lambda: memoryview(bytearray()))


@dataclass(slots=True)
class ShmPagePTE:
    """Shared memory page table entry representation."""

    page_idx: int
    owner_id: int
    physical_addr: int
    is_valid: bool = True


_FB_CONF_MAX_SHM_PAGE_SLOTS = FB_CONF_MAX_SHM_PAGES


class ShmPageRegistry:
    """
    Shared memory page table registry for physical memory manager.
    `page_idx` identifies a 4KB virtual reservation slot. Its range is
    independent of the physical SHM byte budget, so a fixed-size array indexed
    directly by page_idx is the direct fit -- not a dict.
    """

    __slots__ = ("ptes",)

    def __init__(self):
        self.ptes: StaticVector[ShmPagePTE | None] = StaticVector.of(
            (None,) * _FB_CONF_MAX_SHM_PAGE_SLOTS,
            capacity=_FB_CONF_MAX_SHM_PAGE_SLOTS,
        )

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

    __slots__ = (
        "_is_active",
        "_is_in_flight",
        "_manager",
        "base_address",
        "data",
        "owner",
        "page_idx",
        "shm_id",
        "size",
        "slot_idx",
    )

    def __init__(
        self,
        shm_id: int,
        page_idx: int,
        slot_idx: int,
        size: int,
        owner: int,
        base_address: int,
        manager: MemoryManager,
        data: memoryview | None = None,
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
        self.data: memoryview = data if data is not None else memoryview(bytearray(size))

    def get_address(self) -> int:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        assert self.owner == self._manager.current_task_id, (
            "GOTCHA-MEM-02: non-owner cannot access SharedBlock"
        )
        return self.base_address

    def get_size(self) -> int:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        assert self.owner == self._manager.current_task_id, (
            "GOTCHA-MEM-02: non-owner cannot access SharedBlock"
        )
        return self.size

    def get_owner(self) -> int:
        return self.owner

    def _check_access(self, offset: int, length: int = 1) -> None:
        assert self._is_active, "Cannot access released or dropped SharedBlock"
        assert not self._is_in_flight, "Cannot access in-flight SharedBlock"
        assert 0 <= offset and offset + length <= self.size, (
            f"Access out of bounds: offset {offset} + len {length} > size {self.size}"
        )

    def get_bytearray(self) -> memoryview:
        """Returns a bounded view into the fixed shared-memory backing store."""
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

    def write_bytes(self, offset: int, src: memoryview) -> None:
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
        assert self.owner == self._manager.current_task_id, (
            "GOTCHA-MEM-02: non-owner cannot release SharedBlock"
        )
        if self._manager is not None:
            self._is_active = False
            self._is_in_flight = True
            self._manager._set_shared_owner(self.page_idx, FB_TASK_ID_FLIGHT)
        return self.shm_id

    def move_to(self, new_owner: int) -> SharedBlock:
        """
        Simulates C++23 move semantics (std::move / rvalue reference &&).
        Invalidates this SharedBlock handle (disallowing subsequent access from sender)
        and returns an active SharedBlock handle owned by new_owner.
        """
        assert self._is_active, "Cannot move inactive or already moved SharedBlock"
        self._is_active = False
        self._is_in_flight = False

        if self._manager is not None:
            self._manager._set_shared_owner(self.page_idx, new_owner)
            self._manager._notify_shared_page_mapped(self.page_idx)

        return SharedBlock(
            shm_id=self.shm_id,
            page_idx=self.page_idx,
            slot_idx=self.slot_idx,
            size=self.size,
            owner=new_owner,
            base_address=self.base_address,
            manager=self._manager,
            data=self.data,
        )

    def drop(self) -> None:
        """RAII drop handler: automatically deallocates physical buffer if still owned."""
        if self._is_active:
            self._is_active = False
            if self._manager is not None:
                self._manager._deallocate_shared_slot(self.page_idx, self.slot_idx, self.owner)
        elif self._is_in_flight:
            pass

    def __del__(self) -> None:
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


@dataclass(slots=True)
class MPURegion:
    region_no: int
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

    __slots__ = ("dsb_count", "isb_count", "patch_count", "patch_in_progress", "regions")

    def __init__(self, pool_base: int = 0x20020000):
        self.regions: tuple[MPURegion, ...] = ()
        self.dsb_count = 0
        self.isb_count = 0
        self.patch_count = 0
        self.patch_in_progress = False
        self._setup_static_regions(pool_base)

    def _setup_static_regions(self, pool_base: int) -> None:
        # 8 statically allocated regions matching runtime_memory.md §7.1 Table.
        # Base addresses (Regions 1/2/4/5/6/7) are system_config.md's
        # FB_CONF_MPU_R*_BASE constants (PMSAv8 MPU 物理アドレスマップ節).
        self.regions = (
            MPURegion(
                0,
                0x00000000,
                0x0007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            MPURegion(
                1,
                0x20000000,
                0x20007FE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                2,
                0x20008000,
                0x2001FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                3,
                pool_base,
                pool_base + 0x000FFE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                4,
                0x20040000,
                0x2007FFE0,
                AccessPermission.RO,
                xn=False,
            ),
            MPURegion(
                5,
                0x40000000,
                0x4003FFE0,
                AccessPermission.RW,
                xn=True,
                is_device=True,
            ),
            MPURegion(
                6,
                FB_CONF_MPU_R6_SHARED_MEMORY_BASE,
                0x200BFFE0,
                AccessPermission.RW,
                xn=True,
            ),
            MPURegion(
                7,
                0x200C0000,
                0x200C0020,
                AccessPermission.NO_ACCESS,
                xn=True,
            ),
        )

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

    def patch_stencil(self) -> None:
        """Record one instruction-stencil patch inside the active transaction."""
        assert self.patch_in_progress, "Stencil patch requires an active JIT transaction"
        self.patch_count += 1

    def assert_no_rwx(self) -> None:
        for r in self.regions:
            if r.enabled:
                assert not (r.is_writable and r.is_executable), (
                    f"Invariant violation: Region {r.region_no} has RWX permissions"
                )


class MemoryManager:
    """Consolidated Physical Memory Manager, implementing the Tier 1 co_mem
    contract (system_memory.md) via the Tier 2 physical realization
    (runtime_memory.md)."""

    __slots__ = (
        "_page_mapping_callbacks",
        "_scheduler",
        "mpu",
        "page_registry",
        "partition_owners",
        "pool_base",
        "pool_size",
        "shm_allocated_bytes",
        "shm_pages",
        "shm_slots",
        "shm_storage",
        "total_allocated_bytes",
    )

    def __init__(self, scheduler: Scheduler):
        self._scheduler = scheduler
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
            capacity=_FB_CONF_MAX_SHM_PAGE_SLOTS
        )
        self.shm_storage = bytearray(FB_CONF_SHM_SIZE)
        self.shm_allocated_bytes = 0
        # One 4KB virtual reservation per block preserves page-granular ownership.
        self.shm_pages: StaticVector[ShmPageInfo] = StaticVector(
            capacity=_FB_CONF_MAX_SHM_PAGE_SLOTS
        )
        for i in range(_FB_CONF_MAX_SHM_PAGE_SLOTS):
            self.shm_pages.append(
                ShmPageInfo(
                    page_idx=i,
                    owner_id=0,
                    allocated=False,
                    allocated_bytes=0,
                    slot_count=0,
                    physical_addr=0,
                )
            )

    @property
    def current_task_id(self) -> int:
        """Returns the scheduler-authenticated identity for the active task."""
        return self._scheduler.current_task_id

    def register_page_mapping_callbacks(
        self,
        callbacks: PageMappingCallbacks,
    ) -> None:
        """Registers external page table / MMU listener callbacks for SHM page events."""
        self._page_mapping_callbacks = callbacks

    def _notify_shared_page_mapped(self, page_idx: int) -> None:
        callbacks = self._page_mapping_callbacks
        if callbacks is None:
            return
        page = self.shm_pages[page_idx]
        callbacks.on_map_page(page_idx, page.physical_addr, page.owner_id, page.allocated_bytes)

    def _set_shared_owner(self, page_idx: int, new_owner_id: int) -> None:
        previous_owner_id = self.page_registry.get_owner(page_idx)
        assert previous_owner_id is not None, (
            "Shared page must be registered before ownership changes"
        )
        if previous_owner_id == new_owner_id:
            return
        assert self.page_registry.update_owner(page_idx, new_owner_id)
        self.shm_pages[page_idx].owner_id = new_owner_id
        callbacks = self._page_mapping_callbacks
        if callbacks is not None:
            callbacks.on_owner_changed(
                page_idx,
                self.shm_pages[page_idx].physical_addr,
                previous_owner_id,
                new_owner_id,
            )

    def _find_shm_storage_offset(self, size: int) -> int | None:
        """Finds a reusable first-fit range in the fixed physical SHM pool."""
        candidate = 0
        while candidate + size <= FB_CONF_SHM_SIZE:
            conflict_end: int | None = None
            for page in self.shm_pages:
                if not page.allocated:
                    continue
                page_start = page.physical_addr - FB_CONF_MPU_R6_SHARED_MEMORY_BASE
                page_end = page_start + page.allocated_bytes
                if candidate < page_end and page_start < candidate + size:
                    conflict_end = page_end
                    break
            if conflict_end is None:
                return candidate
            candidate = conflict_end
        return None

    def init_manager(self, pool_base: int, pool_size: int) -> Result[bool]:
        assert pool_base % FB_WASM_PAGE_SIZE == 0, (
            f"pool_base 0x{pool_base:X} must be 64KB aligned (WasmPageAlignment)"
        )
        self.pool_base = pool_base
        self.pool_size = pool_size
        self.total_allocated_bytes = 0
        self.mpu = PMSAv8MPU(pool_base)
        return Result(value=True)

    def acquire_task_heap(self) -> Result[PartitionView]:
        owner = self._scheduler.current_task_id
        if self.partition_owners.view().find(owner) is not None:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.ALREADY_ACQUIRED,
                    RecoveryStrategy(
                        RecoveryAction.RETRY,
                        MemoryReasonCode.TASK_PARTITION_ALREADY_ACQUIRED,
                    ),
                )
            )

        slot_index = len(self.partition_owners)
        if slot_index >= len(FB_CONF_TASK_HEAP_SIZES):
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.POOL_EXHAUSTED,
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE,
                        MemoryReasonCode.PHYSICAL_MEMORY_POOL_EXHAUSTED,
                    ),
                )
            )

        slot_size = FB_CONF_TASK_HEAP_SIZES[slot_index]
        if self.total_allocated_bytes + slot_size > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.POOL_EXHAUSTED,
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE,
                        MemoryReasonCode.PHYSICAL_MEMORY_POOL_EXHAUSTED,
                    ),
                )
            )

        offset = sum(FB_CONF_TASK_HEAP_SIZES[:slot_index])
        base_addr = self.pool_base + offset
        pv = PartitionView(
            owner=owner,
            base_address=base_addr,
            size=slot_size,
            data=bytearray(slot_size),
        )
        self.partition_owners.insert(owner, pv)
        self.total_allocated_bytes += slot_size
        return Result(value=pv)

    def release_task_heap(self) -> None:
        caller_task_id = self._scheduler.current_task_id
        if self.partition_owners.view().find(caller_task_id) is None:
            return
        pv = self.partition_owners.remove(caller_task_id)
        if pv is not None:
            self.total_allocated_bytes -= pv.size

    def allocate_shared(
        self,
        size: int,
    ) -> Result[SharedBlock]:
        caller_task_id = self._scheduler.current_task_id
        assert caller_task_id != 0, "Shared block must be owned by an explicit task"
        if size <= 0 or size > FB_CONF_SHM_SIZE:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.INVALID_SIZE,
                    RecoveryStrategy(
                        RecoveryAction.RETRY,
                        MemoryReasonCode.REQUESTED_SHM_SIZE_OUT_OF_BOUNDS,
                    ),
                )
            )

        # Each block receives its own virtual page reservation. The physical
        # backing consumes only `size` bytes from the separate fixed SHM pool.
        target_page: ShmPageInfo | None = None
        if self.total_allocated_bytes + size > self.pool_size:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.SHM_EXHAUSTED,
                    RecoveryStrategy(RecoveryAction.DEGRADE, MemoryReasonCode.NO_FREE_SHM_PAGES),
                )
            )
        for page in self.shm_pages:
            if not page.allocated:
                target_page = page
                break

        if self.shm_allocated_bytes + size > FB_CONF_SHM_SIZE:
            physical_offset = None
        else:
            physical_offset = self._find_shm_storage_offset(size)
        if target_page is None or physical_offset is None:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.SHM_EXHAUSTED,
                    RecoveryStrategy(
                        RecoveryAction.DEGRADE,
                        MemoryReasonCode.ALL_SHM_PAGE_SLOTS_EXHAUSTED,
                    ),
                )
            )

        # Initialize new page exclusively for caller_task_id
        target_page.allocated = True
        target_page.owner_id = caller_task_id
        target_page.allocated_bytes = size
        target_page.slot_count = 1
        target_page.physical_addr = FB_CONF_MPU_R6_SHARED_MEMORY_BASE + physical_offset
        self.total_allocated_bytes += size
        self.shm_allocated_bytes += size

        # Register in page registry and notify listener
        base_addr = target_page.physical_addr
        self.page_registry.register_page(target_page.page_idx, caller_task_id, base_addr)
        if self._page_mapping_callbacks is not None:
            self._notify_shared_page_mapped(target_page.page_idx)

        # Allocate slot inside target_page
        slot_idx = 0
        shm_id = (target_page.page_idx << 8) | slot_idx
        slot_data = memoryview(self.shm_storage)[physical_offset : physical_offset + size]
        for index in range(size):
            slot_data[index] = 0
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

    def grant_shared(self, shm_id: int) -> bool:
        """Grants in-flight SHM block to the receiver task in page table (Grant phase)."""
        new_owner_task_id = self._scheduler.current_task_id
        assert new_owner_task_id != 0 and new_owner_task_id != FB_TASK_ID_FLIGHT, (
            "Grant target must be a concrete task identity"
        )
        assert self._scheduler.get_task(new_owner_task_id) is not None, (
            "Grant target must be a registered scheduler task"
        )
        slot = self.shm_slots.view().find(shm_id)
        if slot is None or not slot.allocated:
            return False
        current_owner = self.page_registry.get_owner(slot.page_idx)
        assert current_owner == FB_TASK_ID_FLIGHT or current_owner == new_owner_task_id, (
            "Shared block must be in flight before grant"
        )
        slot.owner = new_owner_task_id
        self._set_shared_owner(slot.page_idx, new_owner_task_id)
        return True

    def revoke_shared(self, shm_id: int) -> bool:
        """Revokes the current owner's mapping before a RESOURCE handle is sent."""
        slot = self.shm_slots.view().find(shm_id)
        if slot is None or not slot.allocated:
            return False
        current_owner = self._scheduler.current_task_id
        page_owner = self.page_registry.get_owner(slot.page_idx)
        if page_owner == FB_TASK_ID_FLIGHT:
            return True
        assert slot.owner == current_owner and page_owner == current_owner, (
            "Only the current SHM owner may revoke a RESOURCE handle"
        )
        self._set_shared_owner(slot.page_idx, FB_TASK_ID_FLIGHT)
        return True

    def claim(self, shm_id: int) -> Result[SharedBlock]:
        receiver_task_id = self._scheduler.current_task_id
        slot = self.shm_slots.view().find(shm_id)
        if slot is None or not slot.allocated:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.INVALID_SHM_ID,
                    RecoveryStrategy(
                        RecoveryAction.RETRY,
                        MemoryReasonCode.INVALID_OR_DEALLOCATED_SHM_ID,
                    ),
                )
            )

        page_idx = slot.page_idx
        current_owner = self.page_registry.get_owner(page_idx)
        if current_owner != receiver_task_id:
            return Result(
                error=MemoryErrorResult(
                    MemoryErrorCode.GRANT_NOT_COMPLETED,
                    RecoveryStrategy(
                        RecoveryAction.RETRY,
                        MemoryReasonCode.GRANT_PHASE_INCOMPLETE,
                    ),
                )
            )

        slot.owner = receiver_task_id
        self._notify_shared_page_mapped(page_idx)
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

    def rollback_transfer(self, shm_id: int) -> None:
        """Restores a shared block's original owner in the page table."""
        original_sender_id = self._scheduler.current_task_id
        slot = self.shm_slots.view().find(shm_id)
        if slot is not None:
            slot.owner = original_sender_id
            self._set_shared_owner(slot.page_idx, original_sender_id)
            self._notify_shared_page_mapped(slot.page_idx)

    def deallocate(self, addr: int) -> None:
        """Deallocate local static partition or slot. Owner enforced."""
        caller_task_id = self._scheduler.current_task_id
        owners_view = self.partition_owners.view()
        for i in range(len(owners_view.keys)):
            owner = owners_view.keys[i]
            pv = owners_view.values[i]
            if pv.base_address == addr:
                if owner == caller_task_id:
                    self.release_task_heap()
                return

    def _deallocate_shared_slot(self, page_idx: int, slot_idx: int, owner: int) -> None:
        shm_id = (page_idx << 8) | slot_idx
        slot = self.shm_slots.view().find(shm_id)
        if slot is None:
            return
        self.shm_slots.remove(shm_id)

        # Check whether the virtual reservation still has any live block.
        page = self.shm_pages[page_idx] if page_idx < len(self.shm_pages) else None
        has_remaining = False
        for other in self.shm_slots.view().values:
            if other.page_idx == page_idx:
                has_remaining = True
                break

        if not has_remaining and page is not None:
            if self._page_mapping_callbacks is not None:
                self._page_mapping_callbacks.on_unmap_page(page_idx, slot.base_address)
            self.page_registry.unregister_page(page_idx)
            page.allocated = False
            page.allocated_bytes = 0
            page.slot_count = 0
            page.owner_id = 0
            self.shm_allocated_bytes -= slot.size
            page.physical_addr = 0
            self.total_allocated_bytes -= slot.size
