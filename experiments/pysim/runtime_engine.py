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

from typing import Any, Callable


class CardState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2          # Queued for compilation
    COMPILED = 3


class HotspotBitmap:
    """2-bit state per CARD (card = pc >> card_shift)."""

    def __init__(self, card_shift: int = 6):
        self.card_shift = card_shift
        self.state: dict[int, int] = {}

    def card_of(self, pc: int) -> int:
        return pc >> self.card_shift

    def get_state(self, pc: int) -> int:
        return self.state.get(self.card_of(pc), CardState.UNEXECUTED)

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""
        card = self.card_of(pc)
        state = self.state.get(card, CardState.UNEXECUTED)
        if state == CardState.COMPILED:
            return state
        if state == CardState.UNEXECUTED:
            state = CardState.EXECUTED
        elif state == CardState.EXECUTED:
            state = CardState.HOT
        self.state[card] = state
        return state

    def mark_compiled(self, pc: int) -> None:
        self.state[self.card_of(pc)] = CardState.COMPILED

    def mark_evicted(self, pc: int) -> None:
        """Evicted trace resets card state to EXECUTED (01)."""
        self.state[self.card_of(pc)] = CardState.EXECUTED


class HistoryRing:
    """Fixed-size ring of recently executed basic-block head PCs."""

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self.buf: list[int] = []
        self.dropped = 0

    def record(self, pc: int) -> None:
        if len(self.buf) >= self.capacity:
            self.buf.pop(0)
            self.dropped += 1
        self.buf.append(pc)

    def drain(self) -> list[int]:
        out = self.buf
        self.buf = []
        return out


class JITTrace:
    """Compiled native trace descriptor."""

    def __init__(self, head_pc: int, native_fn: Callable[..., Any], size_bytes: int = 64):
        self.head_pc = head_pc
        self.native_fn = native_fn
        self.size_bytes = size_bytes
        self.chain_next: int | None = None


class JITCacheBank:
    def __init__(self, bank_id: int, capacity_bytes: int = 2048):
        self.bank_id = bank_id
        self.capacity_bytes = capacity_bytes
        self.used_bytes = 0
        self.traces: dict[int, JITTrace] = {}
        self.inbound_sources: set[int] = set()

    def clear(self) -> list[int]:
        purged = list(self.traces.keys())
        self.traces.clear()
        self.inbound_sources.clear()
        self.used_bytes = 0
        return purged

    def allocate(self, trace: JITTrace) -> bool:
        prev = self.traces.get(trace.head_pc)
        delta = trace.size_bytes - (prev.size_bytes if prev else 0)
        if self.used_bytes + delta > self.capacity_bytes:
            return False
        self.traces[trace.head_pc] = trace
        self.used_bytes += delta
        return True


class JITMultiBufferCache:
    """3-bank rotating JIT code cache: Active / Warm / Oldest."""

    def __init__(self, bank_capacity: int = 2048):
        self.banks = [JITCacheBank(i, bank_capacity) for i in range(3)]
        self.active_idx, self.warm_idx, self.oldest_idx = 0, 1, 2
        self.promotions = 0
        self.evictions = 0
        self.on_evict: Callable[[list[int]], None] | None = None

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
            if head_pc in bank.traces:
                return bank
        return None

    def find_trace(self, head_pc: int) -> JITTrace | None:
        for bank in self.banks:
            if head_pc in bank.traces:
                return bank.traces[head_pc]
        return None

    def register_chain(self, source_pc: int, target_pc: int) -> None:
        target_bank = self.find_bank(target_pc)
        if target_bank is not None:
            target_bank.inbound_sources.add(source_pc)

    def lookup(self, head_pc: int) -> JITTrace | None:
        if head_pc in self.active.traces:
            return self.active.traces[head_pc]

        if head_pc in self.warm.traces:
            return self.warm.traces[head_pc]

        trace = self.oldest.traces.get(head_pc)
        if trace is None:
            return None

        # Oldest bank hit: promote to Active bank immediately
        del self.oldest.traces[head_pc]
        self.oldest.used_bytes -= trace.size_bytes
        if not self.active.allocate(trace):
            self.rotate()
            self.active.allocate(trace)
        self.promotions += 1
        return trace

    def insert(self, trace: JITTrace) -> bool:
        if not self.active.allocate(trace):
            self.rotate()
            if not self.active.allocate(trace):
                return False
        return True

    def rotate(self) -> list[int]:
        """Rotates Active -> Warm -> Oldest -> Active and purges the old Oldest bank."""
        new_active = self.oldest_idx
        new_warm = self.active_idx
        new_oldest = self.warm_idx

        # Invalidate inbound chains into the old Oldest bank before purging
        purged_pcs = self.banks[new_active].clear()
        self.evictions += len(purged_pcs)

        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest

        if self.on_evict and purged_pcs:
            self.on_evict(purged_pcs)

        return purged_pcs


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    def __init__(self, jit_compiler: Callable[[int], JITTrace] | None = None, yield_threshold: int = 16):
        self.bitmap = HotspotBitmap()
        self.ring = HistoryRing()
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.jit_compiler = jit_compiler
        self.compile_queue: list[int] = []   # LIFO queue
        self.yield_threshold = yield_threshold
        self.exec_counter = 0

    def _handle_eviction(self, purged_pcs: list[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def record_block_head(self, pc: int) -> None:
        """Called at each basic-block head by the interpreter."""
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
                self.compile_queue.append(pc)

    def drain_compile_queue(self) -> int:
        """Drains LIFO compile queue and compiles traces into Active JIT cache."""
        compiled_count = 0
        while self.compile_queue:
            pc = self.compile_queue.pop()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue
            if self.jit_compiler:
                trace = self.jit_compiler(pc)
                if self.cache.insert(trace):
                    self.bitmap.mark_compiled(pc)
                    compiled_count += 1
        return compiled_count
