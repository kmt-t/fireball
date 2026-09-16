"""Independent memory ports used by Tier 1 components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PageMappingCallbacks:
    """Tier 1 hook for observing shared-page mapping and ownership changes."""

    on_map_page: Callable[[int, int, int, int], None]
    # (virtual_page_idx, physical_base_addr, owner_id, mapped_size_bytes)
    on_owner_changed: Callable[[int, int, int, int], None]
    # (page_idx, physical_addr, previous_owner_id, new_owner_id)
    on_unmap_page: Callable[[int, int], None]
    # (page_idx, physical_addr)


class SharedBlock(Protocol):
    """Shared-memory block operations required by ownership boundaries."""

    data: memoryview
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
    def update_owner(self, page_idx: int, owner_id: int) -> bool: ...


class MemoryManager(Protocol):
    """Tier 1 memory port; Tier 2 owns the allocator implementation."""

    shm_slots: SharedSlotTable
    page_registry: PageRegistry

    def allocate_shared(self, size: int) -> MemoryResult: ...

    def claim(self, shm_id: int) -> MemoryResult: ...

    def grant_shared(self, shm_id: int) -> bool: ...

    def revoke_shared(self, shm_id: int) -> bool: ...

    def register_page_mapping_callbacks(self, callbacks: PageMappingCallbacks) -> None: ...
