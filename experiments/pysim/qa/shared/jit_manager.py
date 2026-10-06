"""Plugin boundary adapter for the Tier 3 C++ JitRuntime."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Sequence
from typing import Protocol

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_ENTRY_CAPACITY,
    JIT_CACHE_OLDEST_OFFSET_BYTES,
    JIT_CACHE_WARM_OFFSET_BYTES,
    JIT_CARD_SHIFT,
)
from qa.private import jit_native_abi as native_abi
from qa.private.jit_native_abi import CacheField
from qa.shared.jit_scoring import JIT_CANDIDATE_THRESHOLD, block_score
from system_containers import MutableBitStorage, StaticVector
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.interpreter import (
    NativeDispatchEntryPoint,
    NativeModuleExecution,
)
from tier2_runtime.wasm.module import BasicBlock, Module
from tier3_plugins.jit.jit_manager import JITRuntimeManager as NativeJITRuntimeManager

from .jit_abi import (
    EMPTY_NATIVE_DISPATCH_SNAPSHOT,
    NativeBlockVisitHistory,
    NativeDispatchSnapshot,
    NativeTraceDispatchEntry,
)
from .jit_cache import (
    BlockCardMask,
    CardUpdateBitmap,
    HotspotBitmap,
    JitRuntimeBoundary,
    JITTrace,
)


class JITCompiler(Protocol):
    """Native compiler configuration and external compilation observation."""


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


class JITRuntimeManager(NativeJITRuntimeManager):
    """QA-only views of cache and card state; the product adapter is opaque."""

    __slots__ = (
        "_block_execution_counts",
        "_block_inputs",
        "_card_shift",
        "_cards",
        "_code_bytes",
        "_compile_code_leases",
        "_compile_width_buffers",
        "_hotspot_profiling_enabled",
        "_native",
        "_native_dispatch_cache_key",
        "_native_dispatch_cache_snapshot",
        "_profile",
        "_retire_observer",
        "aging_scan_bytes",
        "aging_step_units",
        "bitmap",
        "cache",
        "candidate_threshold",
        "compile_queue_capacity",
        "history_capacity",
        "jit_compiler",
        "min_trace_bytes",
        "trackable",
        "update_bitmap",
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
        self._card_shift = card_shift
        self._retire_observer = retire_observer
        self._native = native_abi.NativeRuntimeStorage(
            JIT_CACHE_BANK_CAPACITY_BYTES,
            JIT_CACHE_BANK_ENTRY_CAPACITY,
            (
                JIT_CACHE_ACTIVE_OFFSET_BYTES,
                JIT_CACHE_WARM_OFFSET_BYTES,
                JIT_CACHE_OLDEST_OFFSET_BYTES,
            ),
        )
        self._pointer = self._native.pointer
        self.module: Module | None = None
        self._compile_code_leases: StaticVector[BufferLease] = StaticVector(
            capacity=len(code_lengths)
        )
        self._compile_width_buffers: StaticVector[ctypes.Array] = StaticVector(
            capacity=len(code_lengths)
        )
        self._block_inputs = (native_abi._NativeWasmBlock * 0)()
        self._block_execution_counts = (ctypes.c_uint32 * 0)()
        self._bind_buffers(code_lengths, ())
        self._bind_profile()
        self.cache = JitRuntimeBoundary.borrow(self._native, retire_observer)
        self.cache.metadata_provider = self._describe_trace
        self._native_dispatch_cache_snapshot = EMPTY_NATIVE_DISPATCH_SNAPSHOT
        self._native_dispatch_cache_key = None
        self._bind_test_views(code_lengths, ())

    def _bind_test_views(self, lengths: Sequence[int], bases: Sequence[int]) -> None:
        self.bitmap = HotspotBitmap(self.card_shift, lengths, bases)
        self.trackable = BlockCardMask(self.card_shift, lengths, bases)
        self.update_bitmap = CardUpdateBitmap(self.bitmap.card_count)
        self.bitmap.storage = self._cards[0]
        self.update_bitmap.storage = self._cards[1]
        self.trackable.storage = self._cards[2]
        from .jit_cache import _NativeBits

        self.bitmap._bits = _NativeBits(self.bitmap.storage, self.bitmap.card_count, 2)
        self.update_bitmap._bits = _NativeBits(
            self.update_bitmap.storage, self.bitmap.card_count, 1
        )
        self.trackable._bits = _NativeBits(self.trackable.storage, self.trackable.card_count, 1)
        assert self._native._cursor is not None
        self.update_bitmap._cursor = self._native._cursor

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
        code_lengths = tuple(
            len(module.code_for(index)) if index >= len(module.imports) else 0
            for index in range(len(module.imports) + len(module.functions))
        )
        pc_bases = tuple(
            module.function_pc_offset(index) if index >= len(module.imports) else 0
            for index in range(len(module.imports) + len(module.functions))
        )
        self._bind_buffers(code_lengths, pc_bases)
        self._block_inputs = (native_abi._NativeWasmBlock * len(module.blocks))()
        counters_enabled = bool(native_abi.BLOCK_COUNTERS_ENABLED())
        self._block_execution_counts = (
            (ctypes.c_uint32 * len(module.blocks))()
            if counters_enabled
            else (ctypes.c_uint32 * 0)()
        )
        for index, block in enumerate(module.blocks):
            function = module.functions[block.func_index - len(module.imports)]
            assert function.local_width_map_cache is not None
            local_index = block.func_index - len(module.imports)
            code = module.code_for(block.func_index)
            widths = self._compile_width_buffers[local_index]
            self._block_inputs[index] = native_abi._NativeWasmBlock(
                block.head_pc,
                block.head_pc - module.function_pc_offset(block.func_index),
                block.byte_span,
                0xFFFF_FFFF if block.next_pc is None else block.next_pc,
                0xFFFF_FFFF if block.loops_to is None else block.loops_to,
                block.frame_depth,
                block_score(module, block),
                native_abi._NativeByteView(
                    self._compile_code_leases[local_index].address, len(code)
                ),
                native_abi._NativeLocalLayout(
                    native_abi._NativeByteView(
                        ctypes.addressof(widths) if len(widths) else 0, len(widths)
                    ),
                    function.local_width_map_cache.count,
                    function.local_width_map_cache.slot_words,
                ),
                (
                    ctypes.addressof(self._block_execution_counts)
                    + index * ctypes.sizeof(ctypes.c_uint32)
                    if counters_enabled
                    else None
                ),
            )
        self._bind_profile()
        self._bind_test_views(_module_code_lengths(module), _module_function_pc_bases(module))
        self._native_dispatch_cache_key = None

    def _begin(self) -> None:
        self.cache._begin()

    def _check(self) -> None:
        self.cache._check()

    @property
    def compilation_pcs(self) -> tuple[int, ...]:
        """Actual occupied descriptor slots in allocation order for a fresh QA runtime."""
        slots = 3 * JIT_CACHE_BANK_ENTRY_CAPACITY + 1
        return tuple(
            pc
            for i in range(slots)
            if (pc := int(native_abi.OWNED_PC(self._native.pointer, i))) != 0xFFFF_FFFF_FFFF_FFFF
        )

    @property
    def exec_counter(self) -> int:
        return self.cache.scalar(CacheField.EXECUTION_COUNT)

    @property
    def history_overwritten_count(self) -> int:
        return self.cache.scalar(CacheField.HISTORY_OVERWRITTEN)

    @property
    def last_history_analysis_approximate(self) -> bool:
        return self.cache.scalar(CacheField.HISTORY_APPROXIMATE) != 0

    @property
    def _trackable_generation(self) -> int:
        return self.cache.scalar(CacheField.TRACKABLE_GENERATION)

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
            loops_to=block.loops_to,
            frame_depth=block.frame_depth,
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

    def has_pending_compilation(self) -> bool:
        """Return whether the bounded compile queue has work for an idle slice."""

        return self.cache.scalar(CacheField.QUEUE_COUNT) != 0

    @property
    def aging_steps(self) -> int:
        return self.cache.scalar(CacheField.AGING_STEPS)

    @property
    def aging_units_processed(self) -> int:
        return self.cache.scalar(CacheField.AGING_UNITS)

    @property
    def aging_bytes_scanned(self) -> int:
        return self.cache.scalar(CacheField.AGING_SCANNED)

    def lookup(self, pc: int) -> JITTrace | None:
        """Delegate filtering, lookup and promotion to the native JitRuntime."""
        assert 0 <= pc <= 0xFFFF_FFFF
        self.cache._begin()
        token = int(native_abi.RUNTIME_FILTERED_LOOKUP(self.cache._native.pointer, pc))
        self.cache._check()
        return self.cache._reference(token)

    def unmark_trackable(self, pc: int) -> None:
        """Remove a permanently unsupported block from the candidate mask."""

        assert 0 <= pc <= 0xFFFF_FFFF
        assert native_abi.RUNTIME_SUPPRESS(self.cache._native.pointer, pc) == 1

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
        for index, registered in enumerate(self.module.blocks):
            if registered.head_pc == block.head_pc:
                trace._native.exec_count = ctypes.cast(
                    self._block_inputs[index].extension_data,
                    ctypes.POINTER(ctypes.c_uint32),
                )
                break

    def _bind_buffers(self, code_lengths: Sequence[int], pc_bases: Sequence[int]) -> None:
        lengths = (ctypes.c_uint32 * len(code_lengths))(*code_lengths)
        bases = (ctypes.c_uint32 * len(pc_bases))(*pc_bases) if pc_bases else None
        size = ctypes.c_uint64()
        assert native_abi.REGION_SIZE(lengths, bases, len(lengths), ctypes.byref(size)) == 1
        self._code_bytes = size.value
        cards = int(native_abi.CARD_COUNT(self._code_bytes, self._card_shift))
        assert cards >= 0
        self._cards = (
            MutableBitStorage(count=cards, bits=2),
            MutableBitStorage(count=cards, bits=1),
            MutableBitStorage(count=cards, bits=1),
        )
        self._native.bind_cards(
            self._cards[0].buffer,
            self._cards[1].buffer,
            cards,
            self._card_shift,
            ctypes.c_uint32(),
            self.aging_step_units,
            self.aging_scan_bytes,
        )

    def _bind_profile(self) -> None:
        """Retain caller storage and bind it to the native profiling owner."""
        mask = self._cards[2].buffer
        mask_view = (ctypes.c_uint8 * len(mask)).from_buffer(mask)
        self._profile = native_abi.NativeProfile(
            mask_view,
            len(mask),
            self.module_id,
            int(self.hotspot_profiling_enabled),
            self.candidate_threshold,
            self.min_trace_bytes,
            self._code_bytes,
            self.history_capacity,
            self.compile_queue_capacity,
            int(self.jit_compiler is not None),
            self._block_inputs,
            len(self._block_inputs),
        )
        assert native_abi.RUNTIME_BIND_PROFILE(self._native.pointer, self._profile) == 1

    @property
    def hotspot_profiling_enabled(self) -> bool:
        return self._hotspot_profiling_enabled

    @property
    def card_shift(self) -> int:
        return self._card_shift

    def age_step(self) -> int:
        return self.cache.age_step()

    def bind_execution(
        self, dispatcher: NativeDispatchEntryPoint, execution: NativeModuleExecution
    ) -> int:
        assert execution is not None and execution.module is self.module
        assert self._native.pointer is not None
        native_abi.RUNTIME_BIND_DISPATCH(
            self._native.pointer, ctypes.cast(dispatcher, ctypes.c_void_p)
        )
        extension = native_abi.RUNTIME_EXTENSION(self._native.pointer)
        assert extension is not None
        return int(extension)

    def native_entry(self, call, result) -> int:
        self._begin()
        status = native_abi.RUNTIME_RUN(call, result)
        self._check()
        return status

    def on_yield(self) -> None:
        self._begin()
        assert native_abi.RUNTIME_YIELD(self._native.pointer) == 1
        self._check()

    def idle_hook(self, budget: int = 4) -> int:
        assert 0 <= budget <= 0x7FFF_FFFF
        self._begin()
        result = int(native_abi.RUNTIME_COMPILE(self._native.pointer, budget))
        assert result >= 0
        self._check()
        return result

    def flush_all(self) -> None:
        self.cache.flush_all()

    def reset_stats(self) -> None:
        native_abi.RUNTIME_RESET_COUNTS(self._native.pointer)

    def close(self) -> None:
        self._native.close()


__all__ = ("JITCompiler", "JITRuntimeManager")
