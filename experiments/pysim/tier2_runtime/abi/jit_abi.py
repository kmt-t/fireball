"""Tier 2 ABI types shared with the optional Tier 3 JIT extension."""

from __future__ import annotations

import ctypes
from typing import Protocol

from bump_allocator import BumpAllocator


class NativeTraceDispatchEntry(ctypes.Structure):
    """ctypes view of one trace descriptor in the Tier 3 dispatch table."""

    __slots__ = ()

    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("entry_address", ctypes.c_void_p),
        ("byte_span", ctypes.c_uint32),
        ("result_words", ctypes.c_uint32),
        ("has_return_value", ctypes.c_uint32),
        ("stack_words", ctypes.c_uint32),
        ("frame_depth", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("loops_to", ctypes.c_uint32),
        ("chain_next_pc", ctypes.c_uint32),
        ("chain_stack_words", ctypes.c_uint32),
        ("promote_on_hit", ctypes.c_uint32),
    )


class NativeBlockVisitHistory:
    """Typed view over the fixed C buffer containing recent block PCs."""

    __slots__ = ("_storage",)

    def __init__(self, storage: ctypes.Array) -> None:
        self._storage = storage

    @property
    def native_address(self) -> int:
        return 0 if len(self._storage) == 0 else ctypes.addressof(self._storage)

    @property
    def native_bytes(self) -> int:
        return ctypes.sizeof(self._storage)

    def __len__(self) -> int:
        return len(self._storage)

    def __getitem__(self, index: int) -> int:
        assert 0 <= index < len(self)
        return int(self._storage[index])


class NativeDispatchSnapshot:
    """Fixed trace, hotspot-candidate, and history buffers for one dispatch."""

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_arena_size",
        "_workspace_released",
        "block_history",
        "entries",
        "entry_count",
        "trackable_blocks",
        "trackable_count",
    )

    entries: ctypes.Array
    entry_count: int
    trackable_blocks: ctypes.Array
    trackable_count: int
    block_history: NativeBlockVisitHistory

    def __init__(
        self,
        entries: ctypes.Array,
        entry_count: int,
        trackable_blocks: ctypes.Array,
        trackable_count: int,
        block_history: ctypes.Array,
        allocator: BumpAllocator | None = None,
    ) -> None:
        assert 0 <= entry_count <= len(entries)
        assert 0 <= trackable_count <= len(trackable_blocks)
        self.entries = entries
        self.entry_count = entry_count
        self.trackable_blocks = trackable_blocks
        self.trackable_count = trackable_count
        self.block_history = NativeBlockVisitHistory(block_history)
        self._allocator = allocator
        self._arena_offset: int | None = None
        self._arena_size = (
            ctypes.sizeof(entries) + ctypes.sizeof(trackable_blocks) + ctypes.sizeof(block_history)
        )
        self._workspace_released = False
        if allocator is not None and self._arena_size:
            self._arena_offset = allocator.acquire(self._arena_size, alignment=8)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def release_workspace(self) -> None:
        """Return the fixed dispatch buffers when the cache snapshot is replaced."""

        if self._allocator is None or self._workspace_released or self._arena_size == 0:
            return
        assert self._arena_offset is not None
        self._allocator.release(self._arena_offset, self._arena_size, alignment=8)
        self._workspace_released = True


EMPTY_NATIVE_DISPATCH_SNAPSHOT = NativeDispatchSnapshot(
    (NativeTraceDispatchEntry * 0)(),
    0,
    (ctypes.c_uint32 * 0)(),
    0,
    (ctypes.c_uint32 * 0)(),
)


class JITTrace(Protocol):
    """Executable trace state exposed across the Tier 2/Tier 3 boundary."""

    head_pc: int
    has_return_val: bool
    loops_to: int | None
    next_pc: int | None
    result_words: int
    stack_words: int
    exec_count: int

    def execute(
        self,
        ctx: ctypes.c_void_p,
        sp: ctypes.c_void_p,
        local_base: ctypes.c_void_p,
        tos: int,
    ) -> None: ...


__all__ = (
    "EMPTY_NATIVE_DISPATCH_SNAPSHOT",
    "JITTrace",
    "NativeBlockVisitHistory",
    "NativeDispatchSnapshot",
    "NativeTraceDispatchEntry",
)
