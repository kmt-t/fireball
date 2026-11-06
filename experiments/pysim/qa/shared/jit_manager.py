"""Plugin boundary adapter for the Tier 3 C++ JitRuntime."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Sequence
from typing import Protocol

from config import (
    FB_CONF_JIT_CANDIDATE_THRESHOLD,
    JIT_CACHE_BANK_ENTRY_CAPACITY,
    JIT_CARD_SHIFT,
    JIT_COMPILE_QUEUE_CAPACITY,
    JIT_HISTORY_CAPACITY,
)
from qa.private import jit_native_abi as native_abi
from qa.private.jit_native_abi import CacheField
from system_containers import StaticVector, fold_mix32
from system_containers import StaticVector as _StaticVector
from tier2_runtime.abi.native_abi import RUN_DISPATCH_JIT as NATIVE_RUN_DISPATCH_JIT
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.interpreter import (
    ExecutionContext,
    InterpreterBindings,
    NativeInterpreter,
    NativeModuleExecution,
)
from tier2_runtime.runtime.python_api import PAGE_SIZE
from tier2_runtime.wasm.module import BasicBlock, Memory, Module
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
from .x64_jit import TraceCompiler


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


class _NativeBitStorage:
    """Card state owned by the native runtime, viewed without a copy."""

    __slots__ = ("bits", "buffer", "count")

    def __init__(self, address: int, nbytes: int, count: int, bits: int) -> None:
        self.buffer = memoryview((ctypes.c_uint8 * nbytes).from_address(address))
        self.count = count
        self.bits = bits


def _module_execution(module: Module) -> tuple[NativeInterpreter, NativeModuleExecution]:
    """Build the product module execution that an interpreter builds for `module`.

    The interpreter owns the allocator that holds the view's tables, so the caller keeps it alive.
    """
    memory_decl = (
        module.memory if module.memory is not None else Memory(min_pages=0, max_pages=None)
    )
    bindings = InterpreterBindings(
        memory=bytearray(memory_decl.min_pages * PAGE_SIZE),
        memory_decl=memory_decl,
        imported_memory=False,
        host_functions=_StaticVector.of(
            tuple(None for _ in module.imports), capacity=len(module.imports)
        ),
        globals=_StaticVector(capacity=0),
        tables=_StaticVector(capacity=0),
    )
    interpreter = NativeInterpreter.with_module_allocator(module, bindings)
    interpreter._prepare_native_execution(
        ExecutionContext(module, allocator=interpreter.bump_allocator)
    )
    execution = interpreter._module_execution
    assert execution is not None
    return interpreter, execution


class JITRuntimeManager(NativeJITRuntimeManager):
    """QA-only views of cache and card state; the product adapter is opaque."""

    __slots__ = (
        "_body_counters",
        "_card_shift",
        "_cards",
        "_code_bytes",
        "_compile_code_leases",
        "_module_owner",
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
        "trackable",
        "update_bitmap",
    )

    def __init__(
        self,
        code_lengths: Sequence[int] = (),
        module_id: int = 0,
        retire_observer: Callable[[int, int], None] | None = None,
    ):
        assert 0 <= module_id <= 0xFFFF_FFFF
        # Every runtime parameter is the product configuration; tests do not override it.
        self.candidate_threshold = FB_CONF_JIT_CANDIDATE_THRESHOLD
        self.compile_queue_capacity = JIT_COMPILE_QUEUE_CAPACITY
        self.aging_step_units = int(native_abi.AGING_STEP_UNITS())
        self.aging_scan_bytes = int(native_abi.AGING_STEP_SCAN_BYTES())
        # The QA runtime owns a compiler, as the product runtime does.
        self.jit_compiler: JITCompiler = TraceCompiler()
        self.history_capacity = JIT_HISTORY_CAPACITY
        self.module_id = module_id
        self._card_shift = JIT_CARD_SHIFT
        self._retire_observer = retire_observer
        # The product runtime is created when a module is registered; it has no earlier state.
        self.module: Module | None = None
        self._native: native_abi.NativeRuntimeStorage | None = None
        self._body_counters = False
        self._pointer: int | None = None
        self._module_owner: NativeInterpreter | None = None
        self._execution: NativeModuleExecution | None = None
        self._compile_code_leases: StaticVector[BufferLease] = StaticVector(
            capacity=len(code_lengths)
        )
        self._native_dispatch_cache_snapshot = EMPTY_NATIVE_DISPATCH_SNAPSHOT
        self._native_dispatch_cache_key = None

    def _bind_test_views(self) -> None:
        """Expose the card arrays that the native runtime owns as QA views, without copies."""
        assert self.module is not None and self._native is not None
        views = self._native.card_views()
        self._cards = (
            _NativeBitStorage(views.states, views.state_bytes, views.cards, 2),
            _NativeBitStorage(views.dirty, views.dirty_bytes, views.cards, 1),
            _NativeBitStorage(views.trackable, views.dirty_bytes, views.cards, 1),
        )
        lengths = _module_code_lengths(self.module)
        bases = _module_function_pc_bases(self.module)
        self.bitmap = HotspotBitmap(self.card_shift, lengths, bases)
        self.trackable = BlockCardMask(self.card_shift, lengths, bases)
        self.update_bitmap = CardUpdateBitmap(self.bitmap.card_count)
        assert self.bitmap.card_count == views.cards
        self.bitmap.storage = self._cards[0]
        self.update_bitmap.storage = self._cards[1]
        self.trackable.storage = self._cards[2]
        from .jit_cache import _NativeBits

        self.bitmap._bits = _NativeBits(self.bitmap.storage, self.bitmap.card_count, 2)
        self.update_bitmap._bits = _NativeBits(
            self.update_bitmap.storage, self.bitmap.card_count, 1
        )
        self.trackable._bits = _NativeBits(self.trackable.storage, self.trackable.card_count, 1)
        self.update_bitmap._cursor = ctypes.c_uint32.from_address(views.cursor)

    def register_module(self, module: Module, module_id: int | None = None) -> None:
        """Create the product runtime for `module` and bind its loader metadata."""

        if self.module is module:
            if module_id is not None:
                assert module_id == self.module_id
            return
        assert self.module is None, "a QA runtime binds one module"
        if module_id is not None:
            assert 0 <= module_id <= 0xFFFF_FFFF
            self.module_id = module_id
        # The JIT reads the original bytecode, so native views must not use the expression copy.
        module.drop_native_code_copies()
        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        self._compile_code_leases = StaticVector(capacity=len(module.functions))
        for local_index in range(len(module.functions)):
            code = module.code_for(len(module.imports) + local_index)
            self._compile_code_leases.append(BufferLease(memoryview(code)))
            assert module.functions[local_index].local_layout_cache is not None
        module.build_native_global_widths()
        self._module_owner, self._execution = _module_execution(module)
        self._native = native_abi.NativeRuntimeStorage(
            ctypes.addressof(self._execution._module_view), self.module_id
        )
        self._pointer = self._native.pointer
        native_abi.RUNTIME_ENABLE_BODY_COUNTERS(self._pointer, 1 if self._body_counters else 0)
        self.cache = JitRuntimeBoundary.borrow(self._native, self._retire_observer)
        self.cache.metadata_provider = self._describe_trace
        self._native_dispatch_cache_key = None
        self._bind_test_views()

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
    def history_visit_count(self) -> int:
        """Visits recorded by the history analysis (not trace body executions)."""
        return self.cache.scalar(CacheField.HISTORY_VISITS)

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
        assert function.local_layout_cache is not None
        annotations = function.interpreter_annotation_pool
        assert self.module.block_storage is not None
        block_index = self.module.block_storage.view().find(fold_mix32(pc))
        assert block_index is not None
        assert self.module.blocks[block_index].head_pc == pc
        code = self.module.code_for(function_index)
        block_offset = pc - self.module.function_pc_offset(function_index)
        local_index = function_index - len(self.module.imports)
        layout = function.local_layout_cache
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
                offsets=native_abi.NativeByteView(layout.offsets_address, layout.count * 2),
                types=native_abi.NativeByteView(layout.types_address, layout.count),
                local_count=layout.count,
                total_words=layout.total_words,
            ),
            block_index=block_index,
            next_block_index=block.next_block_index,
            loops_to_block_index=block.loops_to_block_index,
            interpreter_annotations=native_abi.NativeByteView(
                0 if annotations is None else annotations.native_address,
                0 if annotations is None else len(annotations),
            ),
            interpreter_annotation_byte_offset=block.interpreter_annotation_byte_offset,
            entry_stack_words=block.entry_stack_words,
            global_widths=native_abi.NativeByteView(
                self.module.global_widths_address, len(self.module.globals)
            ),
        )
        return block_input

    def native_dispatch_state(self) -> NativeDispatchSnapshot:
        """Return module-wide ctypes tables consumed by native nested calls."""

        cache_key = (self.cache.generation, self._trackable_generation)
        if cache_key == self._native_dispatch_cache_key:
            return self._native_dispatch_cache_snapshot

        module = self.module
        assert module is not None
        trace_capacity = min(len(module.blocks), self.cache.entry_capacity)
        entries = (NativeTraceDispatchEntry * trace_capacity)()
        entry_count = self.cache.build_snapshot(entries)
        mask_buffer = self.trackable.storage.buffer
        trackable_mask = (ctypes.c_uint8 * len(mask_buffer)).from_buffer(mask_buffer)
        trackable_card_count = self.trackable.card_count
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
            common_trace_entry=self.cache.common_trace_entry,
        )
        self._native_dispatch_cache_key = cache_key
        return self._native_dispatch_cache_snapshot

    def record_native_block_visits(
        self, visits: NativeBlockVisitHistory, total_visits: int
    ) -> bool:
        """Append the interpreter's ordered block history without analyzing it."""

        assert 0 <= total_visits <= 0xFFFF_FFFF_FFFF_FFFF
        pointer = ctypes.cast(visits.native_address, ctypes.POINTER(ctypes.c_uint32))
        result = native_abi.RUNTIME_VISITS(
            self.cache._native.pointer, pointer, len(visits), total_visits
        )
        assert result == 1
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
        result = native_abi.RUNTIME_RECORD(self.cache._native.pointer, pc)
        assert result == 1
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
        """Delegate filtering and lookup to the native JitRuntime."""
        assert 0 <= pc <= 0xFFFF_FFFF
        self.cache._begin()
        location = native_abi.CacheLookup()
        found = native_abi.RUNTIME_FILTERED_LOOKUP(
            self.cache._native.pointer, pc, ctypes.byref(location)
        )
        self.cache._check()
        return None if found == 0 else self.cache._reference(pc)

    def unmark_trackable(self, pc: int) -> None:
        """Remove a permanently unsupported block from the candidate mask."""

        assert 0 <= pc <= 0xFFFF_FFFF
        result = native_abi.RUNTIME_SUPPRESS(self.cache._native.pointer, pc)
        assert result == 1

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

    @property
    def card_shift(self) -> int:
        return self._card_shift

    def age_step(self) -> int:
        return self.cache.age_step()

    def bind_execution(self, execution: NativeModuleExecution) -> int:
        assert execution is not None and execution.module is self.module
        assert self._native.pointer is not None
        provider = native_abi.RUNTIME_PROVIDER(self._native.pointer)
        assert provider is not None
        return int(provider)

    native_entry = NATIVE_RUN_DISPATCH_JIT

    def on_yield(self) -> None:
        self._begin()
        result = native_abi.RUNTIME_YIELD(self._native.pointer)
        assert result == 1
        self._check()

    def idle_hook(self) -> int:
        self._begin()
        result = int(native_abi.RUNTIME_COMPILE(self._native.pointer))
        assert result >= 0
        self._check()
        return result

    def enable_body_counters(self, enabled: bool) -> None:
        """Request the per-block trace-body counters (QA counting configuration only).

        The counters are attached when the module is registered, so the request is kept here and
        applied at that point.
        """
        self._body_counters = enabled
        if self._native is not None:
            native_abi.RUNTIME_ENABLE_BODY_COUNTERS(self._native.pointer, 1 if enabled else 0)

    def flush_all(self) -> None:
        self.cache.flush_all()

    def reset_stats(self) -> None:
        native_abi.RUNTIME_RESET_COUNTS(self._native.pointer)

    def close(self) -> None:
        if self._native is not None:
            self._native.close()
        self._pointer = None


__all__ = ("JITCompiler", "JITRuntimeManager")
