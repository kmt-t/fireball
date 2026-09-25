"""Tier 2 call contract implemented by Tier 3 JIT runtime components."""

from __future__ import annotations

import ctypes
from typing import Protocol, TypeAlias

from wasm_module import BasicBlock, Module

NativeTraceDispatchEntry: TypeAlias = tuple[
    int, int, int, int, int, int, int, int, int, int, int, int
]
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
    ) -> tuple[tuple[NativeTraceDispatchEntry, ...], tuple[int, ...]]: ...

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


__all__ = ("JITRuntime", "JITTrace", "NativeBlockVisit", "NativeTraceDispatchEntry")
