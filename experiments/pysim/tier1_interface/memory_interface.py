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

    @property
    def owner(self) -> int: ...

    def u64_capacity(self) -> int: ...

    def read_u64(self, index: int) -> int: ...

    def read_entry(self, index: int) -> tuple[int, int]: ...

    def write_u64(self, index: int, val: int) -> None: ...

    def write_entry(self, index: int, key: int, val: int) -> None: ...

    def release(self) -> int: ...


class MemoryResult(Protocol):
    @property
    def is_err(self) -> bool: ...

    def unwrap(self) -> SharedBlock: ...


class MemoryManager(Protocol):
    """Tier 1 memory port; Tier 2 owns the allocator implementation."""

    def allocate_shared(self, size: int) -> MemoryResult: ...

    def claim(self, shm_id: int) -> MemoryResult: ...

    def grant_shared(self, shm_id: int) -> bool: ...

    def revoke_shared(self, shm_id: int) -> bool: ...
