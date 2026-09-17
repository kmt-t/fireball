"""Tier 3 JIT profiling state and rotating code-cache components.

The Tier 2 runtime owns execution orchestration; this module owns the JIT
state machine, trace descriptors, and cache residency policy.
"""

from __future__ import annotations

import bisect
import ctypes
from collections.abc import Callable
from typing import TYPE_CHECKING

from common_code import (
    COMMON_ABSOLUTE_POOL_OFFSET,
    COMMON_EPILOGUE_OFFSET,
    COMMON_HELPER_OFFSET,
    COMMON_PROLOGUE_OFFSET,
    TRACE_ENTRY_STUB_BYTES,
    JITCodeCacheRegion,
)
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

if TYPE_CHECKING:
    from execution_context import WASMContext


NativeTraceFn = Callable[
    [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, int], int | None
]
TraceArgument = ctypes.c_void_p


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
# FB_CONF-style bounds (JITMultiBufferCache.NUM_FAST_SLOTS=4,
# RuntimeEngine.compile_queue_capacity=4).
FB_CONF_MAX_INBOUND_SOURCES = JIT_CACHE_MAX_INBOUND_SOURCES
class HotspotBitmap:
    """Per-function 2-bit card state with one owned storage per function."""

    __slots__ = ("card_shift", "func_storages")

    def __init__(self, card_shift: int = JIT_CARD_SHIFT, code_lengths: tuple[int, ...] = ()):
        self.card_shift = card_shift
        assert all(code_len >= 0 for code_len in code_lengths)
        self.func_storages: StaticVector[MutableBitStorage] = StaticVector(
            capacity=len(code_lengths)
        )
        for code_len in code_lengths:
            self.func_storages.append(
                MutableBitStorage(
                    count=max(1, (code_len + (1 << card_shift) - 1) >> card_shift),
                    bits=2,
                )
            )

    def _split_pc(self, pc: int) -> tuple[int, int]:
        if pc > 0xFFFF:
            func_idx = (pc >> 16) & 0xFFFF
            offset = pc & 0xFFFF
        else:
            func_idx = 0
            offset = pc
        return func_idx, offset

    def card_of(self, pc: int) -> int:
        _, offset = self._split_pc(pc)
        return offset >> self.card_shift

    def get_state(self, pc: int) -> int:
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        return storage.view().at(card)

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        s = storage.view().at(card)
        if s == CardState.COMPILED:
            return s
        if s == CardState.UNEXECUTED:
            s = CardState.EXECUTED
        elif s == CardState.EXECUTED:
            s = CardState.HOT
        storage.put(card, s)
        return s

    def mark_compiled(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        storage.put(card, CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        """
        Evicted trace resets card state to UNEXECUTED (00), not EXECUTED:
        the trace fell out of cache favor once already, so it must re-earn
        hotness through the full warm-up cycle again rather than jumping
        straight back to HOT after a single touch -- otherwise a
        marginally-hot card would thrash between compile and evict forever.
        """
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        storage.put(card, CardState.UNEXECUTED)


class BlockCardMask:
    """
    Per-function 1-bit-per-CARD mask, mirroring `HotspotBitmap`'s own
    per-function `MutableBitStorage` at the same `card_shift`. Initialized at
    `register_module_blocks` time from a property already fully known then;
    a compile failure may clear one bit permanently, but eviction and cache
    flush do not restore or clear candidate eligibility.
    """

    __slots__ = ("card_shift", "func_storages")

    def __init__(self, card_shift: int = JIT_CARD_SHIFT, code_lengths: tuple[int, ...] = ()):
        self.card_shift = card_shift
        assert all(code_len >= 0 for code_len in code_lengths)
        self.func_storages: StaticVector[MutableBitStorage] = StaticVector(
            capacity=len(code_lengths)
        )
        for code_len in code_lengths:
            self.func_storages.append(
                MutableBitStorage(
                    count=max(1, (code_len + (1 << card_shift) - 1) >> card_shift),
                    bits=1,
                )
            )

    def _split_pc(self, pc: int) -> tuple[int, int]:
        if pc > 0xFFFF:
            return (pc >> 16) & 0xFFFF, pc & 0xFFFF
        return 0, pc

    def mark(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        card = offset >> self.card_shift
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        assert card < storage.count
        storage.put(card, 1)

    def is_marked(self, pc: int) -> bool:
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        return storage.view().at(card) != 0

    def unmark(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        assert func_idx < len(self.func_storages)
        storage = self.func_storages[func_idx]
        card = offset >> self.card_shift
        assert card < storage.count
        storage.put(card, 0)

    def clear(self) -> None:
        for storage in self.func_storages:
            storage.clear()


class HistoryRing:
    """Fixed-size ring of recently executed basic-block head PCs backed by RingBuffer."""

    __slots__ = ("ring",)

    def __init__(self, capacity: int = 32):
        self.ring: RingBuffer[int] = RingBuffer(capacity)

    @property
    def capacity(self) -> int:
        return self.ring.capacity

    @property
    def dropped(self) -> int:
        return self.ring.dropped

    def record(self, pc: int) -> None:
        self.ring.push(pc)

    def drain(self) -> StaticVector[int]:
        return self.ring.drain()


class JITTraceHeader:
    """
    x64-specific 56-byte fixed physical memory layout:
        +0x00 head_wasm_pc(u32)
        +0x04 trace_byte_size(u16)
        +0x06 flags(u8) [0x01: PROMOTED, 0x02: LOOP_HEADER]
        +0x07 variant_id(u8)
        +0x08 chain_next_pc(u32)
        +0x0C reserved(u32)
        +0x10 chain_target_addr(u64)
        +0x18 common_prologue_offset(u32)
        +0x1C common_epilogue_offset(u32)
        +0x20 common_helper_offset(u32)
        +0x24 helper_index(u32)
        +0x28 helper_target_addr(u64)
        +0x30 absolute_pool_offset(u32)
        +0x34 reserved(u32)
    """

    __slots__ = (
        "absolute_pool_offset",
        "chain_next_pc",
        "chain_target_addr",
        "common_epilogue_offset",
        "common_helper_offset",
        "common_prologue_offset",
        "flags",
        "head_wasm_pc",
        "helper_index",
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
        helper_index: int = -1,
        helper_target_addr: int = 0,
    ):
        self.head_wasm_pc = head_wasm_pc & 0xFFFF_FFFF
        assert trace_byte_size >= JIT_X64_TRACE_HEADER_BYTES
        self.trace_byte_size = trace_byte_size & 0xFFFF
        self.flags = flags & 0xFF
        self.variant_id = variant_id & 0xFF
        self.chain_next_pc: int | None = None
        self.chain_target_addr: int | None = None
        self.common_prologue_offset = COMMON_PROLOGUE_OFFSET
        self.common_epilogue_offset = COMMON_EPILOGUE_OFFSET
        self.common_helper_offset = COMMON_HELPER_OFFSET
        self.helper_index = helper_index
        assert 0 <= helper_target_addr <= 0xFFFF_FFFF_FFFF_FFFF
        self.helper_target_addr = helper_target_addr
        self.absolute_pool_offset = COMMON_ABSOLUTE_POOL_OFFSET

    def pack(self) -> bytes:
        import struct

        return struct.pack(
            "<IHBBIIQIIIIQII",
            self.head_wasm_pc,
            self.trace_byte_size,
            self.flags,
            self.variant_id,
            self.chain_next_pc or 0,
            0,
            self.chain_target_addr or 0,
            self.common_prologue_offset,
            self.common_epilogue_offset,
            self.common_helper_offset,
            self.helper_index & 0xFFFF_FFFF,
            self.helper_target_addr,
            self.absolute_pool_offset,
            0,
        )


class JITTrace:
    """Compiled native trace descriptor backed by JITTraceHeader and native ctypes function pointer."""

    __slots__ = (
        "_exec_buf",
        "_keepalive",
        "chain_fallback_patch_offset",
        "chain_header_patch_offset",
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
        buf: ctypes.Array[ctypes.c_ubyte] | None = None,
        native_fn: NativeTraceFn | None = None,
        raw_addr: int | None = None,
        code_blob: bytes | None = None,
        entry_body_patch_offset: int = -1,
        entry_prologue_patch_offset: int = -1,
        exit_patch_offset: int = -1,
        helper_header_patch_offset: int = -1,
        helper_exit_patch_offset: int = -1,
        chain_header_patch_offset: int = -1,
        chain_fallback_patch_offset: int = -1,
        helper_index: int = -1,
        helper_target_addr: int = 0,
    ):
        self.head_pc = head_pc
        self.fn = fn or native_fn  # Direct ctypes CFUNCTYPE function pointer or callable
        self.raw_addr = raw_addr  # Entry point as a plain int, for native_trace_call
        self.code_blob = code_blob
        self.code_offset: int | None = None
        self.entry_body_patch_offset = entry_body_patch_offset
        self.entry_prologue_patch_offset = entry_prologue_patch_offset
        self.exit_patch_offset = exit_patch_offset
        self.helper_header_patch_offset = helper_header_patch_offset
        self.helper_exit_patch_offset = helper_exit_patch_offset
        self.chain_header_patch_offset = chain_header_patch_offset
        self.chain_fallback_patch_offset = chain_fallback_patch_offset
        assert size_bytes >= JIT_X64_TRACE_HEADER_BYTES
        self.size_bytes = size_bytes
        self.next_pc = next_pc  # Unconditional fallthrough successor
        self.loops_to = loops_to  # Conditional loop backedge (never auto-chained)
        self.has_return_val = has_return_val
        assert result_words > 0
        self.result_words = result_words
        self.header = JITTraceHeader(
            head_wasm_pc=head_pc,
            trace_byte_size=size_bytes,
            helper_index=helper_index,
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

    def invoke(self, ctx: WASMContext) -> int:
        """Helper to invoke trace directly on WASMContext via CPS 4-argument calling convention."""
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
        traces: StaticVector[tuple[int, JITTrace]] = StaticVector(
            capacity=self.entry_capacity
        )
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
        "_fast_slots",
        "active_idx",
        "banks",
        "code_region",
        "evictions",
        "oldest_idx",
        "on_evict",
        "promotions",
        "warm_idx",
    )

    NUM_FAST_SLOTS = JIT_CACHE_FAST_SLOT_COUNT

    def __init__(self, bank_capacity: int = JIT_CACHE_BANK_CAPACITY_BYTES):
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
        self.on_evict: Callable[[StaticVector[int]], None] | None = None
        # Direct-mapped 4-slot cache keyed by a repeatedly folded XOR over
        # UnifiedPC.
        self._fast_slots: StaticVector[tuple[int, JITTrace] | None] = StaticVector(
            capacity=self.NUM_FAST_SLOTS
        )
        for _ in range(self.NUM_FAST_SLOTS):
            self._fast_slots.append(None)

    def _hash_slot(self, pc: int) -> int:
        """Fold a 32-bit UnifiedPC with four XORs and select two bits."""
        temp = pc ^ (pc >> 16)
        temp = temp ^ (temp >> 8)
        temp = temp ^ (temp >> 4)
        temp = temp ^ (temp >> 2)
        return temp & (self.NUM_FAST_SLOTS - 1)

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
        return (
            source.next_pc is not None
            and source.loops_to is None
            and not source.has_return_val
        )

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
        source.header.chain_next_pc = target.head_pc
        self._set_chain_target(source, target)
        if not target_bank.inbound_sources.contains(source.head_pc):
            assert target_bank.inbound_sources.push_back(source.head_pc)

    def _unlink_chain(self, source: JITTrace) -> None:
        source.chain_next = None
        source.header.chain_target_addr = 0
        if source.code_offset is not None and source.raw_addr is not None:
            self.code_region.patch_header_u64(source.code_offset, 0)

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
            trace.chain_header_patch_offset,
            trace.chain_fallback_patch_offset,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self.code_region.buffer

    def lookup(self, head_pc: int) -> JITTrace | None:
        slot = self._hash_slot(head_pc)
        cached = self._fast_slots[slot]
        if cached is not None and cached[0] == head_pc:
            return cached[1]

        trace = self.active.get_trace(head_pc)
        if trace is not None:
            self._fast_slots[slot] = (head_pc, trace)
            return trace
        trace = self.warm.get_trace(head_pc)
        if trace is not None:
            self._fast_slots[slot] = (head_pc, trace)
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
        following_sources: StaticVector[int] = StaticVector(
            capacity=FB_CONF_MAX_INBOUND_SOURCES
        )
        for src_pc in old_oldest.inbound_sources:
            src_trace = self.find_trace(src_pc)
            if src_trace is not None and src_trace.chain_next == head_pc:
                following_sources.append(src_pc)
        for src_pc in following_sources:
            old_oldest.inbound_sources.remove(src_pc)

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
        self._fast_slots[slot] = (head_pc, trace)
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
        slot = self._hash_slot(trace.head_pc)
        self._fast_slots[slot] = (trace.head_pc, trace)
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
            if (
                src_trace is not None
                and src_trace.chain_next is not None
                and old_oldest_bank.has_trace(src_trace.chain_next)
            ):
                # Check if target was promoted to Active
                target_in_active = self.banks[new_warm].get_trace(
                    src_trace.chain_next
                ) or self.banks[self.active_idx].get_trace(src_trace.chain_next)
                if target_in_active is None:
                    self._unlink_chain(src_trace)

        purged_pcs = old_oldest_bank.clear()
        self.evictions += len(purged_pcs)
        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest
        self._fast_slots = StaticVector(capacity=self.NUM_FAST_SLOTS)
        for _ in range(self.NUM_FAST_SLOTS):
            self._fast_slots.append(None)
        if self.on_evict and purged_pcs:
            self.on_evict(purged_pcs)
        return purged_pcs

    def flush_all(self) -> None:
        """Invalidates all JIT cache banks and unlinks chains ({Debugger_Jit_Flush})."""
        for bank in self.banks:
            for _, trace in bank.traces:
                if trace.chain_next is not None:
                    self._unlink_chain(trace)
            purged = bank.clear()
            self.evictions += len(purged)
            if self.on_evict and purged:
                self.on_evict(purged)
        self._fast_slots = StaticVector(capacity=self.NUM_FAST_SLOTS)
        for _ in range(self.NUM_FAST_SLOTS):
            self._fast_slots.append(None)
