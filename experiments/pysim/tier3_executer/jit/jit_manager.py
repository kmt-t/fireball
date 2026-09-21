"""Tier 3 JIT hotspot detection, compilation scheduling, and cache ownership."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    JIT_CARD_SHIFT,
    RUNTIME_BLOCK_CACHE_SLOT_COUNT,
)
from control_flow import iter_block_ops
from jit_scoring import JIT_CANDIDATE_THRESHOLD
from system_containers import StaticVector
from wasm_module import BasicBlock, LocalWidthMap, Module, WasmOperand
from wasm_opcodes import BLOCK, END, IF, LOOP

from .jit_cache import (
    BlockCardMask,
    CardState,
    FunctionUpdateBitmap,
    HistoryRing,
    HotspotBitmap,
    JITMultiBufferCache,
    JITTrace,
)


class JITCompiler(Protocol):
    """Tier 3 trace compiler contract used by the cache manager."""

    def compile_trace(
        self,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_widths: LocalWidthMap,
    ) -> JITTrace | None: ...


def _module_code_lengths(module: Module) -> StaticVector[int]:
    """Build bounded per-function code lengths from loader-owned metadata."""

    lengths: StaticVector[int] = StaticVector(capacity=len(module.imports) + len(module.functions))
    for _ in module.imports:
        lengths.append(0)
    for index in range(len(module.functions)):
        lengths.append(len(module.code_for(len(module.imports) + index)))
    return lengths


def _empty_block_slots() -> StaticVector[tuple[int, BasicBlock | None] | None]:
    """Create the fixed direct-mapped block lookup cache."""

    slots: StaticVector[tuple[int, BasicBlock | None] | None] = StaticVector(
        capacity=RUNTIME_BLOCK_CACHE_SLOT_COUNT
    )
    for _ in range(RUNTIME_BLOCK_CACHE_SLOT_COUNT):
        slots.append(None)
    return slots


class JITRuntimeManager:
    """Own all Tier 3 hotspot and compiled-trace state for one module.

    The manager is deliberately independent from ``RuntimeEngine``.  The
    engine supplies execution boundaries through the ``JITRuntime`` protocol;
    this component owns the card state machine, queue, cache rotation, and
    trace lookup policy.
    """

    __slots__ = (
        "_fast_block_slots",
        "aging_bytes_scanned",
        "aging_scan_bytes",
        "aging_step_units",
        "aging_steps",
        "aging_units_processed",
        "bitmap",
        "cache",
        "candidate_threshold",
        "compile_queue",
        "compile_queue_capacity",
        "jit_compiler",
        "min_trace_bytes",
        "module",
        "ring",
        "trackable",
        "update_bitmap",
        "yield_threshold",
        "exec_counter",
    )

    def __init__(
        self,
        jit_compiler: JITCompiler | None = None,
        yield_threshold: int = 16,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: Sequence[int] = (),
        min_trace_bytes: int | None = None,
        candidate_threshold: int = JIT_CANDIDATE_THRESHOLD,
        compile_queue_capacity: int = 4,
        aging_step_units: int = FB_CONF_JIT_AGING_STEP_UNITS,
        aging_scan_bytes: int = FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    ):
        assert yield_threshold >= 1
        assert candidate_threshold >= 0
        assert compile_queue_capacity >= 1
        assert aging_step_units >= 1 and aging_scan_bytes >= 1
        self.yield_threshold = yield_threshold
        self.candidate_threshold = candidate_threshold
        self.compile_queue_capacity = compile_queue_capacity
        self.aging_step_units = aging_step_units
        self.aging_scan_bytes = aging_scan_bytes
        self.jit_compiler = jit_compiler
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.update_bitmap = FunctionUpdateBitmap(function_count=len(code_lengths))
        self.ring = HistoryRing()
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.cache.on_rotate = self.age_step
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.module: Module | None = None
        self._fast_block_slots = _empty_block_slots()
        self.exec_counter = 0
        self.aging_steps = 0
        self.aging_units_processed = 0
        self.aging_bytes_scanned = 0

    @property
    def card_shift(self) -> int:
        """Return the immutable card granularity used by this manager."""

        return self.bitmap.card_shift

    def register_module(self, module: Module) -> None:
        """Bind loader metadata and initialize all JIT-owned indexes."""

        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        code_lengths = _module_code_lengths(module)
        self.bitmap = HotspotBitmap(card_shift=self.card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=self.card_shift, code_lengths=code_lengths)
        self.update_bitmap = FunctionUpdateBitmap(function_count=len(code_lengths))
        self.ring = HistoryRing()
        self.compile_queue = StaticVector(capacity=self.compile_queue_capacity)
        self._fast_block_slots = _empty_block_slots()
        self.exec_counter = 0
        self.trackable.clear()
        for block in module.blocks:
            if (
                block.byte_span >= self.min_trace_bytes
                and block.jit_score >= self.candidate_threshold
            ):
                self.trackable.mark(block.head_pc)

    def get_block(self, pc: int) -> BasicBlock | None:
        """Resolve a block through the fixed direct-mapped Tier 3 index cache."""

        temp = pc ^ (pc >> 16)
        temp = temp ^ (temp >> 8)
        temp = temp ^ (temp >> 4)
        slot = temp & (RUNTIME_BLOCK_CACHE_SLOT_COUNT - 1)
        cached = self._fast_block_slots[slot]
        if cached is not None and cached[0] == pc:
            return cached[1]
        block = self.module.get_block(pc) if self.module is not None else None
        self._fast_block_slots[slot] = (pc, block)
        return block

    def _compile_trace(self, pc: int, block: BasicBlock) -> JITTrace | None:
        """Compile one trackable block from loader metadata."""

        assert self.module is not None
        assert self.jit_compiler is not None
        function_index = pc >> 16
        function = self.module.functions[function_index - len(self.module.imports)]
        assert function.local_width_map_cache is not None
        code = self.module.code_for(function_index)
        next_pc = block.next_pc
        loops_to = block.loops_to
        terminator_offset = (pc & 0xFFFF) + block.byte_span
        terminator = code[terminator_offset] if terminator_offset < len(code) else END
        # A trace never pushes a structured control frame.  Leave its delimiter
        # for the interpreter so the frame stack remains an exact prefix.
        if terminator == BLOCK or terminator == LOOP or terminator == IF:
            next_pc = None
            loops_to = None
        return self.jit_compiler.compile_trace(
            pc,
            iter_block_ops(code, pc & 0xFFFF, block.byte_span),
            next_pc,
            loops_to,
            block.byte_span,
            function.local_width_map_cache,
        )

    def is_trackable(self, pc: int) -> bool:
        """Return whether the loader-selected candidate bit is set."""

        return self.trackable.is_marked(pc)

    def card_state(self, pc: int) -> int:
        """Return the current two-bit hotspot state for a block PC."""

        return self.bitmap.get_state(pc)

    def record_block_head(self, pc: int) -> bool:
        """Record one candidate execution and report a yield threshold hit."""

        if not self.trackable.is_marked(pc):
            return False
        self.ring.record(pc)
        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.on_yield()
            return True
        return False

    def on_yield(self) -> None:
        """Promote history cards and queue newly hot blocks for compilation."""

        self.exec_counter = 0
        for pc in self.ring.drain():
            new_state = self.bitmap.touch(pc)
            if new_state == CardState.EXECUTED:
                self.update_bitmap.mark(self.bitmap.function_of(pc))
            if new_state == CardState.HOT and not self.compile_queue.contains(pc):
                self.compile_queue.push_back(pc)
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()

    def age_step(self) -> int:
        """Perform one bounded card-aging sweep after a cache rotation."""

        update = self.update_bitmap
        self.aging_steps += 1
        if update.unit_count == 0:
            return 0
        decayed = 0
        units = 0
        scanned = 0
        scan_limit = min(self.aging_scan_bytes, update.unit_count)
        while units < self.aging_step_units and scanned < scan_limit:
            bits = update.unit(update.cursor)
            if bits != 0:
                for bit in range(FunctionUpdateBitmap.UNIT_FUNCTIONS):
                    if (bits >> bit) & 1:
                        func_idx = update.cursor * FunctionUpdateBitmap.UNIT_FUNCTIONS + bit
                        decayed += self.bitmap.decay_executed_function(func_idx)
                        update.unmark(func_idx)
                units += 1
            update.cursor = 0 if update.cursor + 1 == update.unit_count else update.cursor + 1
            scanned += 1
        self.aging_units_processed += units
        self.aging_bytes_scanned += scanned
        return decayed

    def idle_hook(self, budget: int = 4) -> int:
        """Compile queued traces in reverse execution order during an idle slice."""

        assert budget >= 0
        compiled_count = 0
        while self.compile_queue and compiled_count < budget:
            pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(pc) is not None:
                self.bitmap.mark_compiled(pc)
                continue
            trace = None
            if self.jit_compiler is not None:
                block = self.get_block(pc)
                assert block is not None
                trace = self._compile_trace(pc, block)
            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(pc)
                compiled_count += 1
            else:
                self.trackable.unmark(pc)
        return compiled_count

    def drain_compile_queue(self) -> int:
        """Compile all currently queued traces."""

        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def lookup(self, pc: int) -> JITTrace | None:
        """Apply the candidate/card filter and look up one resident trace."""

        if not self.trackable.is_marked(pc):
            return None
        if self.bitmap.get_state(pc) != CardState.COMPILED:
            return None
        return self.cache.lookup(pc)

    def find_trace(self, pc: int) -> JITTrace | None:
        """Find a resident trace without applying the hotspot filter."""

        return self.cache.find_trace(pc)

    def insert_trace(self, trace: JITTrace) -> bool:
        """Install a test or compiler-produced trace into the Tier 3 cache."""

        return self.cache.insert(trace)

    def mark_compiled(self, pc: int) -> None:
        """Synchronize a card with a trace installed by the caller."""

        self.bitmap.mark_compiled(pc)

    def unmark_trackable(self, pc: int) -> None:
        """Remove a permanently unsupported block from the candidate mask."""

        self.trackable.unmark(pc)

    def chain_length(self, trace: JITTrace) -> int:
        """Return the number of resident bodies reached by a native chain."""

        count = 1
        current = trace
        while current.chain_next is not None:
            successor = self.cache.find_trace(current.chain_next)
            assert successor is not None
            current = successor
            count += 1
            assert count <= 1024
        return count

    def terminal_trace(self, trace: JITTrace) -> JITTrace:
        """Resolve the final resident body of a native chain."""

        current = trace
        count = 1
        while current.chain_next is not None:
            successor = self.cache.find_trace(current.chain_next)
            assert successor is not None
            current = successor
            count += 1
            assert count <= 1024
        return current

    def max_chain_stack_words(self, trace: JITTrace) -> int:
        """Return the largest raw stack write required by a resident chain."""

        words = trace.stack_words
        current = trace
        while current.chain_next is not None:
            successor = self.cache.find_trace(current.chain_next)
            assert successor is not None
            current = successor
            if current.stack_words > words:
                words = current.stack_words
        return words

    def flush_all(self) -> None:
        """Invalidate all resident traces without running card aging."""

        self.cache.flush_all()

    def reset_stats(self) -> None:
        """Reset per-trace execution counters owned by the JIT component."""

        for bank in self.cache.banks:
            for _, trace in bank.traces:
                trace.exec_count = 0

    def _handle_eviction(self, purged_pcs: StaticVector[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)


__all__ = ("JITCompiler", "JITRuntimeManager")
