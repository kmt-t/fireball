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
        ("exec_count", ctypes.c_void_p),
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
    """Owned trace/history buffers and a borrowed hotspot mask for one dispatch."""

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_arena_size",
        "_workspace_released",
        "block_history",
        "entries",
        "entry_count",
        "trace_source_address",
        "trackable_card_count",
        "trackable_mask",
        "trackable_shift",
    )

    entries: ctypes.Array
    entry_count: int
    trace_source_address: int
    trackable_mask: ctypes.Array
    trackable_shift: int
    trackable_card_count: int
    block_history: NativeBlockVisitHistory

    def __init__(
        self,
        entries: ctypes.Array,
        entry_count: int,
        trackable_mask: ctypes.Array,
        trackable_card_count: int,
        block_history: ctypes.Array,
        allocator: BumpAllocator | None = None,
        trackable_shift: int = 0,
    ) -> None:
        assert 0 <= entry_count <= len(entries)
        assert 0 <= trackable_card_count <= len(trackable_mask) * 8
        assert 0 <= trackable_shift < 32
        self.entries = entries
        self.trace_source_address = 0
        self.entry_count = entry_count
        self.trackable_mask = trackable_mask
        self.trackable_card_count = trackable_card_count
        self.trackable_shift = trackable_shift
        self.block_history = NativeBlockVisitHistory(block_history)
        self._allocator = allocator
        self._arena_offset: int | None = None
        self._arena_size = ctypes.sizeof(entries) + ctypes.sizeof(block_history)
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


class _DispatchBuffers(ctypes.Structure):
    _fields_ = (
        ("entries", ctypes.c_void_p),
        ("count", ctypes.c_uint32),
        ("mask", ctypes.c_void_p),
        ("cards", ctypes.c_uint32),
        ("shift", ctypes.c_uint32),
        ("mask_bytes", ctypes.c_uint32),
        ("history", ctypes.c_void_p),
        ("history_bytes", ctypes.c_uint64),
    )


def dispatch_for_test(
    interpreter, call_state, snapshot, threshold, execution_count, native_dispatcher
):
    from functools import partial

    from qa.private.jit_native_abi import _NATIVE_LIBRARY

    wrapper = _NATIVE_LIBRARY.fb_qa_dispatch
    wrapper.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    wrapper.restype = ctypes.c_int
    buffers = _DispatchBuffers(
        ctypes.addressof(snapshot.entries),
        snapshot.entry_count,
        ctypes.addressof(snapshot.trackable_mask),
        snapshot.trackable_card_count,
        snapshot.trackable_shift,
        ctypes.sizeof(snapshot.trackable_mask),
        snapshot.block_history.native_address,
        snapshot.block_history.native_bytes,
    )
    entry = partial(wrapper, ctypes.cast(native_dispatcher, ctypes.c_void_p), ctypes.byref(buffers))
    result = interpreter.run_native_dispatch(call_state, threshold, execution_count, entry)
    return (
        *result[:5],
        call_state.context._native_result.eligible_block_visits,
        result[5],
        snapshot.block_history,
    )
