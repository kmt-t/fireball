"""Independent memory ports used by Tier 1 components."""

from __future__ import annotations

from typing import Protocol


class SharedBlock(Protocol):
    """Shared-memory block operations required by ownership boundaries."""

    data: bytearray
    owner: int

    def u64_capacity(self) -> int: ...

    def read_u64(self, index: int) -> int: ...

    def read_entry(self, index: int) -> tuple[int, int]: ...

    def write_u64(self, index: int, value: int) -> None: ...

    def write_entry(self, index: int, key: int, value: int) -> None: ...

    def release(self) -> int: ...


class MemoryResult(Protocol):
    is_err: bool

    def unwrap(self) -> SharedBlock: ...


class SharedSlot(Protocol):
    allocated: bool
    page_idx: int


class SharedSlotTable(Protocol):
    def find(self, key: int) -> SharedSlot | None: ...


class PageRegistry(Protocol):
    def update_owner(self, page_idx: int, owner_id: int) -> None: ...


class MemoryManager(Protocol):
    """Tier 1 memory port; Tier 2 owns the allocator implementation."""

    shm_slots: SharedSlotTable
    page_registry: PageRegistry

    def allocate_shared(self, size: int) -> MemoryResult: ...

    def claim(self, shm_id: int) -> MemoryResult: ...

    def grant_shared(self, shm_id: int) -> bool: ...
