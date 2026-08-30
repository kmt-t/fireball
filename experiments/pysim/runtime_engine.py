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


from system_containers import BitView, RingBuffer


class CardState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2          # Queued for compilation
    COMPILED = 3


class HotspotBitmap:
    """2-bit state per CARD (card = pc >> card_shift) backed by BitView<2>."""

    def __init__(self, card_shift: int = 6, max_cards: int = 4096):
        self.card_shift = card_shift
        self.max_cards = max_cards
        # 2 bits per card => 4 cards per byte
        self.storage = bytearray((max_cards + 3) // 4)
        self.view = BitView(self.storage, bits=2, origin=0, count=max_cards)

    def card_of(self, pc: int) -> int:
        return (pc >> self.card_shift) % self.max_cards

    def get_state(self, pc: int) -> int:
        return self.view.at(self.card_of(pc))

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""
        card = self.card_of(pc)
        s = self.view.at(card)
        if s == CardState.COMPILED:
            return s
        if s == CardState.UNEXECUTED:
            s = CardState.EXECUTED
        elif s == CardState.EXECUTED:
            s = CardState.HOT
        self.view.put(card, s)
        return s

    def mark_compiled(self, pc: int) -> None:
        self.view.put(self.card_of(pc), CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        """Evicted trace resets card state to EXECUTED (01)."""
        self.view.put(self.card_of(pc), CardState.EXECUTED)


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
        # Flat list of (head_pc, JITTrace) pairs instead of dynamic dict
        self.traces: list[tuple[int, JITTrace]] = []
        self.inbound_sources: list[int] = []

    def get_trace(self, head_pc: int) -> JITTrace | None:
        for pc, trace in self.traces:
            if pc == head_pc:
                return trace
        return None

    def has_trace(self, head_pc: int) -> bool:
        return self.get_trace(head_pc) is not None

    def remove_trace(self, head_pc: int) -> JITTrace | None:
        for i, (pc, trace) in enumerate(self.traces):
            if pc == head_pc:
                self.traces.pop(i)
                return trace
        return None

    def clear(self) -> list[int]:
        purged = [pc for pc, _ in self.traces]
        self.traces.clear()
        self.inbound_sources.clear()
        self.used_bytes = 0
        return purged

    def allocate(self, trace: JITTrace) -> bool:
        prev = self.get_trace(trace.head_pc)
        delta = trace.size_bytes - (prev.size_bytes if prev else 0)
        if self.used_bytes + delta > self.capacity_bytes:
            return False
        if prev is not None:
            self.remove_trace(trace.head_pc)
        self.traces.append((trace.head_pc, trace))
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
            target_bank.inbound_sources.append(source_pc)

    def lookup(self, head_pc: int) -> JITTrace | None:
        trace = self.active.get_trace(head_pc)
        if trace is not None:
            return trace

        trace = self.warm.get_trace(head_pc)
        if trace is not None:
            return trace

        trace = self.oldest.get_trace(head_pc)
        if trace is None:
            return None

        # Oldest bank hit: promote to Active bank immediately
        self.oldest.remove_trace(head_pc)
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

    def idle_hook(self, budget: int = 4) -> int:
        """Drains the LIFO compile queue during COOS idle_hook. {JIT_ReverseCompilationOrder}
        Compiling in reverse order increases immediate chaining probability."""
        compiled_count = 0
        while self.compile_queue and compiled_count < budget:
            pc = self.compile_queue.pop()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue
            if self.jit_compiler:
                trace = self.jit_compiler(pc)
                if self.cache.insert(trace):
                    self.bitmap.mark_compiled(pc)
                    compiled_count += 1
        return compiled_count

    def drain_compile_queue(self) -> int:
        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def run_wasm_coroutine(self, interp: Any, func_index: int, args: list[int], yield_every: int = 64):
        """Runs a WASM function as a cooperative coroutine on COOS.
        Yields every `yield_every` instructions, draining history ring to compile queue on each yield."""
        gen = interp.call_coroutine(func_index, args, yield_every=yield_every)
        try:
            while True:
                next(gen)
                self.on_yield()
                yield  # Cooperative yield to scheduler
        except StopIteration as e:
            self.on_yield()
            return e.value or []
