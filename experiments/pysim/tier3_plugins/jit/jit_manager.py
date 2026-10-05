"""Plugin boundary adapter for the Tier 3 C++ JitRuntime."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Sequence
from typing import Protocol

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    JIT_CARD_SHIFT,
)
from system_containers import StaticVector
from tier2_runtime.abi.jit_abi import (
    EMPTY_NATIVE_DISPATCH_SNAPSHOT,
    NativeBlockVisitHistory,
    NativeDispatchSnapshot,
    NativeTraceDispatchEntry,
)
from tier2_runtime.abi.native_abi import BufferLease
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

from . import native_abi
from .jit_cache import (
    BlockCardMask,
    CardUpdateBitmap,
    HotspotBitmap,
    JitRuntimeBoundary,
    JITTrace,
)
from .native_abi import CacheField


class JITCompiler(Protocol):
    """Native compiler configuration and external compilation observation."""

    compile_observer: Callable[[int, bool, int], None] | None


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


class JITRuntimeManager:
    """Connect the Tier 3 native JitRuntime to pysim execution boundaries.

    The C++ JitRuntime owns card transitions, aging, trace residency, cache
    rotation, lookup and chaining. This host adapter transfers interpreter
    history and supplies loader/compiler metadata. The native owner analyzes
    history and schedules the compile queue.
    """

    __slots__ = (
        "_block_inputs",
        "_compile_code_leases",
        "_compile_width_buffers",
        "_hotspot_profiling_enabled",
        "_native_dispatch_cache_key",
        "_native_dispatch_cache_snapshot",
        "_profile",
        "aging_scan_bytes",
        "aging_step_units",
        "bitmap",
        "cache",
        "candidate_threshold",
        "compile_queue_capacity",
        "history_capacity",
        "jit_compiler",
        "min_trace_bytes",
        "module",
        "module_id",
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
        retire_observer: Callable[[int, int], None] | None = None,
    ):
        assert 1 <= yield_threshold <= 0xFFFFFFFF
        assert 0 <= candidate_threshold <= 0xFFFF_FFFF
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
        assert 0 <= self.min_trace_bytes <= 0xFFFF_FFFF
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.update_bitmap = CardUpdateBitmap(card_count=self.bitmap.card_count)
        self.cache = JitRuntimeBoundary(
            compile_observer=None if jit_compiler is None else jit_compiler.compile_observer,
            retire_observer=retire_observer,
        )
        self.cache.metadata_provider = self._describe_trace
        self.cache.bind_cards(self.bitmap, self.update_bitmap, aging_step_units, aging_scan_bytes)
        self.module: Module | None = None
        self._native_dispatch_cache_snapshot = EMPTY_NATIVE_DISPATCH_SNAPSHOT
        self._compile_code_leases: StaticVector[BufferLease] = StaticVector(
            capacity=len(code_lengths)
        )
        self._compile_width_buffers: StaticVector[ctypes.Array] = StaticVector(
            capacity=len(code_lengths)
        )
        self._native_dispatch_cache_key: tuple[int, int, bool] | None = None
        self._block_inputs = (native_abi._NativeWasmBlock * 0)()
        self._bind_profile()

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
        self.cache.bind_cards(
            self.bitmap, self.update_bitmap, self.aging_step_units, self.aging_scan_bytes
        )
        self._block_inputs = (native_abi._NativeWasmBlock * len(module.blocks))()
        for index, block in enumerate(module.blocks):
            request = self._block_input(block.head_pc, block)
            self._block_inputs[index] = native_abi.marshal_block(
                request, block.loops_to, block.frame_depth, block.jit_score
            )
        self._bind_profile()
        self._native_dispatch_cache_key = None

    def _bind_profile(self) -> None:
        """Retain caller storage and bind it to the native profiling owner."""
        mask = self.trackable.storage.buffer
        mask_view = (ctypes.c_uint8 * len(mask)).from_buffer(mask)
        self._profile = native_abi.NativeProfile(
            mask_view,
            len(mask),
            self.module_id,
            int(self.hotspot_profiling_enabled),
            self.candidate_threshold,
            self.min_trace_bytes,
            self.bitmap.region.byte_length,
            self.history_capacity,
            self.compile_queue_capacity,
            int(self.jit_compiler is not None),
            self._block_inputs,
            len(self._block_inputs),
        )
        assert native_abi.RUNTIME_BIND_PROFILE(self.cache._native.pointer, self._profile) == 1

    @property
    def exec_counter(self) -> int:
        return self.cache._native.scalar(CacheField.EXECUTION_COUNT)

    @property
    def history_overwritten_count(self) -> int:
        return self.cache._native.scalar(CacheField.HISTORY_OVERWRITTEN)

    @property
    def last_history_analysis_approximate(self) -> bool:
        return self.cache._native.scalar(CacheField.HISTORY_APPROXIMATE) != 0

    @property
    def _trackable_generation(self) -> int:
        return self.cache._native.scalar(CacheField.TRACKABLE_GENERATION)

    def get_block(self, pc: int) -> BasicBlock | None:
        """Resolve loader-owned metadata at the plugin boundary."""
        return self.module.get_block(pc) if self.module is not None else None

    def _block_input(self, pc: int, block: BasicBlock) -> native_abi.WasmBlock:
        """Marshal loader-owned metadata at module registration."""

        assert self.module is not None
        function_index = block.func_index
        function = self.module.functions[function_index - len(self.module.imports)]
        assert function.local_width_map_cache is not None
        code = self.module.code_for(function_index)
        block_offset = pc - self.module.function_pc_offset(function_index)
        local_index = function_index - len(self.module.imports)
        widths_view = function.local_width_map_cache.raw_view
        width_buffer = self._compile_width_buffers[local_index]
        block_input = native_abi.WasmBlock(
            head_pc=pc,
            code=native_abi.NativeByteView(
                self._compile_code_leases[local_index].address, len(code)
            ),
            offset=block_offset,
            byte_length=block.byte_span,
            next_pc=block.next_pc,
            locals=native_abi.NativeLocalLayout(
                widths=native_abi.NativeByteView(
                    ctypes.addressof(width_buffer) if len(widths_view) > 0 else 0, len(widths_view)
                ),
                local_count=function.local_width_map_cache.count,
                slot_words=function.local_width_map_cache.slot_words,
            ),
        )
        return block_input

    def native_dispatch_state(self) -> NativeDispatchSnapshot:
        """Return module-wide ctypes tables consumed by native nested calls."""

        cache_key = (
            self.cache.generation,
            self._trackable_generation,
            self.hotspot_profiling_enabled,
        )
        if cache_key == self._native_dispatch_cache_key:
            return self._native_dispatch_cache_snapshot

        module = self.module
        assert module is not None
        trace_capacity = min(len(module.blocks), self.cache.entry_capacity)
        entries = (NativeTraceDispatchEntry * trace_capacity)()
        entry_count = self.cache.build_snapshot(entries)
        mask_buffer = self.trackable.storage.buffer
        trackable_mask = (
            (ctypes.c_uint8 * len(mask_buffer)).from_buffer(mask_buffer)
            if self.hotspot_profiling_enabled
            else (ctypes.c_uint8 * 0)()
        )
        trackable_card_count = self.trackable.card_count if self.hotspot_profiling_enabled else 0
        block_history = (ctypes.c_uint32 * self.history_capacity)()
        self._native_dispatch_cache_snapshot.release_workspace()
        self._native_dispatch_cache_snapshot = NativeDispatchSnapshot(
            entries,
            entry_count,
            trackable_mask,
            trackable_card_count,
            block_history,
            allocator=module.allocator,
            trackable_shift=self.trackable.card_shift,
        )
        self._native_dispatch_cache_key = cache_key
        return self._native_dispatch_cache_snapshot

    def record_native_block_visits(
        self, visits: NativeBlockVisitHistory, total_visits: int
    ) -> bool:
        """Append the interpreter's ordered block history without analyzing it."""

        assert 0 <= total_visits <= 0xFFFF_FFFF_FFFF_FFFF
        pointer = ctypes.cast(visits.native_address, ctypes.POINTER(ctypes.c_uint32))
        assert (
            native_abi.RUNTIME_VISITS(
                self.cache._native.pointer, pointer, len(visits), total_visits
            )
            == 1
        )
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
        assert 0 <= idle_budget <= 0x7FFF_FFFF
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
                self.cache.promote(oldest_pc)
            if self.hotspot_profiling_enabled and eligible_block_visits > 0:
                self.record_native_block_visits(block_visits, eligible_block_visits)
            if native_status != NATIVE_DISPATCH_OLDEST_TRACE:
                break
        if native_status == 0:
            call_state = interp.resolve_native_call_boundary(call_state)
        yield_requested = native_status == NATIVE_DISPATCH_YIELD
        self.cache._begin()
        result = native_abi.RUNTIME_FINISH(
            self.cache._native.pointer, int(yield_requested), idle_budget
        )
        self.cache._check()
        assert result == 1

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

        assert 0 <= pc <= 0xFFFF_FFFF
        assert native_abi.RUNTIME_RECORD(self.cache._native.pointer, pc) == 1
        return False

    def on_interpreter_exit(self, yield_requested: bool) -> None:
        """Transfer the completed execution boundary to the native history analyzer."""
        self.cache._begin()
        result = native_abi.RUNTIME_ANALYZE(self.cache._native.pointer, int(yield_requested))
        self.cache._check()
        assert result == 1

    def on_yield(self) -> None:
        """Finish pending history and reset the interpreter execution counter."""

        self.on_interpreter_exit(True)

    def has_pending_compilation(self) -> bool:
        """Return whether the bounded compile queue has work for an idle slice."""

        return self.cache._native.scalar(CacheField.QUEUE_COUNT) != 0

    def age_step(self) -> int:
        """Perform one bounded card-aging sweep after a cache rotation."""

        return self.cache.age_step()

    @property
    def aging_steps(self) -> int:
        return self.cache._native.scalar(CacheField.AGING_STEPS)

    @property
    def aging_units_processed(self) -> int:
        return self.cache._native.scalar(CacheField.AGING_UNITS)

    @property
    def aging_bytes_scanned(self) -> int:
        return self.cache._native.scalar(CacheField.AGING_SCANNED)

    def idle_hook(self, budget: int = 4) -> int:
        """Compile queued traces in reverse execution order during an idle slice."""

        assert 0 <= budget <= 0x7FFF_FFFF
        self.cache._begin()
        result = int(native_abi.RUNTIME_COMPILE(self.cache._native.pointer, budget))
        self.cache._check()
        assert result >= 0
        return result

    def lookup(self, pc: int) -> JITTrace | None:
        """Delegate filtering, lookup and promotion to the native JitRuntime."""
        assert 0 <= pc <= 0xFFFF_FFFF
        self.cache._begin()
        token = int(native_abi.RUNTIME_FILTERED_LOOKUP(self.cache._native.pointer, pc))
        self.cache._check()
        return self.cache._reference(token)

    def find_trace(self, pc: int) -> JITTrace | None:
        """Find a resident trace without applying the hotspot filter."""

        return self.cache.find_trace(pc)

    def insert_trace(self, trace: JITTrace) -> bool:
        """Install a test or compiler-produced trace into the Tier 3 cache."""

        return self.cache.insert(trace)

    def unmark_trackable(self, pc: int) -> None:
        """Remove a permanently unsupported block from the candidate mask."""

        assert 0 <= pc <= 0xFFFF_FFFF
        assert native_abi.RUNTIME_SUPPRESS(self.cache._native.pointer, pc) == 1

    def flush_all(self) -> None:
        """Invalidate all resident traces without running card aging."""

        self.cache.flush_all()

    def reset_stats(self) -> None:
        """Reset per-trace execution counters owned by the JIT component."""

        self.cache.reset_execution_counts()

    def _describe_trace(self, trace: JITTrace) -> None:
        """Attach loader metadata to the descriptor before native snapshot publication."""

        if self.module is None:
            return
        block = self.get_block(trace.head_pc)
        if block is None:
            assert trace.code_blob is None
            return
        trace._native.byte_span = block.byte_span
        trace._native.frame_depth = block.frame_depth
        trace._native.dispatch_next_pc = 0xFFFF_FFFF if block.next_pc is None else block.next_pc
        trace._native.dispatch_loops_to = 0xFFFF_FFFF if block.loops_to is None else block.loops_to


__all__ = ("JITCompiler", "JITRuntimeManager")
