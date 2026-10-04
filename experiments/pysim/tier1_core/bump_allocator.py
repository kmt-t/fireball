"""Fixed-capacity bump allocator owned by a runtime instance."""

from __future__ import annotations

import ctypes

from config import FB_CONF_RUNTIME_BUMP_ARENA_BYTES

_WORKSPACE_SLOT_COUNT = 64


class BumpAllocator:
    """Bounded resource accounting with watermark rollback and reusable workspaces."""

    __slots__ = (
        "_workspace_alignments",
        "_workspace_offsets",
        "_workspace_sizes",
        "_workspace_states",
        "capacity",
        "offset",
    )

    def __init__(self, capacity: int = FB_CONF_RUNTIME_BUMP_ARENA_BYTES) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self.offset = 0
        # State 0 is unused, 1 is leased, and 2 is available for reuse.
        self._workspace_offsets = (ctypes.c_uint64 * _WORKSPACE_SLOT_COUNT)()
        self._workspace_sizes = (ctypes.c_uint64 * _WORKSPACE_SLOT_COUNT)()
        self._workspace_alignments = (ctypes.c_uint32 * _WORKSPACE_SLOT_COUNT)()
        self._workspace_states = (ctypes.c_uint8 * _WORKSPACE_SLOT_COUNT)()

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

    def acquire(self, size: int, alignment: int = 4) -> int:
        """Acquire a reusable fixed-size runtime workspace from the arena."""

        assert size > 0
        assert alignment > 0 and alignment & (alignment - 1) == 0
        reusable_slot = -1
        empty_slot = -1
        for slot in range(_WORKSPACE_SLOT_COUNT):
            state = self._workspace_states[slot]
            if (
                state == 2
                and self._workspace_sizes[slot] == size
                and self._workspace_alignments[slot] == alignment
            ):
                reusable_slot = slot
                break
            if state == 0 and empty_slot < 0:
                empty_slot = slot
        if reusable_slot >= 0:
            self._workspace_states[reusable_slot] = 1
            return int(self._workspace_offsets[reusable_slot])
        assert empty_slot >= 0, "runtime workspace tracking capacity exceeded"
        offset = self.allocate(size, alignment)
        self._workspace_offsets[empty_slot] = offset
        self._workspace_sizes[empty_slot] = size
        self._workspace_alignments[empty_slot] = alignment
        self._workspace_states[empty_slot] = 1
        return offset

    def release(self, offset: int, size: int, alignment: int = 4) -> None:
        """Return a reusable runtime workspace after its execution ends."""

        assert alignment > 0 and alignment & (alignment - 1) == 0
        for slot in range(_WORKSPACE_SLOT_COUNT):
            if (
                self._workspace_states[slot] == 1
                and self._workspace_offsets[slot] == offset
                and self._workspace_sizes[slot] == size
                and self._workspace_alignments[slot] == alignment
            ):
                self._workspace_states[slot] = 2
                return
        assert False, "runtime workspace was not leased"

    def restore(self, saved_offset: int) -> None:
        """Roll back allocations from the active load transaction."""

        assert 0 <= saved_offset <= self.offset
        for slot in range(_WORKSPACE_SLOT_COUNT):
            state = self._workspace_states[slot]
            offset = self._workspace_offsets[slot]
            size = self._workspace_sizes[slot]
            if state == 1:
                assert offset + size <= saved_offset, "cannot roll back an active runtime workspace"
            elif state == 2 and offset + size > saved_offset:
                self._workspace_states[slot] = 0
        self.offset = saved_offset

    def reset(self) -> None:
        """Release the arena when its owning runtime is destroyed."""

        for slot in range(_WORKSPACE_SLOT_COUNT):
            assert self._workspace_states[slot] != 1, (
                "cannot reset an arena with active runtime workspaces"
            )
            self._workspace_states[slot] = 0
        self.offset = 0
