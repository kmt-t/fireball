"""Tier 3 JIT profiling state and rotating code-cache components.

The Tier 2 runtime owns execution orchestration; this module owns the JIT
state machine, trace descriptors, and cache residency policy.
"""

from __future__ import annotations

import bisect
import ctypes
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from bump_allocator import BumpAllocator
from config import (
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CACHE_FAST_SLOT_COUNT,
    JIT_CACHE_MAX_INBOUND_SOURCES,
    JIT_CACHE_OLDEST_OFFSET_BYTES,
    JIT_CACHE_WARM_OFFSET_BYTES,
    JIT_CARD_SHIFT,
    JIT_TRACE_DEFAULT_BYTES,
    JIT_X64_TRACE_HEADER_BYTES,
)
from system_containers import (
    MutableBitStorage,
    RingBuffer,
    StaticVector,
)
from tier2_runtime.wasm.module import IntegerSequence

from .common_code import (
    COMMON_HELPER_OFFSET,
    TRACE_ENTRY_STUB_BYTES,
    JITCodeCacheRegion,
)
from .native_abi import FAST_CACHE_SLOT_COUNT, NativeFastCacheStorage

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import ExecutionContext


NativeTraceFn = Callable[[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, int], int | None]
TraceArgument = ctypes.c_void_p
assert FAST_CACHE_SLOT_COUNT == JIT_CACHE_FAST_SLOT_COUNT


class CardState:
    __slots__ = ()

    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2  # Queued for compilation
    COMPILED = 3


_CARD_STATE_NAMES = ("UNEXECUTED", "EXECUTED", "HOT", "COMPILED")

# Max distinct chain-in predecessor PCs tracked per JITCacheBank
# (JITCacheBank.inbound_sources), for a StaticVector fixed-capacity bound
# ({GLOBAL_Policy_Memory}). Not yet spec'd: structured WASM control flow
# gives a merge/loop head only a handful of real predecessors (a loop
# back-edge plus its fallthrough entry, or a few br_table cases), so this
# is sized generously against that, matching this file's other small
# FB_CONF-style bounds (JITMultiBufferCache.NUM_FAST_SLOTS=16,
# JITRuntimeManager.compile_queue_capacity=4).
FB_CONF_MAX_INBOUND_SOURCES = JIT_CACHE_MAX_INBOUND_SOURCES


class NativeFastCache:
    """C++ direct-mapped lookup over Python-owned trace handles."""

    __slots__ = ("_next_handle", "_references", "_storage")

    def __init__(self, allocator: BumpAllocator | None = None) -> None:
        assert FAST_CACHE_SLOT_COUNT == JIT_CACHE_FAST_SLOT_COUNT
        self._storage = NativeFastCacheStorage(allocator)
        self._references: StaticVector[tuple[int, JITTrace] | None] = StaticVector(
            capacity=FAST_CACHE_SLOT_COUNT
        )
        for _ in range(FAST_CACHE_SLOT_COUNT):
            self._references.append(None)
        self._next_handle = 1

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Bind the native C++ fast-cache arrays to the runtime arena."""

        self._storage.bind_allocator(allocator)

    def lookup(self, head_pc: int) -> JITTrace | None:
        handle = self._storage.lookup(head_pc)
        if handle == 0:
            return None
        slot = self._storage.slot(head_pc)
        entry = self._references[slot]
        assert entry is not None and entry[0] == handle
        assert entry[1].head_pc == head_pc
        return entry[1]

    def store(self, head_pc: int, trace: JITTrace) -> None:
        assert trace.head_pc == head_pc
        slot = self._storage.slot(head_pc)
        entry = self._references[slot]
        if entry is not None and entry[1] is trace:
            self._storage.store(head_pc, entry[0])
            return
        assert self._next_handle <= 0xFFFF_FFFF_FFFF_FFFF
        handle = self._next_handle
        self._next_handle += 1
        self._storage.store(head_pc, handle)
        self._references[slot] = (handle, trace)

    def clear(self) -> None:
        self._storage.clear()
        for slot in range(len(self._references)):
            self._references[slot] = None


class _CodeRegionLayout:
    """Translate module Code-section PCs to one dense region-wide card space."""

    __slots__ = ("byte_length",)

    def __init__(
        self,
        code_lengths: IntegerSequence,
        function_pc_bases: IntegerSequence,
    ) -> None:
        assert all(code_len >= 0 for code_len in code_lengths)
        if not function_pc_bases:
            bases: StaticVector[int] = StaticVector(capacity=len(code_lengths))
            cursor = 0
            for code_len in code_lengths:
                bases.append(cursor)
                cursor += code_len + 1
            function_pc_bases = bases.freeze()
        assert len(function_pc_bases) == len(code_lengths)
        end_pc = 0
        previous_end = 0
        for index in range(len(code_lengths)):
            base = function_pc_bases[index]
            code_len = code_lengths[index]
            assert 0 <= base <= 0xFFFF_FFFF
            assert base + code_len <= 0x1_0000_0000
            assert previous_end <= base
            if code_len > 0:
                end_pc = base + code_len
            previous_end = base + code_len
        self.byte_length = end_pc

        # UnifiedPC is already relative to the Code-section payload start.
        # Keep the bitmap aligned to that origin, including metadata gaps.

    def card_index(self, pc: int, card_shift: int) -> int:
        assert 0 <= pc < self.byte_length
        return pc >> card_shift


class HotspotBitmap:
    """One 2-bit card-state bitmap spanning a module's complete code region."""

    __slots__ = ("card_shift", "region", "storage")

    def __init__(
        self,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: IntegerSequence = (),
        function_pc_bases: IntegerSequence = (),
    ) -> None:
        self.card_shift = card_shift
        self.region = _CodeRegionLayout(code_lengths, function_pc_bases)
        self.storage = MutableBitStorage(count=self.card_count, bits=2)

    @property
    def card_count(self) -> int:
        return (self.region.byte_length + (1 << self.card_shift) - 1) >> self.card_shift

    def card_of(self, pc: int) -> int:
        return self.region.card_index(pc, self.card_shift)

    def get_card_state(self, card_index: int) -> int:
        assert 0 <= card_index < self.card_count
        return self.storage.view().at(card_index)

    def get_state(self, pc: int) -> int:
        return self.get_card_state(self.card_of(pc))

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""

        card = self.card_of(pc)
        state = self.storage.view().at(card)
        if state == CardState.COMPILED:
            return state
        if state == CardState.UNEXECUTED:
            state = CardState.EXECUTED
        elif state == CardState.EXECUTED:
            state = CardState.HOT
        self.storage.put(card, state)
        return state

    def mark_compiled(self, pc: int) -> None:
        self.storage.put(self.card_of(pc), CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        """
        Evicted trace resets its code-region card to UNEXECUTED. Other PCs in
        the same card share that reset and must earn hotness again as well.
        """

        self.storage.put(self.card_of(pc), CardState.UNEXECUTED)

    def decay_executed_card(self, card_index: int) -> int:
        """Reset one dirty code-region card if it is EXECUTED; preserve HOT/COMPILED."""

        state = self.get_card_state(card_index)
        if state == CardState.EXECUTED:
            self.storage.put(card_index, CardState.UNEXECUTED)
            return 1
        return 0


class CardUpdateBitmap:
    """One dirty bit per code-region card, swept eight cards per storage byte."""

    __slots__ = ("card_count", "cursor", "storage")

    UNIT_CARDS: ClassVar[int] = 8

    def __init__(self, card_count: int = 0) -> None:
        assert card_count >= 0
        self.card_count = card_count
        self.storage = MutableBitStorage(count=card_count, bits=1)
        self.cursor = 0

    @property
    def unit_count(self) -> int:
        return (self.card_count + self.UNIT_CARDS - 1) // self.UNIT_CARDS

    def mark(self, card_index: int) -> None:
        assert 0 <= card_index < self.card_count
        self.storage.put(card_index, 1)

    def unmark(self, card_index: int) -> None:
        assert 0 <= card_index < self.card_count
        self.storage.put(card_index, 0)

    def is_marked(self, card_index: int) -> bool:
        assert 0 <= card_index < self.card_count
        return self.storage.view().at(card_index) != 0

    def unit(self, byte: int) -> int:
        """Return dirty bits for cards ``byte * 8`` through ``byte * 8 + 7``."""

        assert 0 <= byte < self.unit_count
        return self.storage.buffer[byte]


class BlockCardMask:
    """One candidate/suppression bit per card across the complete code region."""

    __slots__ = ("card_shift", "region", "storage")

    def __init__(
        self,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: IntegerSequence = (),
        function_pc_bases: IntegerSequence = (),
    ) -> None:
        self.card_shift = card_shift
        self.region = _CodeRegionLayout(code_lengths, function_pc_bases)
        self.storage = MutableBitStorage(count=self.card_count, bits=1)

    @property
    def card_count(self) -> int:
        return (self.region.byte_length + (1 << self.card_shift) - 1) >> self.card_shift

    def mark(self, pc: int) -> None:
        self.storage.put(self.region.card_index(pc, self.card_shift), 1)

    def is_marked(self, pc: int) -> bool:
        return self.storage.view().at(self.region.card_index(pc, self.card_shift)) != 0

    def unmark(self, pc: int) -> None:
        self.storage.put(self.region.card_index(pc, self.card_shift), 0)

    def clear(self) -> None:
        self.storage.clear()


class HistoryRing:
    """Fixed-size ring of recently executed module/PC pairs backed by RingBuffer."""

    __slots__ = ("ring",)

    def __init__(self, capacity: int = 32):
        assert capacity > 0
        self.ring: RingBuffer[tuple[int, int]] = RingBuffer(capacity)

    @property
    def capacity(self) -> int:
        return self.ring.capacity

    @property
    def dropped(self) -> int:
        return self.ring.dropped

    def record(self, module_id: int, pc: int | None = None) -> None:
        if pc is None:
            pc = module_id
            module_id = 0
        assert 0 <= module_id <= 0xFFFF_FFFF
        assert 0 <= pc <= 0xFFFF_FFFF
        self.ring.push((module_id, pc))

    def record_dropped(self, count: int) -> None:
        """Account for entries overwritten before the native ring was transferred."""
        assert count >= 0
        self.ring.dropped += count

    def drain(self) -> StaticVector[tuple[int, int]]:
        return self.ring.drain()


class JITTraceHeader:
    """
    x64-specific 24-byte fixed physical memory layout:
        +0x00 head_wasm_pc(u32)
        +0x04 trace_byte_size(u16)
        +0x06 flags(u8) [0x01: PROMOTED, 0x02: LOOP_HEADER]
        +0x07 variant_id(u8)
        +0x08 chain_target_addr(u64)
        +0x10 helper_target_addr(u64)

    The common helper entry is compiler metadata used by installation-time
    relocation. Prologue, epilogue, and chain dispatcher offsets are fixed by
    the build configuration and are not repeated in each trace header.
    """

    __slots__ = (
        "chain_target_addr",
        "common_helper_offset",
        "flags",
        "head_wasm_pc",
        "helper_target_addr",
        "trace_byte_size",
        "variant_id",
    )

    FLAG_PROMOTED = 0x01
    FLAG_LOOP_HEADER = 0x02

    def __init__(
        self,
        head_wasm_pc: int,
        trace_byte_size: int = JIT_TRACE_DEFAULT_BYTES,
        flags: int = 0,
        variant_id: int = 0,
        helper_target_addr: int = 0,
    ):
        self.head_wasm_pc = head_wasm_pc & 0xFFFF_FFFF
        assert trace_byte_size >= JIT_X64_TRACE_HEADER_BYTES
        self.trace_byte_size = trace_byte_size & 0xFFFF
        self.flags = flags & 0xFF
        self.variant_id = variant_id & 0xFF
        self.chain_target_addr: int | None = None
        self.common_helper_offset = COMMON_HELPER_OFFSET
        assert 0 <= helper_target_addr <= 0xFFFF_FFFF_FFFF_FFFF
        self.helper_target_addr = helper_target_addr

    def pack(self) -> bytes:
        import struct

        packed = struct.pack(
            "<IHBBQQ",
            self.head_wasm_pc,
            self.trace_byte_size,
            self.flags,
            self.variant_id,
            self.chain_target_addr or 0,
            self.helper_target_addr,
        )
        assert len(packed) == JIT_X64_TRACE_HEADER_BYTES
        return packed


class JITTrace:
    """Compiled native trace descriptor backed by JITTraceHeader and native ctypes function pointer."""

    __slots__ = (
        "_exec_buf",
        "_keepalive",
        "chain_dispatch_patch_offset",
        "chain_next",
        "code_blob",
        "code_offset",
        "entry_body_patch_offset",
        "entry_prologue_patch_offset",
        "exec_count",
        "exit_patch_offset",
        "fn",
        "has_return_val",
        "head_pc",
        "header",
        "helper_exit_patch_offset",
        "helper_header_patch_offset",
        "loops_to",
        "next_pc",
        "raw_addr",
        "result_words",
        "size_bytes",
        "stack_words",
    )

    def __init__(
        self,
        head_pc: int,
        fn: NativeTraceFn | None = None,
        size_bytes: int = JIT_TRACE_DEFAULT_BYTES,
        next_pc: int | None = None,
        loops_to: int | None = None,
        has_return_val: bool = False,
        result_words: int = 1,
        stack_words: int = 1,
        buf: ctypes.Array[ctypes.c_ubyte] | None = None,
        native_fn: NativeTraceFn | None = None,
        raw_addr: int | None = None,
        code_blob: bytes | None = None,
        entry_body_patch_offset: int = -1,
        entry_prologue_patch_offset: int = -1,
        exit_patch_offset: int = -1,
        helper_header_patch_offset: int = -1,
        helper_exit_patch_offset: int = -1,
        chain_dispatch_patch_offset: int = -1,
        helper_target_addr: int = 0,
    ):
        self.head_pc = head_pc
        self.fn = fn or native_fn  # Direct ctypes CFUNCTYPE function pointer or callable
        self.raw_addr = raw_addr  # Entry point address consumed by native dispatch
        self.code_blob = code_blob
        self.code_offset: int | None = None
        self.entry_body_patch_offset = entry_body_patch_offset
        self.entry_prologue_patch_offset = entry_prologue_patch_offset
        self.exit_patch_offset = exit_patch_offset
        self.helper_header_patch_offset = helper_header_patch_offset
        self.helper_exit_patch_offset = helper_exit_patch_offset
        self.chain_dispatch_patch_offset = chain_dispatch_patch_offset
        assert size_bytes >= JIT_X64_TRACE_HEADER_BYTES
        self.size_bytes = size_bytes
        self.next_pc = next_pc  # Unconditional fallthrough successor
        self.loops_to = loops_to  # Conditional loop backedge (never auto-chained)
        self.has_return_val = has_return_val
        assert result_words > 0
        self.result_words = result_words
        # Raw words the trace may write from `sp` upward: spilled cache entries, raw
        # constants for helpers, and the residual value. The C++ dispatcher checks the
        # resident descriptor's chain requirement before entering the trace.
        assert stack_words >= 1
        self.stack_words = stack_words
        self.header = JITTraceHeader(
            head_wasm_pc=head_pc,
            trace_byte_size=size_bytes,
            helper_target_addr=helper_target_addr,
        )
        self.chain_next: int | None = None
        self.exec_count: int = 0
        self._exec_buf = buf  # Keeps executable buffer alive in memory

    @property
    def native_fn(self) -> NativeTraceFn | None:
        return self.fn

    @property
    def flags(self) -> int:
        return self.header.flags

    @flags.setter
    def flags(self, val: int) -> None:
        self.header.flags = val

    def __call__(
        self,
        ctx_or_locals: TraceArgument,
        sp_or_mem: TraceArgument,
        local_base: TraceArgument,
        tos: int,
    ) -> int:
        """
        Invokes the native JIT trace directly via ctypes CPS 4-argument calling convention:
                (void* ctx, void* sp, void* local_base, uint32_t tos)
        """

        assert self.fn is not None
        return self.fn(ctx_or_locals, sp_or_mem, local_base, tos)

    def invoke(self, ctx: ExecutionContext) -> int:
        """Invoke a trace directly on the shared context through its CPS ABI."""
        result_slot = len(ctx.stack)
        # The interpreter owns the shared stack before entry.  A trace that
        # needs an input operand is rejected during compilation; therefore no
        # entry value is popped or copied into a JIT-owned stack.
        self.execute(ctx.context_ptr, ctx.sp_ptr, ctx.locals_ptr, 0)
        if not self.has_return_val:
            return 0
        ctx.stack.set_size(result_slot + self.result_words)
        result = ctx.stack[result_slot]
        return result

    def execute(
        self, ctx: TraceArgument, sp: TraceArgument, local_base: TraceArgument, tos: int
    ) -> None:
        """Execute the compiled PIC entry point with the shared CPS arguments."""

        assert self.fn is not None
        self.fn(ctx, sp, local_base, tos)


class JITCacheBank:
    """
    Sorted head_pc -> JITTrace store (jit_runtime.md §3.3's JitEntryIndex is
    a flat_map_view over a sorted array). Removal is a tombstone (a live
    key's value slot set to None), never a physical shift: within one bank,
    inserts and removes are never interleaved mid-operation, and the whole
    bank is wiped by `clear()` a few rotations after it starts filling up
    regardless, so tombstones never accumulate beyond one bank's short
    lifetime. Re-inserting an already-present (live or tombstoned) key
    reuses its existing slot in O(log n); only a genuinely new key ever
    pays the O(n) shift a sorted array requires.
    """

    __slots__ = (
        "_keys",
        "_values",
        "bank_id",
        "capacity_bytes",
        "code_offset_bytes",
        "entry_capacity",
        "inbound_sources",
        "used_bytes",
    )

    def __init__(
        self,
        bank_id: int,
        capacity_bytes: int = JIT_CACHE_BANK_CAPACITY_BYTES,
        code_offset_bytes: int = 0,
    ):
        self.bank_id = bank_id
        self.code_offset_bytes = code_offset_bytes
        assert capacity_bytes >= JIT_X64_TRACE_HEADER_BYTES
        self.capacity_bytes = capacity_bytes
        self.entry_capacity = max(1, capacity_bytes // JIT_X64_TRACE_HEADER_BYTES)
        self.used_bytes = 0
        self._keys: StaticVector[int] = StaticVector(capacity=self.entry_capacity)
        self._values: StaticVector[JITTrace | None] = StaticVector(
            capacity=self.entry_capacity
        )  # None marks a tombstoned slot
        self.inbound_sources: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_INBOUND_SOURCES)

    def _live_index(self, head_pc: int) -> int | None:
        idx = bisect.bisect_left(self._keys, head_pc)
        if idx < len(self._keys) and self._keys[idx] == head_pc and self._values[idx] is not None:
            return idx
        return None

    @property
    def traces(self) -> StaticVector[tuple[int, JITTrace]]:
        traces: StaticVector[tuple[int, JITTrace]] = StaticVector(capacity=self.entry_capacity)
        for pc, trace in zip(self._keys, self._values, strict=True):
            if trace is not None:
                traces.append((pc, trace))
        return traces

    def get_trace(self, head_pc: int) -> JITTrace | None:
        idx = self._live_index(head_pc)
        return self._values[idx] if idx is not None else None

    def has_trace(self, head_pc: int) -> bool:
        return self._live_index(head_pc) is not None

    def remove_trace(self, head_pc: int) -> JITTrace | None:
        idx = self._live_index(head_pc)
        if idx is None:
            return None
        trace = self._values[idx]
        self._values[idx] = None
        return trace

    def clear(self) -> StaticVector[int]:
        purged: StaticVector[int] = StaticVector(capacity=self.entry_capacity)
        for pc, trace in zip(self._keys, self._values, strict=True):
            if trace is not None:
                purged.append(pc)
        self._keys.clear()
        self._values.clear()
        self.inbound_sources.clear()
        self.used_bytes = 0
        return purged

    def allocate(self, trace: JITTrace) -> bool:
        prev = self.get_trace(trace.head_pc)
        delta = trace.size_bytes - (prev.size_bytes if prev else 0)
        if self.used_bytes + delta > self.capacity_bytes:
            return False
        if prev is not None and prev.code_offset is not None:
            trace.code_offset = prev.code_offset
        else:
            trace.code_offset = self.code_offset_bytes + self.used_bytes
        idx = bisect.bisect_left(self._keys, trace.head_pc)
        if idx < len(self._keys) and self._keys[idx] == trace.head_pc:
            self._values[idx] = trace  # reuse the existing (live or tombstoned) slot
        else:
            assert self._keys.insert_at(idx, trace.head_pc)
            assert self._values.insert_at(idx, trace)
        self.used_bytes += delta
        return True


class JITMultiBufferCache:
    """3-bank rotating JIT cache with bounded O(n + k log n) purge and XOR lookup."""

    __slots__ = (
        "_fast_cache",
        "active_idx",
        "banks",
        "code_region",
        "evictions",
        "generation",
        "oldest_idx",
        "on_evict",
        "on_rotate",
        "promotions",
        "warm_idx",
    )

    NUM_FAST_SLOTS = JIT_CACHE_FAST_SLOT_COUNT

    def __init__(
        self,
        bank_capacity: int = JIT_CACHE_BANK_CAPACITY_BYTES,
        allocator: BumpAllocator | None = None,
    ):
        assert 0 < bank_capacity <= JIT_CACHE_BANK_CAPACITY_BYTES
        bank_offsets = (
            JIT_CACHE_ACTIVE_OFFSET_BYTES,
            JIT_CACHE_WARM_OFFSET_BYTES,
            JIT_CACHE_OLDEST_OFFSET_BYTES,
        )
        self.banks: StaticVector[JITCacheBank] = StaticVector(capacity=JIT_CACHE_BANK_COUNT)
        for i in range(JIT_CACHE_BANK_COUNT):
            self.banks.append(JITCacheBank(i, bank_capacity, bank_offsets[i]))
        self.code_region = JITCodeCacheRegion()
        self.active_idx, self.warm_idx, self.oldest_idx = 0, 1, 2
        self.promotions = 0
        self.evictions = 0
        self.generation = 0
        self.on_evict: Callable[[StaticVector[int]], None] | None = None
        # Called once at the end of every rotate() (never by flush_all()); the
        # runtime engine runs one {JIT_CardAgingSweep} step from it.
        self.on_rotate: Callable[[], int] | None = None
        # Direct-mapped 16-slot cache keyed by a repeatedly folded XOR over
        # UnifiedPC.
        self._fast_cache = NativeFastCache(allocator)

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Bind C++-visible lookup storage to the owning runtime arena."""

        self._fast_cache.bind_allocator(allocator)

    @property
    def active(self) -> JITCacheBank:
        return self.banks[self.active_idx]

    @property
    def common_code(self) -> JITCodeCacheRegion:
        """Return the non-evictable common prefix of the code region."""

        return self.code_region

    @property
    def warm(self) -> JITCacheBank:
        return self.banks[self.warm_idx]

    @property
    def oldest(self) -> JITCacheBank:
        return self.banks[self.oldest_idx]

    def find_bank(self, head_pc: int) -> JITCacheBank | None:
        for bank in self.banks:
            if bank.has_trace(head_pc):
                return bank
        return None

    def find_trace(self, head_pc: int) -> JITTrace | None:
        for bank in self.banks:
            trace = bank.get_trace(head_pc)
            if trace is not None:
                return trace
        return None

    def register_chain(self, source_pc: int, target_pc: int) -> None:
        source = self.find_trace(source_pc)
        target = self.find_trace(target_pc)
        if source is not None and target is not None:
            self._link_chain(source, target)

    @staticmethod
    def _chain_eligible(source: JITTrace) -> bool:
        return source.next_pc is not None and source.loops_to is None and not source.has_return_val

    def _set_chain_target(self, source: JITTrace, target: JITTrace | None) -> None:
        source.header.chain_target_addr = (
            0
            if target is None or target.raw_addr is None
            else target.raw_addr + TRACE_ENTRY_STUB_BYTES
        )
        if source.code_offset is not None and source.raw_addr is not None:
            self.code_region.patch_header_u64(
                source.code_offset,
                source.header.chain_target_addr,
            )

    def _link_chain(self, source: JITTrace, target: JITTrace) -> None:
        assert source.next_pc == target.head_pc
        assert self._chain_eligible(source)
        target_bank = self.find_bank(target.head_pc)
        assert target_bank is self.active or target_bank is self.warm
        source.chain_next = target.head_pc
        self._set_chain_target(source, target)
        if not target_bank.inbound_sources.contains(source.head_pc):
            target_bank.inbound_sources.append(source.head_pc)
        self.generation += 1

    def _unlink_chain(self, source: JITTrace) -> None:
        source.chain_next = None
        source.header.chain_target_addr = 0
        if source.code_offset is not None and source.raw_addr is not None:
            self.code_region.patch_header_u64(source.code_offset, 0)
        self.generation += 1

    def _try_link_chain(self, source: JITTrace) -> None:
        if not self._chain_eligible(source):
            self._unlink_chain(source)
            return
        target_pc = source.next_pc
        assert target_pc is not None
        target = self.find_trace(target_pc)
        target_bank = self.find_bank(target_pc)
        if target is not None and (target_bank is self.active or target_bank is self.warm):
            self._link_chain(source, target)
        else:
            self._unlink_chain(source)

    def _install_trace(self, trace: JITTrace) -> None:
        """Install compiled bytes into the shared rotating code region."""

        if trace.code_blob is None:
            return
        assert trace.code_offset is not None
        assert len(trace.code_blob) == trace.size_bytes
        fn, raw_addr = self.code_region.install_trace(
            trace.code_offset,
            trace.code_blob,
            trace.entry_body_patch_offset,
            trace.entry_prologue_patch_offset,
            trace.exit_patch_offset,
            trace.helper_header_patch_offset,
            trace.helper_exit_patch_offset,
            trace.chain_dispatch_patch_offset,
            trace.header.common_helper_offset,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self.code_region.buffer

    def lookup(self, head_pc: int) -> JITTrace | None:
        cached = self._fast_cache.lookup(head_pc)
        if cached is not None:
            return cached

        trace = self.active.get_trace(head_pc)
        if trace is not None:
            self._fast_cache.store(head_pc, trace)
            return trace
        trace = self.warm.get_trace(head_pc)
        if trace is not None:
            self._fast_cache.store(head_pc, trace)
            return trace
        trace = self.oldest.get_trace(head_pc)
        if trace is None:
            return None
        # Oldest bank hit: promote to Active bank immediately.
        old_oldest = self.oldest
        old_oldest.remove_trace(head_pc)
        old_oldest.used_bytes -= trace.size_bytes
        trace.flags |= JITTraceHeader.FLAG_PROMOTED
        # Any inbound chain sources registered against the bank this trace
        # used to live in must follow it to wherever it lands -- captured
        # now, before a possible rotate() below clears old_oldest's own
        # inbound_sources as a side effect of purging a *different* bank's
        # worth of traces into it. Without this, a later rotate() looks for
        # these sources in the bank that used to hold the promoted trace,
        # never finds them there anymore, and never unlinks them to the
        # interpreter fallback once this trace is eventually purged for real.
        following_sources: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_INBOUND_SOURCES)
        for src_pc in old_oldest.inbound_sources:
            src_trace = self.find_trace(src_pc)
            if src_trace is not None and src_trace.chain_next == head_pc:
                following_sources.append(src_pc)
        for src_pc in following_sources:
            old_oldest.inbound_sources.remove(src_pc)
            src_trace = self.find_trace(src_pc)
            if src_trace is not None:
                self._unlink_chain(src_trace)

        if not self.active.allocate(trace):
            self.rotate()
            assert self.active.allocate(trace)
        self._install_trace(trace)

        target_bank = self.find_bank(head_pc)
        if target_bank is not None:
            for src_pc in following_sources:
                src_trace = self.find_trace(src_pc)
                if src_trace is not None:
                    self._link_chain(src_trace, trace)

        self._try_link_chain(trace)

        self.promotions += 1
        self._fast_cache.store(head_pc, trace)
        self.generation += 1
        return trace

    def insert(self, trace: JITTrace) -> bool:
        if not self.active.allocate(trace):
            self.rotate()
            if not self.active.allocate(trace):
                return False
        self._install_trace(trace)
        self._try_link_chain(trace)
        # Forward chaining: check if any resident trace in active/warm can now chain into this trace
        for b in (self.active, self.warm):
            for _, resident_t in b.traces:
                if resident_t.chain_next is None and self._chain_eligible(resident_t):
                    res_succ = resident_t.next_pc
                    if res_succ == trace.head_pc:
                        self._link_chain(resident_t, trace)
        self._fast_cache.store(trace.head_pc, trace)
        self.generation += 1
        return True

    def rotate(self) -> StaticVector[int]:
        """
        Rotates Active -> Warm -> Oldest -> Active and purges the old Oldest bank.
        Performs bounded inbound unlinking plus a full purge-bank clear.
        """

        new_active = self.oldest_idx
        new_warm = self.active_idx
        new_oldest = self.warm_idx
        old_oldest_bank = self.banks[new_active]
        # Unlink inbound chains, then clear all slots in the bank being purged.
        for src_pc in old_oldest_bank.inbound_sources:
            src_trace = self.find_trace(src_pc)
            target_pc = src_trace.chain_next if src_trace is not None else None
            if target_pc is not None and old_oldest_bank.has_trace(target_pc):
                # Check if target was promoted to Active
                target_in_active = self.banks[new_warm].get_trace(target_pc) or self.banks[
                    self.active_idx
                ].get_trace(target_pc)
                if target_in_active is None:
                    self._unlink_chain(src_trace)

        purged_pcs = old_oldest_bank.clear()
        self.evictions += len(purged_pcs)
        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest
        self._fast_cache.clear()
        if self.on_evict and purged_pcs:
            self.on_evict(purged_pcs)
        if self.on_rotate:
            self.on_rotate()
        self.generation += 1
        return purged_pcs

    def flush_all(self) -> None:
        """Invalidates all JIT cache banks and unlinks chains."""
        for bank in self.banks:
            for _, trace in bank.traces:
                if trace.chain_next is not None:
                    self._unlink_chain(trace)
            purged = bank.clear()
            self.evictions += len(purged)
            if self.on_evict and purged:
                self.on_evict(purged)
        self._fast_cache.clear()
        self.generation += 1
