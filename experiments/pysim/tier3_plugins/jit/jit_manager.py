"""Tier 3 JIT hotspot detection, compilation scheduling, and cache ownership."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from typing import Protocol

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    FB_CONF_MAX_BASIC_BLOCKS,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CARD_SHIFT,
    JIT_X64_TRACE_HEADER_BYTES,
    RUNTIME_BLOCK_CACHE_SLOT_COUNT,
)
from system_containers import StaticVector
from tier2_runtime.abi.jit_abi import (
    EMPTY_NATIVE_DISPATCH_SNAPSHOT,
    NativeBlockVisitHistory,
    NativeDispatchSnapshot,
    NativeTraceDispatchEntry,
)
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.control_flow import is_control_terminator
from tier2_runtime.interpreter.interpreter import (
    NATIVE_DISPATCH_CALL_BOUNDARY,
    NATIVE_DISPATCH_OLDEST_TRACE,
    NATIVE_DISPATCH_YIELD,
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeDispatchEntryPoint,
    NativeInterpreter,
)
from tier2_runtime.runtime.engine import RuntimeBoundaryResult
from tier2_runtime.wasm.jit_scoring import JIT_CANDIDATE_THRESHOLD
from tier2_runtime.wasm.module import BasicBlock, Module
from tier2_runtime.wasm.opcodes import END

from .jit_cache import (
    BlockCardMask,
    CardState,
    CardUpdateBitmap,
    HistoryRing,
    HotspotBitmap,
    JITMultiBufferCache,
    JITTrace,
)


class JITCompiler(Protocol):
    """Tier 3 trace compiler contract used by the cache manager."""

    def compile_wasm_trace(
        self,
        code_address: int,
        code_bytes: int,
        code_offset: int,
        byte_span: int,
        next_pc: int | None,
        loops_to: int | None,
        local_width_address: int,
        local_width_bytes: int,
        local_count: int,
        slot_words: int,
    ) -> tuple[bytes, int, int, int, int, int, int, int, int] | None: ...

    def build_runtime_trace(
        self,
        head_pc: int,
        native_result: tuple[bytes, int, int, int, int, int, int, int, int],
        next_pc: int | None,
        loops_to: int | None,
    ) -> JITTrace | None: ...


def _module_code_lengths(module: Module) -> StaticVector[int]:
    """Build bounded per-function code lengths from loader-owned metadata."""

    lengths: StaticVector[int] = StaticVector(capacity=len(module.imports) + len(module.functions))
    for _ in module.imports:
        lengths.append(0)
    for index in range(len(module.functions)):
        lengths.append(len(module.code_for(len(module.imports) + index)))
    return lengths


def _module_function_pc_bases(module: Module) -> StaticVector[int]:
    """Return per-function Code-section PCs, with zero-sized import slots."""
    bases: StaticVector[int] = StaticVector(capacity=len(module.imports) + len(module.functions))
    for _ in module.imports:
        bases.append(0)
    for local_index in range(len(module.functions)):
        bases.append(module.function_pc_offset(len(module.imports) + local_index))
    return bases


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
        "_compile_code_leases",
        "_compile_width_buffers",
        "_fast_block_slots",
        "_hotspot_profiling_enabled",
        "_last_analyzed_overwrite_count",
        "_native_dispatch_cache_key",
        "_native_dispatch_cache_snapshot",
        "_trackable_generation",
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
        "exec_counter",
        "history_capacity",
        "history_overwritten_count",
        "jit_compiler",
        "last_history_analysis_approximate",
        "min_trace_bytes",
        "module",
        "module_id",
        "ring",
        "trackable",
        "update_bitmap",
        "yield_threshold",
    )

    def __init__(
        self,
        jit_compiler: JITCompiler | None = None,
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: Sequence[int] = (),
        min_trace_bytes: int | None = None,
        candidate_threshold: int = JIT_CANDIDATE_THRESHOLD,
        compile_queue_capacity: int = 4,
        aging_step_units: int = FB_CONF_JIT_AGING_STEP_UNITS,
        aging_scan_bytes: int = FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
        hotspot_profiling_enabled: bool = True,
        history_capacity: int = 32,
        module_id: int = 0,
    ):
        assert 1 <= yield_threshold <= 0xFFFFFFFF
        assert candidate_threshold >= 0
        assert compile_queue_capacity >= 1
        assert aging_step_units >= 1 and aging_scan_bytes >= 1
        assert history_capacity >= 1
        assert 0 <= module_id <= 0xFFFF_FFFF
        self.yield_threshold = yield_threshold
        self.candidate_threshold = candidate_threshold
        self.compile_queue_capacity = compile_queue_capacity
        self.aging_step_units = aging_step_units
        self.aging_scan_bytes = aging_scan_bytes
        self.jit_compiler = jit_compiler
        self._hotspot_profiling_enabled = hotspot_profiling_enabled
        self.history_capacity = history_capacity
        self.module_id = module_id
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.update_bitmap = CardUpdateBitmap(card_count=self.bitmap.card_count)
        self.ring = HistoryRing(capacity=history_capacity)
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.cache.on_rotate = self.age_step
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.module: Module | None = None
        self._fast_block_slots = _empty_block_slots()
        self._native_dispatch_cache_snapshot = EMPTY_NATIVE_DISPATCH_SNAPSHOT
        self._compile_code_leases: StaticVector[BufferLease] = StaticVector(
            capacity=len(code_lengths)
        )
        self._compile_width_buffers: StaticVector[ctypes.Array] = StaticVector(
            capacity=len(code_lengths)
        )
        self._native_dispatch_cache_key: tuple[int, int, bool] | None = None
        self._trackable_generation = 0
        self.exec_counter = 0
        self.history_overwritten_count = 0
        self.last_history_analysis_approximate = False
        self._last_analyzed_overwrite_count = 0
        self.aging_steps = 0
        self.aging_units_processed = 0
        self.aging_bytes_scanned = 0

    @property
    def card_shift(self) -> int:
        """Return the immutable card granularity used by this manager."""

        return self.bitmap.card_shift

    @property
    def hotspot_profiling_enabled(self) -> bool:
        """Return the immutable hotspot observation choice for this JIT Runtime."""

        return self._hotspot_profiling_enabled

    def register_module(self, module: Module, module_id: int | None = None) -> None:
        """Bind loader metadata and initialize all JIT-owned indexes."""

        for lease in self._compile_code_leases:
            lease.release()
        if module_id is not None:
            assert 0 <= module_id <= 0xFFFF_FFFF
            self.module_id = module_id
        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        code_lengths = _module_code_lengths(module)
        function_pc_bases = _module_function_pc_bases(module)
        self._compile_code_leases = StaticVector(capacity=len(module.functions))
        self._compile_width_buffers = StaticVector(capacity=len(module.functions))
        for local_index in range(len(module.functions)):
            function_index = len(module.imports) + local_index
            code = module.code_for(function_index)
            self._compile_code_leases.append(BufferLease(memoryview(code)))
            function = module.functions[local_index]
            assert function.local_width_map_cache is not None
            widths_view = function.local_width_map_cache.raw_view
            width_buffer = (
                (ctypes.c_uint8 * len(widths_view)).from_buffer(widths_view)
                if len(widths_view) > 0
                else (ctypes.c_uint8 * 0)()
            )
            self._compile_width_buffers.append(width_buffer)
        self.bitmap = HotspotBitmap(
            card_shift=self.card_shift,
            code_lengths=code_lengths,
            function_pc_bases=function_pc_bases,
        )
        self.trackable = BlockCardMask(
            card_shift=self.card_shift,
            code_lengths=code_lengths,
            function_pc_bases=function_pc_bases,
        )
        self.update_bitmap = CardUpdateBitmap(card_count=self.bitmap.card_count)
        self.ring = HistoryRing(capacity=self.history_capacity)
        self.compile_queue = StaticVector(capacity=self.compile_queue_capacity)
        self._fast_block_slots = _empty_block_slots()
        self.exec_counter = 0
        self.history_overwritten_count = 0
        self.last_history_analysis_approximate = False
        self._last_analyzed_overwrite_count = 0
        self.trackable.clear()
        for block in module.blocks:
            if (
                block.byte_span >= self.min_trace_bytes
                and block.jit_score >= self.candidate_threshold
            ):
                self.trackable.mark(block.head_pc)
        self._trackable_generation += 1
        self._native_dispatch_cache_key = None

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
        function_index = block.func_index
        function = self.module.functions[function_index - len(self.module.imports)]
        assert function.local_width_map_cache is not None
        code = self.module.code_for(function_index)
        block_offset = pc - self.module.function_pc_offset(function_index)
        terminator_offset = block_offset + block.byte_span
        terminator = code[terminator_offset] if terminator_offset < len(code) else END
        # Control instructions stay with the C++ Interpreter handler. A
        # straight-line successor may use the shared common-code chain dispatcher.
        next_pc = None if is_control_terminator(terminator) else block.next_pc
        local_index = function_index - len(self.module.imports)
        widths_view = function.local_width_map_cache.raw_view
        width_buffer = self._compile_width_buffers[local_index]
        native_result = self.jit_compiler.compile_wasm_trace(
            self._compile_code_leases[local_index].address,
            len(code),
            block_offset,
            block.byte_span,
            next_pc,
            None,
            ctypes.addressof(width_buffer) if len(widths_view) > 0 else 0,
            len(widths_view),
            function.local_width_map_cache.count,
            function.local_width_map_cache.slot_words,
        )
        if native_result is None:
            return None
        return self.jit_compiler.build_runtime_trace(
            pc,
            native_result,
            next_pc,
            None,
        )

    def native_dispatch_state(self) -> NativeDispatchSnapshot:
        """Return module-wide ctypes tables consumed by native nested calls."""

        cache_key = (
            self.cache.generation,
            self._trackable_generation,
            self.hotspot_profiling_enabled,
        )
        if cache_key == self._native_dispatch_cache_key:
            return self._native_dispatch_cache_snapshot

        active = self.cache.active.traces
        warm = self.cache.warm.traces
        oldest = self.cache.oldest.traces
        trace_capacity = JIT_CACHE_BANK_COUNT * self.cache.active.entry_capacity
        entries = (NativeTraceDispatchEntry * trace_capacity)()
        entry_count = 0
        active_index = 0
        warm_index = 0
        oldest_index = 0
        no_pc = 0xFFFF_FFFF
        module = self.module
        assert module is not None
        while active_index < len(active) or warm_index < len(warm) or oldest_index < len(oldest):
            active_item = active[active_index] if active_index < len(active) else None
            warm_item = warm[warm_index] if warm_index < len(warm) else None
            oldest_item = oldest[oldest_index] if oldest_index < len(oldest) else None
            selected = active_item
            selected_bank = 0
            if warm_item is not None and (selected is None or warm_item[0] < selected[0]):
                selected = warm_item
                selected_bank = 1
            if oldest_item is not None and (selected is None or oldest_item[0] < selected[0]):
                selected = oldest_item
                selected_bank = 2
            if selected_bank == 0:
                active_index += 1
            elif selected_bank == 1:
                warm_index += 1
            else:
                oldest_index += 1
            assert selected is not None
            head_pc, trace = selected
            block = self.get_block(head_pc)
            assert block is not None and trace.raw_addr is not None
            assert entry_count < trace_capacity
            entries[entry_count] = NativeTraceDispatchEntry(
                head_pc=head_pc,
                entry_address=trace.raw_addr,
                byte_span=block.byte_span,
                result_words=trace.result_words,
                has_return_value=int(trace.has_return_val),
                stack_words=trace.stack_words,
                frame_depth=block.frame_depth,
                next_pc=no_pc if block.next_pc is None else block.next_pc,
                loops_to=no_pc if block.loops_to is None else block.loops_to,
                chain_next_pc=no_pc if trace.chain_next is None else trace.chain_next,
                chain_stack_words=self.max_chain_stack_words(trace),
                promote_on_hit=int(selected_bank == 2),
            )
            entry_count += 1
        trackable_capacity = FB_CONF_MAX_BASIC_BLOCKS if self.hotspot_profiling_enabled else 0
        trackable_heads = (ctypes.c_uint32 * trackable_capacity)()
        trackable_count = 0
        if self.hotspot_profiling_enabled:
            for block in module.blocks:
                if self.trackable.is_marked(block.head_pc):
                    assert trackable_count < FB_CONF_MAX_BASIC_BLOCKS
                    trackable_heads[trackable_count] = block.head_pc
                    trackable_count += 1
        block_history = (ctypes.c_uint32 * self.history_capacity)()
        self._native_dispatch_cache_snapshot.release_workspace()
        self._native_dispatch_cache_snapshot = NativeDispatchSnapshot(
            entries,
            entry_count,
            trackable_heads,
            trackable_count,
            block_history,
            allocator=module.allocator,
        )
        self._native_dispatch_cache_key = cache_key
        return self._native_dispatch_cache_snapshot

    def record_native_block_visits(
        self, visits: NativeBlockVisitHistory, total_visits: int
    ) -> bool:
        """Append the interpreter's ordered block history without analyzing it."""

        if not self.hotspot_profiling_enabled:
            assert total_visits == 0
            return False
        assert total_visits >= 0
        retained_visits = min(total_visits, len(visits))
        assert total_visits == 0 or len(visits) > 0
        oldest_visit = total_visits - retained_visits
        for index in range(retained_visits):
            pc = visits[(oldest_visit + index) % len(visits)]
            assert self.trackable.is_marked(pc)
            self.ring.record(self.module_id, pc)
        assert retained_visits == min(total_visits, self.history_capacity)
        self.ring.record_dropped(total_visits - retained_visits)
        self.exec_counter += total_visits
        return False

    def run_boundary(
        self,
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int,
        native_dispatcher: NativeDispatchEntryPoint,
    ) -> RuntimeBoundaryResult:
        """Drive the interpreter/JIT dispatcher through one plugin boundary."""

        assert interp.debugger is None, "debugger-enabled calls must bypass the JIT plugin"
        assert idle_budget >= 0
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step_native(call_state))
        assert call_state._frame is not None
        frame = call_state._frame

        body_count = 0
        dispatcher_trace_transitions = 0
        control_count = 0
        interpreted_block_count = 0
        native_status = 0
        while True:
            dispatch_snapshot = self.native_dispatch_state()
            (
                native_status,
                _dispatch_count,
                native_body_count,
                native_trace_transitions,
                native_control_count,
                eligible_block_visits,
                native_interpreted_block_count,
                block_visits,
            ) = interp.run_native_dispatch(
                call_state,
                dispatch_snapshot,
                self.yield_threshold,
                self.exec_counter,
                native_dispatcher=native_dispatcher,
            )
            body_count += native_body_count
            dispatcher_trace_transitions += native_trace_transitions
            control_count += native_control_count
            interpreted_block_count += native_interpreted_block_count
            if native_status == NATIVE_DISPATCH_OLDEST_TRACE:
                oldest_pc = call_state.current_pc()
                assert self.lookup(oldest_pc) is not None, (
                    "native dispatcher reported an Oldest trace absent from the JIT cache"
                )
            if self.hotspot_profiling_enabled and eligible_block_visits > 0:
                self.record_native_block_visits(block_visits, eligible_block_visits)
            if native_status != NATIVE_DISPATCH_OLDEST_TRACE:
                break
        if native_status == 0:
            call_state = interp.resolve_native_call_boundary(call_state)
        yield_requested = native_status == NATIVE_DISPATCH_YIELD
        self.on_interpreter_exit(yield_requested)
        if self.has_pending_compilation() and (
            yield_requested or len(self.compile_queue) >= self.compile_queue_capacity
        ):
            self.idle_hook(budget=idle_budget)

        if native_status == NATIVE_DISPATCH_YIELD:
            frame.context.loop_jump_count = 0
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
            or native_status == NATIVE_DISPATCH_OLDEST_TRACE
            or native_status == NATIVE_DISPATCH_CALL_BOUNDARY
        )
        if not valid_native_status:
            assert False, f"unexpected native JIT dispatch status: {native_status}"
        return RuntimeBoundaryResult(
            call_state,
            yield_requested,
            interpreted_block_count=interpreted_block_count,
            trace_execution_count=body_count,
            control_handler_count=control_count,
            trace_transition_count=dispatcher_trace_transitions,
        )

    def is_trackable(self, pc: int) -> bool:
        """Return whether the loader-selected candidate bit is set."""

        return self.trackable.is_marked(pc)

    def card_state(self, pc: int) -> int:
        """Return the current two-bit hotspot state for a block PC."""

        return self.bitmap.get_state(pc)

    def record_block_head(self, pc: int) -> bool:
        """Record one candidate execution without changing the execution boundary."""

        if not self.hotspot_profiling_enabled:
            return False
        if not self.trackable.is_marked(pc):
            return False
        self.ring.record(self.module_id, pc)
        self.exec_counter += 1
        return False

    def on_interpreter_exit(self, yield_requested: bool) -> None:
        """Analyze the completed interpreter history once at its execution boundary."""
        overwritten_count = self.ring.dropped
        self.history_overwritten_count = overwritten_count
        self.last_history_analysis_approximate = (
            overwritten_count > self._last_analyzed_overwrite_count
        )
        self._last_analyzed_overwrite_count = overwritten_count
        for module_id, pc in self.ring.drain():
            assert module_id == self.module_id
            new_state = self.bitmap.touch(pc)
            if new_state == CardState.EXECUTED:
                self.update_bitmap.mark(self.bitmap.card_of(pc))
            if new_state == CardState.HOT and not self.compile_queue.contains(pc):
                self.compile_queue.push_back(pc)
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()
        if yield_requested:
            self.exec_counter = 0

    def on_yield(self) -> None:
        """Finish pending history and reset the interpreter execution counter."""

        self.on_interpreter_exit(True)

    def has_pending_compilation(self) -> bool:
        """Return whether the bounded compile queue has work for an idle slice."""

        return bool(self.compile_queue)

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
                for bit in range(CardUpdateBitmap.UNIT_CARDS):
                    if (bits >> bit) & 1:
                        card_index = update.cursor * CardUpdateBitmap.UNIT_CARDS + bit
                        if card_index < update.card_count:
                            decayed += self.bitmap.decay_executed_card(card_index)
                            update.unmark(card_index)
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
                self._trackable_generation += 1
        return compiled_count

    def drain_compile_queue(self) -> int:
        """Compile all currently queued traces."""

        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def lookup(self, pc: int) -> JITTrace | None:
        """Apply the candidate/card filter and look up one resident trace."""

        if not self.hotspot_profiling_enabled:
            return self.cache.lookup(pc)
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
        self._trackable_generation += 1

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
        """Bound stack writes across resident forward trace chains."""
        resident_limit = (
            JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_CAPACITY_BYTES // JIT_X64_TRACE_HEADER_BYTES
        )
        pending: StaticVector[JITTrace] = StaticVector(capacity=resident_limit)
        visited: StaticVector[int] = StaticVector(capacity=resident_limit)
        pending.append(trace)
        words = trace.stack_words
        while pending:
            current = pending.pop_back()
            if visited.contains(current.head_pc):
                continue
            visited.append(current.head_pc)
            words = max(words, current.stack_words)
            if current.chain_next is not None:
                successor = self.cache.find_trace(current.chain_next)
                assert successor is not None
                if not visited.contains(successor.head_pc) and not any(
                    item.head_pc == successor.head_pc for item in pending
                ):
                    pending.append(successor)
            assert len(visited) <= resident_limit
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
