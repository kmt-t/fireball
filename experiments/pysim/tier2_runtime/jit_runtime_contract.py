"""Tier 2 call contract implemented by Tier 3 JIT runtime components."""

from __future__ import annotations

import ctypes
from typing import Protocol, TypeAlias

from wasm_module import BasicBlock, Module


class NativeTraceDispatchEntry(ctypes.Structure):
    """ctypes mirror of one native C++ trace-dispatch descriptor."""

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


class NativeDispatchSnapshot:
    """Python-owned fixed buffers shared directly with the C++ dispatcher."""

    __slots__ = (
        "entries",
        "entry_count",
        "observed_visit_counts",
        "trackable_blocks",
        "trackable_count",
    )

    entries: ctypes.Array
    entry_count: int
    trackable_blocks: ctypes.Array
    trackable_count: int
    observed_visit_counts: ctypes.Array

    def __init__(
        self,
        entries: ctypes.Array,
        entry_count: int,
        trackable_blocks: ctypes.Array,
        trackable_count: int,
        observed_visit_counts: ctypes.Array,
    ) -> None:
        assert 0 <= entry_count <= len(entries)
        assert 0 <= trackable_count <= len(trackable_blocks)
        assert len(observed_visit_counts) >= trackable_count
        self.entries = entries
        self.entry_count = entry_count
        self.trackable_blocks = trackable_blocks
        self.trackable_count = trackable_count
        self.observed_visit_counts = observed_visit_counts


EMPTY_NATIVE_DISPATCH_SNAPSHOT = NativeDispatchSnapshot(
    (NativeTraceDispatchEntry * 0)(),
    0,
    (ctypes.c_uint32 * 0)(),
    0,
    (ctypes.c_uint8 * 0)(),
)


NativeBlockVisit: TypeAlias = tuple[int, int]


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


class JITRuntime(Protocol):
    """Tier 2 contract implemented by the selected Tier 3 JIT runtime."""

    yield_threshold: int
    exec_counter: int
    hotspot_profiling_enabled: bool

    def register_module(self, module: Module) -> None: ...

    def get_block(self, pc: int) -> BasicBlock | None: ...

    def is_trackable(self, pc: int) -> bool: ...

    def record_block_head(self, pc: int) -> bool: ...

    def on_yield(self) -> None: ...

    def idle_hook(self, budget: int = 4) -> int: ...

    def age_step(self) -> int: ...

    def lookup(self, pc: int) -> JITTrace | None: ...

    def native_dispatch_state(
        self, function_index: int
    ) -> NativeDispatchSnapshot: ...

    def record_native_block_visits(
        self, visits: tuple[NativeBlockVisit, ...], total_visits: int
    ) -> bool: ...

    def set_hotspot_profiling_enabled(self, enabled: bool) -> None: ...

    def find_trace(self, pc: int) -> JITTrace | None: ...

    def chain_length(self, trace: JITTrace) -> int: ...

    def terminal_trace(self, trace: JITTrace) -> JITTrace: ...

    def max_chain_stack_words(self, trace: JITTrace) -> int: ...

    def reset_stats(self) -> None: ...

    def flush_all(self) -> None: ...


__all__ = (
    "EMPTY_NATIVE_DISPATCH_SNAPSHOT",
    "JITRuntime",
    "JITTrace",
    "NativeBlockVisit",
    "NativeDispatchSnapshot",
    "NativeTraceDispatchEntry",
)
