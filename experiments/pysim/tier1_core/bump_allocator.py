"""Fixed-capacity bump allocator owned by a runtime instance."""

from __future__ import annotations

from config import FB_CONF_RUNTIME_BUMP_ARENA_BYTES


class BumpAllocator:
    """Bounded byte arena with watermark rollback and O(1) reset."""

    __slots__ = ("capacity", "offset", "storage")

    def __init__(self, capacity: int = FB_CONF_RUNTIME_BUMP_ARENA_BYTES) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self.offset = 0
        self.storage = bytearray(capacity)

    def allocate(self, size: int, alignment: int = 4) -> int:
        assert size >= 0
        assert alignment > 0 and alignment & (alignment - 1) == 0
        aligned_offset = (self.offset + alignment - 1) & ~(alignment - 1)
        end = aligned_offset + size
        assert end <= self.capacity, "runtime bump allocator capacity exceeded"
        self.offset = end
        return aligned_offset

    def save(self) -> int:
        return self.offset

    def restore(self, saved_offset: int) -> None:
        """Roll back allocations from the active load transaction."""

        assert 0 <= saved_offset <= self.offset
        self.offset = saved_offset

    def reset(self) -> None:
        """Release the arena when its owning runtime is destroyed."""

        self.offset = 0
