"""Tier 3 JIT profiling state and rotating code-cache components.

The Tier 2 runtime owns execution orchestration; this module owns the JIT
state machine, trace descriptors, and cache residency policy.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable

from system_containers import (
    BitView,
    MutableBitStorage,
    RadixBinaryTreeView,
    RingBuffer,
    StaticVector,
    bswap32,
)


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
# RuntimeEngine.compile_queue_capacity=4).
FB_CONF_MAX_INBOUND_SOURCES = 16


class HotspotBitmap:
    """Per-Function 2-bit state per CARD backed by MutableBitStorage and non-owning BitView<2>.
    `func_storages` owns the backing bit buffers.
    `func_tables` borrows non-owning BitViews indexed by `func_idx`.
    """

    __slots__ = ("card_shift", "default_func_code_len", "func_storages", "func_tables")

    def __init__(self, card_shift: int = 3, default_func_code_len: int = 64):
        self.card_shift = card_shift
        self.default_func_code_len = default_func_code_len
        self.func_storages: list[MutableBitStorage | None] = []
        self.func_tables: list[BitView | None] = []

    def allocate_functions(self, num_functions: int) -> None:
        """Allocates static slot array for known number of functions at load time."""
        if len(self.func_tables) < num_functions:
            delta = num_functions - len(self.func_tables)
            self.func_storages.extend([None] * delta)
            self.func_tables.extend([None] * delta)

    def register_function(self, func_idx: int, code_len: int) -> BitView:
        """Allocates a dedicated MutableBitStorage<2> matching the exact function code length."""
        if func_idx >= len(self.func_tables):
            delta = func_idx + 1 - len(self.func_tables)
            self.func_storages.extend([None] * delta)
            self.func_tables.extend([None] * delta)
        card_count = max(1, (code_len + (1 << self.card_shift) - 1) >> self.card_shift)
        storage = MutableBitStorage(count=card_count, bits=2)
        if self.func_storages[func_idx] is not None:
            old_buf = self.func_storages[func_idx].buffer
            storage.buffer[: min(len(storage.buffer), len(old_buf))] = old_buf[
                : min(len(storage.buffer), len(old_buf))
            ]
        view = storage.view()
        self.func_storages[func_idx] = storage
        self.func_tables[func_idx] = view
        return view

    def _get_or_create_view(self, func_idx: int) -> BitView:
        if func_idx < len(self.func_tables) and self.func_tables[func_idx] is not None:
            return self.func_tables[func_idx]  # type: ignore[return-value]
        return self.register_function(func_idx, self.default_func_code_len)

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
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            return CardState.UNEXECUTED
        return view.at(card)

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            view = self.register_function(func_idx, (card + 1) << self.card_shift)
        s = view.at(card)
        if s == CardState.COMPILED:
            return s
        if s == CardState.UNEXECUTED:
            s = CardState.EXECUTED
        elif s == CardState.EXECUTED:
            s = CardState.HOT
        view.put(card, s)
        return s

    def mark_compiled(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            view = self.register_function(func_idx, (card + 1) << self.card_shift)
        view.put(card, CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        """
        Evicted trace resets card state to UNEXECUTED (00), not EXECUTED:
        the trace fell out of cache favor once already, so it must re-earn
        hotness through the full warm-up cycle again rather than jumping
        straight back to HOT after a single touch -- otherwise a
        marginally-hot card would thrash between compile and evict forever.
        """
        func_idx, offset = self._split_pc(pc)
        if func_idx < len(self.func_tables) and self.func_tables[func_idx] is not None:
            view = self.func_tables[func_idx]
            card = offset >> self.card_shift
            if card < view.size():  # type: ignore[union-attr]
                view.put(card, CardState.UNEXECUTED)  # type: ignore[union-attr]


class BlockCardMask:
    """
    Per-function 1-bit-per-CARD mask, mirroring `HotspotBitmap`'s own
    per-function `MutableBitStorage`/`BitView` split at the same
    `card_shift`. Write-once at `register_module_blocks` time from a
    property already fully known then (never re-derived per dispatch),
    read-only for the rest of the run.
    """

    __slots__ = ("card_shift", "func_storages", "func_tables")

    def __init__(self, card_shift: int = 3):
        self.card_shift = card_shift
        self.func_storages: list[MutableBitStorage | None] = []
        self.func_tables: list[BitView | None] = []

    def _split_pc(self, pc: int) -> tuple[int, int]:
        if pc > 0xFFFF:
            return (pc >> 16) & 0xFFFF, pc & 0xFFFF
        return 0, pc

    def mark(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        card = offset >> self.card_shift
        if func_idx >= len(self.func_tables):
            delta = func_idx + 1 - len(self.func_tables)
            self.func_storages.extend([None] * delta)
            self.func_tables.extend([None] * delta)
        view = self.func_tables[func_idx]
        if view is None or card >= view.size():
            storage = MutableBitStorage(count=card + 1, bits=1)
            old = self.func_storages[func_idx]
            if old is not None:
                storage.buffer[: len(old.buffer)] = old.buffer
            view = storage.view()
            self.func_storages[func_idx] = storage
            self.func_tables[func_idx] = view
        view.put(card, 1)

    def is_marked(self, pc: int) -> bool:
        func_idx, offset = self._split_pc(pc)
        if func_idx >= len(self.func_tables) or self.func_tables[func_idx] is None:
            return False
        view = self.func_tables[func_idx]
        card = offset >> self.card_shift
        if card >= view.size():  # type: ignore[union-attr]
            return False
        return view.at(card) != 0  # type: ignore[union-attr]

    def unmark(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        if func_idx >= len(self.func_tables) or self.func_tables[func_idx] is None:
            return
        view = self.func_tables[func_idx]
        card = offset >> self.card_shift
        if card < view.size():  # type: ignore[union-attr]
            view.put(card, 0)  # type: ignore[union-attr]

    def clear(self) -> None:
        self.func_storages = []
        self.func_tables = []


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

    def drain(self) -> list[int]:
        return self.ring.drain()


class JITTraceHeader:
    """
    16-byte fixed physical memory layout:
        +0x00 head_wasm_pc(u32)
        +0x04 trace_byte_size(u16)
        +0x06 flags(u8) [0x01: PROMOTED, 0x02: LOOP_HEADER]
        +0x07 variant_id(u8)
        +0x08 chain_next_pc(u32)
        +0x0C chain_target_addr(u32)
    """

    __slots__ = (
        "chain_next_pc",
        "chain_target_addr",
        "flags",
        "head_wasm_pc",
        "trace_byte_size",
        "variant_id",
    )

    FLAG_PROMOTED = 0x01
    FLAG_LOOP_HEADER = 0x02

    def __init__(
        self,
        head_wasm_pc: int,
        trace_byte_size: int = 64,
        flags: int = 0,
        variant_id: int = 0,
    ):
        self.head_wasm_pc = head_wasm_pc & 0xFFFF_FFFF
        self.trace_byte_size = trace_byte_size & 0xFFFF
        self.flags = flags & 0xFF
        self.variant_id = variant_id & 0xFF
        self.chain_next_pc: int | None = None
        self.chain_target_addr: int | None = None

    def pack(self) -> bytes:
        import struct

        return struct.pack(
            "<IHBBII",
            self.head_wasm_pc,
            self.trace_byte_size,
            self.flags,
            self.variant_id,
            self.chain_next_pc or 0,
            self.chain_target_addr or 0,
        )


class JITTrace:
    """Compiled native trace descriptor backed by JITTraceHeader and native ctypes function pointer."""

    __slots__ = (
        "__dict__",
        "_exec_buf",
        "_keepalive",
        "chain_next",
        "exec_count",
        "fn",
        "has_return_val",
        "head_pc",
        "header",
        "loops_to",
        "next_pc",
        "raw_addr",
        "size_bytes",
    )

    def __init__(
        self,
        head_pc: int,
        fn: Callable[[int, object, object, int], int] | None = None,
        size_bytes: int = 64,
        next_pc: int | None = None,
        loops_to: int | None = None,
        has_return_val: bool = False,
        buf: object = None,
        native_fn: Callable[[int, object, object, int], int] | None = None,
        raw_addr: int | None = None,
    ):
        self.head_pc = head_pc
        self.fn = fn or native_fn  # Direct ctypes CFUNCTYPE function pointer or callable
        self.raw_addr = raw_addr  # Entry point as a plain int, for native_trace_call
        self.size_bytes = size_bytes
        self.next_pc = next_pc  # Unconditional fallthrough successor
        self.loops_to = loops_to  # Conditional loop backedge (never auto-chained)
        self.has_return_val = has_return_val
        self.header = JITTraceHeader(head_wasm_pc=head_pc, trace_byte_size=size_bytes)
        self.chain_next: int | None = None
        self.exec_count: int = 0
        self._exec_buf = buf  # Keeps executable buffer alive in memory

    @property
    def native_fn(self) -> Callable[..., int] | None:
        return self.fn

    @property
    def flags(self) -> int:
        return self.header.flags

    @flags.setter
    def flags(self, val: int) -> None:
        self.header.flags = val

    def __call__(
        self,
        ctx_or_locals: object | list[int],
        sp_or_mem: int | object = 0,
        local_base: int = 0,
        tos: int = 0,
    ) -> int:
        """
        Invokes the native JIT trace directly via ctypes CPS 4-argument calling convention:
                (void* ctx, void* sp, void* local_base, uint32_t tos)
        """

        return self.fn(ctx_or_locals, sp_or_mem, local_base, tos)

    def invoke(self, ctx: object) -> int:
        """Helper to invoke trace directly on WASMContext via CPS 4-argument calling convention."""
        tos = ctx.pop() if ctx.stack else 0
        self.fn(ctx.context_ptr, ctx.sp_ptr, ctx.locals_ptr, tos)
        if self.has_return_val:
            ctx.push(ctx._c_result.value & 0xFFFF_FFFF)
        return ctx._c_result.value


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
        "inbound_sources",
        "used_bytes",
    )

    def __init__(self, bank_id: int, capacity_bytes: int = 2048):
        self.bank_id = bank_id
        self.capacity_bytes = capacity_bytes
        self.used_bytes = 0
        self._keys: list[int] = []
        self._values: list[JITTrace | None] = []  # None marks a tombstoned slot
        self.inbound_sources: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_INBOUND_SOURCES)

    def _live_index(self, head_pc: int) -> int | None:
        idx = bisect.bisect_left(self._keys, head_pc)
        if idx < len(self._keys) and self._keys[idx] == head_pc and self._values[idx] is not None:
            return idx
        return None

    @property
    def traces(self) -> list[tuple[int, JITTrace]]:
        return [
            (pc, trace)
            for pc, trace in zip(self._keys, self._values, strict=True)
            if trace is not None
        ]

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

    def clear(self) -> list[int]:
        purged = [
            pc for pc, trace in zip(self._keys, self._values, strict=True) if trace is not None
        ]
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
        idx = bisect.bisect_left(self._keys, trace.head_pc)
        if idx < len(self._keys) and self._keys[idx] == trace.head_pc:
            self._values[idx] = trace  # reuse the existing (live or tombstoned) slot
        else:
            self._keys.insert(idx, trace.head_pc)
            self._values.insert(idx, trace)
        self.used_bytes += delta
        return True


class JITMultiBufferCache:
    """3-bank rotating JIT code cache: Active / Warm / Oldest with O(k) bounded unlinking and Direct-Mapped Folding XOR lookup."""

    __slots__ = (
        "__dict__",
        "_fast_slots",
        "active_idx",
        "banks",
        "control_skip_tree",
        "evictions",
        "oldest_idx",
        "on_evict",
        "promotions",
        "warm_idx",
    )

    NUM_FAST_SLOTS = 16

    def __init__(self, bank_capacity: int = 2048):
        self.banks = [JITCacheBank(i, bank_capacity) for i in range(3)]
        self.active_idx, self.warm_idx, self.oldest_idx = 0, 1, 2
        self.promotions = 0
        self.evictions = 0
        self.on_evict: Callable[[list[int]], None] | None = None
        self.control_skip_tree: RadixBinaryTreeView[int] | None = None
        # Direct-mapped 16-slot cache keyed by 4-bit Folding XOR Hash over UnifiedPC
        self._fast_slots: list[tuple[int, JITTrace] | None] = [None] * self.NUM_FAST_SLOTS

    def _hash_slot(self, pc: int) -> int:
        """4-bit Folding XOR Hash over 32-bit UnifiedPC."""
        return ((pc >> 24) ^ (pc >> 16) ^ (pc >> 8) ^ pc) & (self.NUM_FAST_SLOTS - 1)

    @property
    def active(self) -> JITCacheBank:
        return self.banks[self.active_idx]

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
        target_bank = self.find_bank(target_pc)
        if target_bank is not None and source_pc not in target_bank.inbound_sources:
            target_bank.inbound_sources.push_back(source_pc)

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
        following_sources = []
        for src_pc in old_oldest.inbound_sources:
            src_trace = self.find_trace(src_pc)
            if src_trace is not None and src_trace.chain_next == head_pc:
                following_sources.append(src_pc)
        for src_pc in following_sources:
            old_oldest.inbound_sources.remove(src_pc)

        if not self.active.allocate(trace):
            self.rotate()
            self.active.allocate(trace)

        target_bank = self.find_bank(head_pc)
        if target_bank is not None:
            for src_pc in following_sources:
                if src_pc not in target_bank.inbound_sources:
                    target_bank.inbound_sources.push_back(src_pc)

        self.promotions += 1
        self._fast_slots[slot] = (head_pc, trace)
        return trace

    def insert(self, trace: JITTrace) -> bool:
        if not self.active.allocate(trace):
            self.rotate()
            if not self.active.allocate(trace):
                return False
        # Chain into active/warm successor if resident (never oldest, never loops_to)
        succ = trace.next_pc
        if succ is not None and self.control_skip_tree is not None:
            skipped = self.control_skip_tree.find(bswap32(succ))
            if skipped is not None:
                succ = skipped
        if succ is not None and (self.active.has_trace(succ) or self.warm.has_trace(succ)):
            trace.chain_next = succ
            self.register_chain(trace.head_pc, succ)
        # Forward chaining: check if any resident trace in active/warm can now chain into this trace
        for b in (self.active, self.warm):
            for _, resident_t in b.traces:
                if resident_t.chain_next is None and resident_t.next_pc is not None:
                    res_succ = resident_t.next_pc
                    if self.control_skip_tree is not None:
                        res_skipped = self.control_skip_tree.find(bswap32(res_succ))
                        if res_skipped is not None:
                            res_succ = res_skipped
                    if res_succ == trace.head_pc:
                        resident_t.chain_next = trace.head_pc
                        self.register_chain(resident_t.head_pc, trace.head_pc)
        slot = self._hash_slot(trace.head_pc)
        self._fast_slots[slot] = (trace.head_pc, trace)
        return True

    def rotate(self) -> list[int]:
        """
        Rotates Active -> Warm -> Oldest -> Active and purges the old Oldest bank.
                Performs O(k) bounded unlinking on purged inbound sources.
        """

        new_active = self.oldest_idx
        new_warm = self.active_idx
        new_oldest = self.warm_idx
        old_oldest_bank = self.banks[new_active]
        # O(k) Unlink inbound chains pointing to traces in the bank being purged
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
                    src_trace.chain_next = None  # Unlink to interpreter fallback

        purged_pcs = old_oldest_bank.clear()
        self.evictions += len(purged_pcs)
        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest
        self._fast_slots = [None] * self.NUM_FAST_SLOTS
        if self.on_evict and purged_pcs:
            self.on_evict(purged_pcs)
        return purged_pcs

    def flush_all(self) -> None:
        """Invalidates all JIT cache banks and unlinks chains ({Debugger_Jit_Flush})."""
        for bank in self.banks:
            purged = bank.clear()
            self.evictions += len(purged)
            if self.on_evict and purged:
                self.on_evict(purged)
        self._fast_slots = [None] * self.NUM_FAST_SLOTS
