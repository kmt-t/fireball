"""
experiments/pysim/runtime_engine.py
Integrated WASM Tiered Tracing Runtime Engine for pysim.
Implements the 2-bit card-marking hotspot profiler, history ring,
3-bank rotating JIT code cache, and dynamic tiering execution loop
mirroring docs/components/tier2_runtime/runtime_engine.md and
docs/components/tier3_jit/jit_compiler.md.
Execution model:
  Interpreter execution:
    -> at basic-block head PCs: record card index into HistoryRing
    -> 2-bit card state: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)
    -> on yield/idle: drain HistoryRing, promote HOT cards, push trace heads to LIFO compile queue
    -> async/batch JIT compilation into Active cache bank
  JIT trace execution:
    -> lookup in 3-bank cache (Active, Warm, Oldest)
    -> Oldest bank hit triggers immediate Promotion to Active bank
    -> trace chaining with inbound-source unlinking on bank eviction
"""

from __future__ import annotations

import bisect
import ctypes
from collections.abc import Callable, Iterator

from control_flow import iter_block_ops
from interpreter import Interpreter, InterpreterCall
from system_containers import (
    BitView,
    FlatMapView,
    MutableBitStorage,
    MutableRadixBinaryTreeStorage,
    RadixBinaryTreeView,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    RingBuffer,
    StaticVector,
    bswap32,
)
from wasm_module import BasicBlock, Module, TraceBlock
from wasm_opcodes import (
    I32_ADD,
    I32_CONST,
    I32_MUL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)

try:
    import native_trace_call as _native_trace_call
except ImportError:
    # Optional accelerator (see jit/native_trace_call.pyx, jit/build_native.*):
    # not built -- _invoke_trace falls back to the ctypes.CFUNCTYPE path below.
    _native_trace_call = None


class CardState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2  # Queued for compilation
    COMPILED = 3


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

    def __init__(self, card_shift: int = 2, default_func_code_len: int = 64):
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

    def __init__(self, card_shift: int = 2):
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

    def clear(self) -> None:
        self.func_storages = []
        self.func_tables = []


class HistoryRing:
    """Fixed-size ring of recently executed basic-block head PCs backed by RingBuffer."""

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
        ip_or_locals: int | list[int],
        stack_bot_or_mem: int | object = 0,
        local_base: int = 0,
        tos: int = 0,
    ) -> int:
        """
        Invokes the native JIT trace directly via ctypes CPS 4-argument calling convention:
                (uint32_t ip, void* stack_bot, void* local_base, uint32_t tos)
        """

        return self.fn(ip_or_locals, stack_bot_or_mem, local_base, tos)

    def invoke(self, ctx: object) -> int:
        """Helper to invoke trace directly on WASMContext via CPS 4-argument calling convention."""
        tos = ctx.pop() if ctx.stack else 0
        self.fn(self.head_pc, ctx.stack_bot_ptr, ctx.locals_ptr, tos)
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
            if src_trace is not None and src_trace.chain_next in [
                pc for pc, _ in old_oldest_bank.traces
            ]:
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


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    def __init__(
        self,
        jit_compiler: object | None = None,
        yield_threshold: int = 16,
        card_shift: int = 2,
        min_trace_bytes: int | None = None,
        compile_queue_capacity: int = 4,
        block_capacity: int = 64,
    ):
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.trackable = BlockCardMask(card_shift=card_shift)
        self.ring = HistoryRing()
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.jit_compiler = jit_compiler
        self.compile_queue_capacity = compile_queue_capacity
        # LIFO queue: drain_compile_queue() (below) always empties it again
        # the moment it reaches compile_queue_capacity, so that's this
        # StaticVector's exact fixed capacity, never exceeded.
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.blocks: list[tuple[int, BasicBlock]] = []  # Flat slot list instead of dynamic dict
        self.module: Module | None = None
        self.block_storage = MutableRadixBinaryTreeStorage[BasicBlock](
            capacity=block_capacity,
            key_transform=bswap32,
            radix_shift=28,
        )
        self.block_tree = self.block_storage.view()
        self._fast_block_slots: list[tuple[int, BasicBlock | None] | None] = [None] * 16
        self.control_skip_storage: ReadOnlyRadixBinaryTreeStorage[int] | None = None
        self.control_skip_tree: RadixBinaryTreeView[int] | None = None
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        # A card's 2-bit state can only ever describe ONE block: if two
        # distinct block heads shared a card, compiling one would falsely
        # read back as "already compiled" for the other (or evicting one
        # would falsely reset the other's still-resident COMPILED state).
        # Never tracking a block shorter than one card's worth of bytes
        # guarantees every tracked block's next sibling starts at least a
        # full card away, so no two tracked blocks can ever land on the
        # same card -- and it also skips JIT-compiling blocks so short that
        # the interpreter is already faster than a compiled-trace dispatch
        # would be.
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        # Loader-known local counts per function index (params + declared
        # locals), a fixed table built once in register_module_blocks --
        # never derived per JIT call via len(locals_arr)/max(): the loader
        # already knows every function's exact local count at module-load
        # time, so there is nothing to defensively recompute at runtime.
        self._n_locals_by_func: list[int] = []

    def _handle_eviction(self, purged_pcs: list[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        slot = ((pc >> 24) ^ (pc >> 16) ^ (pc >> 8) ^ pc) & 0x0F
        cached = self._fast_block_slots[slot]
        if cached is not None and cached[0] == pc:
            return cached[1]
        blk = None
        if self.module is not None:
            blk = self.module.get_block(pc)
        elif self.block_tree is not None:
            blk = self.block_tree.find(bswap32(pc))
        else:
            for b_pc, b in self.blocks:
                if b_pc == pc:
                    blk = b
                    break
        self._fast_block_slots[slot] = (pc, blk)
        return blk

    def resolve_trace_block(self, pc: int) -> TraceBlock | None:
        """
        Builds this compile's transient `TraceBlock` from the persisted
        `BasicBlock`'s PC metadata plus the owning function's raw bytecode --
        `BasicBlock` itself never stores the op stream (see
        `wasm_module.BasicBlock`); `self.blocks` here only ever comes from a
        real parsed `Module` (`register_module_blocks`), so `self.module` is
        always available whenever `get_block` finds something.
        """
        block = self.get_block(pc)
        if block is None or self.module is None:
            return None
        code = self.module.code_for(pc >> 16)
        ops = iter_block_ops(code, pc & 0xFFFF, block.byte_span)
        return TraceBlock(
            head_pc=pc,
            ops=ops,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            byte_span=block.byte_span,
        )

    def register_module_blocks(self, module: Module) -> None:
        """Binds loader-owned basic blocks and control skip Radix tree from a parsed WASM Module."""
        if module.block_tree is None:
            module.build_basic_block_index()
        self.module = module
        self.control_skip_storage = module.control_skip_storage
        self.control_skip_tree = module.control_skip_tree
        self.cache.control_skip_tree = module.control_skip_tree
        self.blocks = [(b.head_pc, b) for b in module.blocks]
        self._fast_block_slots = [None] * 16
        # `next_pc is not None and byte_span >= min_trace_bytes` is a pure
        # function of static BasicBlock properties + this engine's own
        # min_trace_bytes, both already known here -- decided once per block,
        # not re-derived on every dispatch in record_block_head.
        self.trackable.clear()
        for b in module.blocks:
            if b.next_pc is not None and b.byte_span >= self.min_trace_bytes:
                self.trackable.mark(b.head_pc)
        total_funcs = len(module.imports) + len(module.functions)
        self._n_locals_by_func = [
            max(len(module.locals_layout(idx)), 16) for idx in range(total_funcs)
        ]

    def record_block_head(self, pc: int) -> None:
        """
        Called by `run()` at each basic-block head that has no compiled
        trace yet. Blocks shorter than `min_trace_bytes` (or with no real
        successor) are never recorded here at all -- see the invariant this
        protects in `__init__` -- decided once, in `register_module_blocks`,
        via `self.trackable` rather than re-derived here per call.
        """
        if not self.trackable.is_marked(pc):
            return
        self.ring.record(pc)
        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.on_yield()

    def on_yield(self) -> None:
        """Scans history ring, updates 2-bit card bitmap, and queues HOT traces."""
        self.exec_counter = 0
        drained_pcs = self.ring.drain()
        for pc in drained_pcs:
            new_state = self.bitmap.touch(pc)
            if new_state == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.push_back(pc)
                # JIT compile queue overflow: compile all on the spot!
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()

    def idle_hook(self, budget: int = 4) -> int:
        """
        Drains the LIFO compile queue during COOS idle_hook. {JIT_ReverseCompilationOrder}
                Compiling in reverse order increases immediate chaining probability.
        """

        compiled_count = 0
        while self.compile_queue and compiled_count < budget:
            pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(pc) is not None:
                # Already resident under this exact pc (e.g. queued twice
                # before the first compile's mark_compiled() landed) -- the
                # cache, not the coarse per-card bitmap, is the authority on
                # whether *this* pc specifically already has a trace.
                self.bitmap.mark_compiled(pc)
                continue
            trace = None
            if self.jit_compiler is not None:
                trace_block = self.resolve_trace_block(pc)
                trace = self.jit_compiler.compile_trace(pc, trace_block)

            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(pc)
                compiled_count += 1
            else:
                # Mark as COMPILED in bitmap so uncompilable / failed blocks do not thrash compile_queue
                self.bitmap.mark_compiled(pc)
        return compiled_count

    def drain_compile_queue(self) -> int:
        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def run(
        self,
        interp: Interpreter,
        func_index: int,
        args: list[int],
        quantum: int = 64,
        idle_budget: int = 4,
    ) -> list[int]:
        """
        Drives `interp` to completion. Before every basic block, checks the
                O(1) card bitmap first (`{ADR_TraceBoundaryYield}`) -- most blocks
                are never compiled, so this must reject them without ever touching
                the cache's per-bank search, or the miss penalty on the overwhelmingly
                common path would dwarf the win a hit gets. Only once the card reads
                COMPILED does this look the trace up and invoke it; otherwise it
                records the block head for hotspot tracking and falls back to
                `interp.step()` for that one block. Runs `idle_hook` every `quantum`
                blocks to batch-compile any HOT blocks queued since the last check.
                The interpreter itself never sees any of this -- it has no notion of
                a JIT cache, and this is the runtime's job alone.
        """
        if self.module is None and getattr(interp, "module", None) is not None:
            self.register_module_blocks(interp.module)
        # Driven via interp.run_iter() rather than a manual start()+step()
        # loop: `next(gen)` steps the interpreter for the boundary about to
        # run, while `gen.send(call_state)` tells it to skip stepping --
        # this block was already advanced externally, by _invoke_trace below.
        gen = interp.run_iter(func_index, args, quantum=1)
        call_state = next(gen)
        blocks_run = 0
        while not call_state.finished:
            pc = call_state.current_pc()
            if pc is not None and call_state.cont is not None:
                block_here = self.get_block(pc)
                frame_here = call_state.cont[1]
                # A JIT jump can resume at a pc whose enclosing BLOCK/LOOP/IF
                # frames were pushed onto frame.frames during an earlier
                # interp.step()-driven pass but never popped -- _invoke_trace's
                # computed jumps bypass _h_block/_h_loop/_h_if/_do_branch
                # entirely, so those stale frames can linger. frame.frames'
                # CONTENT is never trusted for branch-target resolution here
                # any more (see boundary_next_pc/loops_to below) -- this only
                # bounds its SIZE to this pc's statically-known nesting depth,
                # so it cannot grow without bound across a long JIT-heavy run.
                if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                    del frame_here.frames[block_here.frame_depth :]
                # This step's own resolved branch target(s), straight from
                # the BasicBlock this pc already denotes -- a pure function
                # of pc alone, so unlike frame.frames it can never desync
                # from reality. _h_br / _h_br_if / _h_else consult these
                # instead of frame.frames when set.
                frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
                frame_here.boundary_loops_to = (
                    block_here.loops_to if block_here is not None else None
                )
            trace = None
            is_compiled = pc is not None and self.bitmap.get_state(pc) == CardState.COMPILED
            if is_compiled:
                trace = self.cache.lookup(pc)
            if trace is not None:
                call_state = self._invoke_trace(interp, call_state, trace)
                call_state = gen.send(call_state)
                blocks_run += 1
            else:
                if pc is not None and not is_compiled:
                    self.record_block_head(pc)
                call_state = next(gen)
                blocks_run += 1
            if blocks_run % quantum == 0:
                self.idle_hook(budget=idle_budget)
        self.idle_hook(budget=idle_budget)
        return call_state.results

    def _invoke_trace(
        self, interp: Interpreter, call_state: InterpreterCall, trace: JITTrace
    ) -> InterpreterCall:
        """
        Executes one compiled native x64 JIT trace and advances `call_state`
        past it. Calls the trace directly on this frame's cached locals
        buffer rather than building a fresh `WASMContext` (ctypes array
        type + instance) per call -- the buffer's address is stable across
        every trace invoked against the same frame, so allocating it once
        and reusing it turns a per-call ctypes array construction into a
        per-call O(locals) value copy on the hot path
        (`{ADR_TraceBoundaryYield}`'s per-block dispatch). When the optional
        `native_trace_call` accelerator (jit/native_trace_call.pyx) is
        built, its raw C function pointer call replaces `trace.fn`'s
        `ctypes.CFUNCTYPE` libffi trampoline, which otherwise dominates this
        call's cost; the fallback keeps this correct on a plain-Python
        checkout. A trace's residual value is VM operand-stack state, not a
        C return value ({ExecutionContext_Layout}): `SPILL_RESULT_TO_STACK_BOT`
        writes it to `frame.jit_result_slot()`'s buffer (passed as `stack_bot`,
        R12) instead of returning it, and every trace always returns void.
        """
        ip, frame, locals_arr, tos = call_state.cont
        try:
            n_locals = self._n_locals_by_func[call_state.func_index]
        except IndexError:
            n_locals = max(len(locals_arr), 16)
        c_locals, locals_ptr = frame.jit_locals_buffer(n_locals)
        for i, v in enumerate(locals_arr):
            c_locals[i] = v
        c_result, result_ptr = frame.jit_result_slot()
        if _native_trace_call is not None and trace.raw_addr is not None:
            _native_trace_call.invoke_trace(
                trace.raw_addr, trace.head_pc, result_ptr.value, locals_ptr.value, 0
            )
        else:
            trace.fn(trace.head_pc, result_ptr, locals_ptr, 0)
        for i in range(len(locals_arr)):
            locals_arr[i] = c_locals[i] & 0xFFFF_FFFF
        res = c_result[0]

        if trace.loops_to is not None:
            # Terminator was BR_IF against a loop backedge: the trace's
            # residual value is the branch condition, consumed here -- it
            # never reaches the WASM operand stack. Mirrors
            # IntegratedHybridEngine._next_pc.
            cond = res if res is not None else 0
            next_unified = trace.loops_to if cond != 0 else trace.next_pc
        else:
            if trace.has_return_val and res is not None:
                frame.values.append(res & 0xFFFF_FFFF)
            next_unified = trace.next_pc

        # next_unified is None only for a JIT-compiled block whose
        # terminator is RETURN (or the rare malformed-tail case) -- the
        # function is ending, so signal "past the end of code" via O(1)
        # `len(frame.code)`, the exact sentinel `current_pc()` already
        # checks for. This never decodes an `Instr` at runtime to find this
        # out: {DirectBytecodeExecution} bans runtime instruction-object
        # generation, and the interpreter's own existing "frame just ended"
        # handling (step(), ip >= len(code))
        # is what must process the actual return-to-caller mechanics next.
        next_ip = (next_unified & 0xFFFF) if next_unified is not None else len(frame.code)
        new_tos = frame.values[-1] if frame.values else 0
        call_state.cont = (next_ip, frame, locals_arr, new_tos)
        return call_state


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution with direct ctypes backing."""

    def __init__(
        self,
        locals_values: list[int] | None = None,
        memory: bytearray | None = None,
        stack_capacity: int = 64,
    ):
        n_locals = max(len(locals_values or []), 16)
        self._c_locals = (ctypes.c_int64 * n_locals)()
        if locals_values:
            for i, v in enumerate(locals_values):
                self._c_locals[i] = v

        self._n_locals = n_locals
        self.stack_capacity = stack_capacity
        self.stack: StaticVector[int] = StaticVector(capacity=stack_capacity)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # A trace's residual value is VM operand-stack state, not a C return
        # value ({ExecutionContext_Layout}), so `SPILL_RESULT_TO_STACK_BOT`
        # writes it here (via R12, the CPS `stack_bot` argument) instead of
        # in the call's return value.
        self._c_result = ctypes.c_int64()

    @property
    def stack_bot_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(ctypes.pointer(self._c_result), ctypes.c_void_p)

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(self._c_locals, ctypes.c_void_p)

    @property
    def mem_ptr(self) -> ctypes.c_void_p:
        if self._c_mem is not None:
            return ctypes.c_void_p(ctypes.addressof(self._c_mem))
        return ctypes.c_void_p(0)

    class _LocalsView:
        def __init__(self, ctx: WASMContext):
            self._ctx = ctx

        def __getitem__(self, idx: int) -> int:
            return self._ctx._c_locals[idx] & 0xFFFF_FFFF

        def __setitem__(self, idx: int, val: int) -> None:
            self._ctx._c_locals[idx] = val & 0xFFFF_FFFF

        def __len__(self) -> int:
            return self._ctx._n_locals

        def __iter__(self) -> Iterator[int]:
            for i in range(self._ctx._n_locals):
                yield self._ctx._c_locals[i] & 0xFFFF_FFFF

    @property
    def locals(self) -> WASMContext._LocalsView:
        return self._LocalsView(self)

    @locals.setter
    def locals(self, values: list[int]) -> None:
        for i, v in enumerate(values):
            self._c_locals[i] = v & 0xFFFF_FFFF

    def push(self, val: int) -> None:
        if not self.stack.push_back(val & 0xFFFF_FFFF):
            raise RuntimeError("WASM execution stack overflow")

    def pop(self) -> int:
        val = self.stack.pop_back()
        if val is None:
            raise RuntimeError("WASM execution stack underflow")
        return val


def _emu_i32_const(stk: list[int], _arr: object, arg: object) -> None:
    stk.append(int(arg))  # type: ignore[arg-type]


def _emu_i32_add(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a + b) & 0xFFFF_FFFF)


def _emu_i32_sub(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a - b) & 0xFFFF_FFFF)


def _emu_i32_mul(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a * b) & 0xFFFF_FFFF)


def _emu_local_get(stk: list[int], arr: object, arg: object) -> None:
    stk.append((arr[arg] if arr else 0) & 0xFFFF_FFFF)  # type: ignore[index]


def _emu_local_set(stk: list[int], arr: object, arg: object) -> None:
    val = stk.pop() & 0xFFFF_FFFF
    if arr:
        arr[arg] = val  # type: ignore[index]


def _emu_local_tee(stk: list[int], arr: object, arg: object) -> None:
    val = stk[-1] & 0xFFFF_FFFF if stk else 0
    if arr:
        arr[arg] = val  # type: ignore[index]


_EMU_TRACE_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[list[int], object, object], None]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _emu_i32_const),
            (I32_ADD, _emu_i32_add),
            (I32_SUB, _emu_i32_sub),
            (I32_MUL, _emu_i32_mul),
            (LOCAL_GET, _emu_local_get),
            (LOCAL_SET, _emu_local_set),
            (LOCAL_TEE, _emu_local_tee),
        ]
    )
)
_EMU_TRACE_MAP: FlatMapView[int, Callable[[list[int], object, object], None]] = (
    _EMU_TRACE_STORAGE.view()
)


class WASMTraceCompiler:
    """Compiles a TraceBlock op stream into a fast callable native JITTrace using table dispatch."""

    def compile_trace(self, head_pc: int, block: TraceBlock) -> JITTrace:
        # `ops` outlives this call, captured by `trace_fn` below for every
        # future invocation of the returned JITTrace, so it needs a fixed
        # capacity: `block.byte_span` (each op is at least 1 byte, so it can
        # never hold more ops than that).
        ops: StaticVector[tuple[int, object]] = StaticVector(capacity=block.byte_span)
        for op, arg in block.ops:
            if not ops.push_back((op, arg)):
                raise ValueError(f"trace at {head_pc:#x}: op count exceeds byte_span capacity")
        has_ret = any(op in (I32_CONST, I32_ADD, I32_SUB, I32_MUL) for op, _ in ops)

        def trace_fn(ip: int, stack_bot: object, local_base: object, tos: int) -> int:
            # Emulated handler matching CPS 4-argument C signature (ip, stack_bot, local_base, tos).
            # A trace's residual value is VM operand-stack state, not a C return
            # value ({ExecutionContext_Layout}): written to `stack_bot` (mirroring
            # x64_jit.py's SPILL_RESULT_TO_STACK_BOT) instead of returned.
            c_arr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_int64)) if local_base else None
            stk: list[int] = [tos] if tos else []
            for op, arg in ops:
                handler = _EMU_TRACE_MAP.find(op)
                if handler is not None:
                    handler(stk, c_arr, arg)
            if stk and stack_bot:
                ctypes.cast(stack_bot, ctypes.POINTER(ctypes.c_int64))[0] = stk[-1]
            return 0

        c_fn = ctypes.CFUNCTYPE(
            ctypes.c_int64,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(trace_fn)
        trace = JITTrace(
            head_pc=head_pc,
            fn=c_fn,
            size_bytes=len(ops) * 4,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=has_ret,
        )
        trace._keepalive = c_fn
        return trace


def _interp_i32_const(ctx: WASMContext, arg: object) -> None:
    ctx.push(int(arg))  # type: ignore[arg-type]


def _interp_i32_add(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a + b) & 0xFFFF_FFFF)


def _interp_i32_sub(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a - b) & 0xFFFF_FFFF)


def _interp_i32_mul(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a * b) & 0xFFFF_FFFF)


def _interp_local_get(ctx: WASMContext, arg: object) -> None:
    ctx.push(ctx.locals[arg])  # type: ignore[index]


def _interp_local_set(ctx: WASMContext, arg: object) -> None:
    ctx.locals[arg] = ctx.pop()  # type: ignore[index]


def _interp_local_tee(ctx: WASMContext, arg: object) -> None:
    val = ctx.stack[-1] & 0xFFFF_FFFF if ctx.stack else 0
    ctx.locals[arg] = val  # type: ignore[index]


_INTERP_BLOCK_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[WASMContext, object], None]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _interp_i32_const),
            (I32_ADD, _interp_i32_add),
            (I32_SUB, _interp_i32_sub),
            (I32_MUL, _interp_i32_mul),
            (LOCAL_GET, _interp_local_get),
            (LOCAL_SET, _interp_local_set),
            (LOCAL_TEE, _interp_local_tee),
        ]
    )
)
_INTERP_BLOCK_MAP: FlatMapView[int, Callable[[WASMContext, object], None]] = (
    _INTERP_BLOCK_STORAGE.view()
)


class IntegratedHybridEngine:
    """
    Full Tiered Runtime Engine: Interpreter execution -> 2-bit card tracking ->
        Cooperative Yield -> Idle-Hook Batch Compilation -> Trace Chaining -> JIT execution.
    """

    def __init__(
        self,
        yield_threshold: int = 4,
        card_shift: int = 2,
        compiler: object | None = None,
        min_trace_bytes: int | None = None,
        compile_queue_capacity: int = 4,
    ):
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.trackable = BlockCardMask(card_shift=card_shift)
        self.history = HistoryRing(capacity=32)
        self.cache = JITMultiBufferCache()
        self.compiler = compiler or WASMTraceCompiler()
        self.compile_queue_capacity = compile_queue_capacity
        # LIFO queue: on_yield below drops a promotion that doesn't fit
        # rather than growing past this -- the card stays HOT, so it's
        # simply retried on the next on_yield() once idle_hook() drains room.
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        # See RuntimeEngine.min_trace_bytes: a card's 2-bit state can only
        # ever describe one block, so a block shorter than one card's worth
        # of bytes is never touched/tracked here at all -- this guarantees
        # every tracked block's next sibling starts at least a full card
        # away, so no two tracked blocks can ever land on the same card.
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        self.blocks: list[tuple[int, BasicBlock]] = []  # Flat slot list instead of dynamic dict
        self.control_skip_storage: ReadOnlyRadixBinaryTreeStorage[int] | None = None
        self.control_skip_tree: RadixBinaryTreeView[int] | None = None
        self.interp_blocks = 0
        self.jit_traces = 0
        self.compilations = 0
        self.yields = 0
        # Handler table dispatch pointer ({DebuggerLabelTableSwitch})
        # Default is normal zero-overhead handler table.
        self.debugger: object | None = None
        self._dispatch = self._dispatch_normal
        self.cache.on_evict = lambda pcs: [self.bitmap.mark_evicted(pc) for pc in pcs]

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def register_module_blocks(self, module: Module) -> None:
        """Binds loader-owned basic blocks and control skip Radix tree from a parsed WASM Module."""
        if module.block_tree is None:
            module.build_basic_block_index()
        self.module = module
        self.control_skip_storage = module.control_skip_storage
        self.control_skip_tree = module.control_skip_tree
        self.cache.control_skip_tree = module.control_skip_tree
        self.blocks = [(b.head_pc, b) for b in module.blocks]
        self.trackable.clear()
        for b in module.blocks:
            if b.next_pc is not None and b.byte_span >= self.min_trace_bytes:
                self.trackable.mark(b.head_pc)

    @property
    def handler_table(self) -> str:
        return "debug" if self._dispatch == self._dispatch_debug else "normal"

    def attach_debugger(self, debugger: object) -> None:
        """Switches handler table pointer to debug dispatch with ZERO per-step overhead in normal mode ({DebuggerLabelTableSwitch})."""
        self.debugger = debugger
        self._dispatch = self._dispatch_debug

    def detach_debugger(self) -> None:
        """Restores handler table pointer to normal fast dispatch ({DebuggerLabelTableSwitch})."""
        self.debugger = None
        self._dispatch = self._dispatch_normal

    def flush_jit_cache(self) -> None:
        """Invalidates all JIT cache banks ({Debugger_Jit_Flush})."""
        self.cache.flush_all()

    def get_block(self, pc: int) -> BasicBlock | None:
        if self.module is not None:
            return self.module.get_block(pc)
        for b_pc, block in self.blocks:
            if b_pc == pc:
                return block
        return None

    def on_yield(self) -> None:
        """Promotes HOT cards in history ring to LIFO compile queue."""
        for pc in self.history.drain():
            if self.bitmap.get_state(pc) == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.push_back(pc)

    def idle_hook(self, budget: int = 4) -> int:
        """Drains compile queue in LIFO reverse order and chains resident successors."""
        compiled = 0
        while self.compile_queue and compiled < budget:
            head_pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(head_pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(head_pc) is not None:
                # Already resident under this exact pc (e.g. queued twice
                # before the first compile's mark_compiled() landed) -- the
                # cache, not the coarse per-card bitmap, is the authority on
                # whether *this* pc specifically already has a trace.
                self.bitmap.mark_compiled(head_pc)
                continue
            trace_block = self.resolve_trace_block(head_pc)
            if trace_block is None:
                continue
            trace = self.compiler.compile_trace(head_pc, trace_block)
            if trace is not None:
                self.cache.insert(trace)
                self.bitmap.mark_compiled(head_pc)
                self.compilations += 1
                compiled += 1
        return compiled

    def resolve_trace_block(self, pc: int, block: BasicBlock | None = None) -> TraceBlock | None:
        """
        Builds this compile/interpret call's transient `TraceBlock` from the
        persisted `BasicBlock`'s PC metadata plus the owning function's raw
        bytecode -- `BasicBlock` itself never stores the op stream (see
        `wasm_module.BasicBlock`). Decoded fresh every call, never cached:
        matches `interpreter.py`'s direct-bytecode dispatch, which redecodes
        LEB128 operands on every step rather than persisting `Instr` objects.
        `block`, when the caller already has it (`_interpret_block`'s hot
        dispatch path), skips this engine's uncached `get_block` -- unlike
        `RuntimeEngine.get_block`, this one has no `_fast_block_slots` cache,
        so re-deriving `block` from `pc` here would redo a full Radix tree
        search on every single non-JIT block dispatch.
        """
        if block is None:
            block = self.get_block(pc)
        if block is None or self.module is None:
            return None
        code = self.module.code_for(pc >> 16)
        ops = iter_block_ops(code, pc & 0xFFFF, block.byte_span)
        return TraceBlock(
            head_pc=pc,
            ops=ops,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            byte_span=block.byte_span,
        )

    def _interpret_block(self, block: BasicBlock, ctx: WASMContext) -> None:
        trace_block = self.resolve_trace_block(block.head_pc, block=block)
        if trace_block is None:
            return
        for op, arg in trace_block.ops:
            handler = _INTERP_BLOCK_MAP.find(op)
            if handler is not None:
                handler(ctx, arg)

    def _next_pc(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        if block.loops_to is not None:
            # Condition at TOS: if non-zero, loop back; else fallthrough
            cond = ctx.pop()
            target = block.loops_to if cond != 0 else block.next_pc
        else:
            target = block.next_pc
        if target is not None and self.control_skip_tree is not None:
            skipped = self.control_skip_tree.find(bswap32(target))
            if skipped is not None:
                return skipped
        return target

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Executes a single basic block strictly in Interpreter mode (for debugging / fallback)."""
        self._interpret_block(block, ctx)
        return self._next_pc(block, ctx)

    def _dispatch_normal(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Normal handler table: Pure zero-overhead execution (JIT or Fast Interpreter)."""
        # O(1) card check first: most blocks are never compiled, so this
        # must reject them without ever touching the cache's per-bank
        # search, or the miss penalty on the overwhelmingly common path
        # would dwarf the win a hit gets.
        trace = self.cache.lookup(pc) if self.bitmap.get_state(pc) == CardState.COMPILED else None
        if trace is not None:
            # Tier 3 JIT Trace Direct C-Call via ctypes
            self.jit_traces += 1
            trace.invoke(ctx)
            # Trace chaining or fallback to interpreter
            next_pc = (
                trace.chain_next if trace.chain_next is not None else self._next_pc(block, ctx)
            )
        else:
            # Tier 2 Interpreter Execution with 2-bit hotspot tracking.
            # Blocks shorter than min_trace_bytes are never tracked (see
            # __init__): compiling them would cost more than the
            # interpreter dispatch it replaces, and it keeps every tracked
            # block's card unambiguously single-owned. `trackable` is sized
            # by the block's own byte_span, never `next_pc - pc`: a backward
            # branch (a loop body's own `br` to its loop head) makes
            # `next_pc - pc` negative, which would wrongly disqualify a
            # compilable block.
            if self.trackable.is_marked(pc):
                self.bitmap.touch(pc)
                self.history.record(pc)
            self.interp_blocks += 1
            self._interpret_block(block, ctx)
            next_pc = self._next_pc(block, ctx)

        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.exec_counter = 0
            self.yields += 1
            self.on_yield()
        return next_pc

    def _dispatch_debug(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Debug handler table: JIT bypass, breakpoint check, PC sampling, dynamic assertion verification."""
        dbg = self.debugger
        if dbg is not None and dbg.has_breakpoint(pc):
            dbg.halted = True
            dbg.stop_signal = 5
            return pc
        if dbg is not None:
            dbg.sample_pc(pc)

        self.interp_blocks += 1
        self._interpret_block(block, ctx)
        if dbg is not None:
            dbg.verify_assertions(ctx.memory)

        next_pc = self._next_pc(block, ctx)
        if next_pc is not None and dbg is not None and dbg.has_breakpoint(next_pc):
            dbg.halted = True
            dbg.stop_signal = 5
        return next_pc

    def run_step(self, pc: int, ctx: WASMContext) -> int | None:
        """
        Executes a single basic block by directly calling the active handler table dispatcher.
                Zero overhead when debugger is detached ({DebuggerLabelTableSwitch}).
        """

        block = self.get_block(pc)
        if block is None:
            return None
        return self._dispatch(pc, block, ctx)
