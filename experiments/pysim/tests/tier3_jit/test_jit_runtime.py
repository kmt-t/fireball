from __future__ import annotations

"""
Unit tests for Tier 3 JIT: JIT Hotspot Profiling & 3-Bank Cache
Traceability: jit_compiler_test_spec.md, jit_runtime_test_spec.md
"""

import struct
import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import wasmtime
from interpreter import Interpreter
from runtime_engine import (
    CardState,
    HistoryRing,
    HotspotBitmap,
    JITCacheBank,
    JITMultiBufferCache,
    JITTrace,
    JITTraceHeader,
    RuntimeEngine,
)
from system_containers import (
    RadixBinaryTreeView,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    bswap32,
    build_radix_table,
)
from test_support import PcOnlyCompiler
from wasm_reader import parse
from x64_jit import TraceCompiler


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_hotspot_01_2bit_card_marking_state_transitions():
    """HOTSPOT-01 / JITR-02: 2-bit state machine: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)."""
    bitmap = HotspotBitmap(card_shift=4)
    pc = 0x100
    assert bitmap.get_state(pc) == CardState.UNEXECUTED
    # First touch: UNEXECUTED -> EXECUTED
    assert bitmap.touch(pc) == CardState.EXECUTED
    assert bitmap.get_state(pc) == CardState.EXECUTED
    # Second touch: EXECUTED -> HOT
    assert bitmap.touch(pc) == CardState.HOT
    assert bitmap.get_state(pc) == CardState.HOT
    # Mark COMPILED
    bitmap.mark_compiled(pc)
    assert bitmap.get_state(pc) == CardState.COMPILED
    assert bitmap.touch(pc) == CardState.COMPILED  # JITR-03: COMPILED touch remains COMPILED


def test_jitr_01_card_marking_granularity():
    """JITR-01: Card marking granularity is 64-byte card, not individual instruction."""
    bitmap = HotspotBitmap(card_shift=6)  # 64-byte cards
    pc1 = 0x1000
    pc2 = 0x1020  # Same 64-byte card (0x1000..0x103F)
    assert bitmap.get_state(pc1) == CardState.UNEXECUTED
    assert bitmap.get_state(pc2) == CardState.UNEXECUTED
    bitmap.touch(pc1)
    # pc2 reflects the state change because both share the same card
    assert bitmap.get_state(pc2) == CardState.EXECUTED


def test_hotspot_02_history_ring_buffered_yield_drain():
    """HOTSPOT-02 / JITR-05: Interpreter records basic-block heads to HistoryRing, drained on yield."""
    ring = HistoryRing(capacity=8)
    for i in range(10):
        ring.record(0x1000 + i * 4)

    assert ring.dropped == 2
    drained = ring.drain()
    assert len(drained) == 8
    assert len(ring.drain()) == 0


def test_hotspot_03_lifo_compile_queue_batch_drain():
    """HOTSPOT-03 / JITR-12: HOT traces are queued to LIFO compile queue and batch-compiled into Active bank."""
    compiled_traces = []

    def dummy_compiler(pc: int) -> JITTrace:
        t = JITTrace(head_pc=pc, native_fn=lambda: pc * 2, size_bytes=64)
        compiled_traces.append(pc)
        return t

    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(dummy_compiler))
    engine.compile_queue = StaticVector.of(
        [0x100, 0x200, 0x300], capacity=engine.compile_queue_capacity
    )
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_traces == [0x300, 0x200], (
        "LIFO compilation order required {JIT_ReverseCompilationOrder}"
    )
    assert engine.cache.active.has_trace(0x300)
    assert engine.cache.active.has_trace(0x200)
    assert not engine.cache.active.has_trace(0x100)


def test_hotspot_04_3bank_cache_oldest_only_promotion():
    """HOTSPOT-04 / JITR-22, 23: 3-bank cache: Warm hit never promotes; Oldest hit promotes to Active."""
    cache = JITMultiBufferCache(bank_capacity=512)
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64)
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t1)  # In Active
    cache.rotate()  # t1 moved to Warm
    cache.insert(t2)  # t2 in Active
    # Warm hit on t1: zero promotion overhead {JIT_OldestOnly_Promote}
    assert cache.lookup(0x100) is t1
    assert cache.promotions == 0
    assert cache.warm.has_trace(0x100)
    cache.rotate()  # t1 moved to Oldest, t2 moved to Warm
    assert cache.oldest.has_trace(0x100)
    # Oldest hit on t1: must promote to Active
    promoted = cache.lookup(0x100)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.active.has_trace(0x100)
    assert not cache.oldest.has_trace(0x100)
    assert (t1.flags & JITTraceHeader.FLAG_PROMOTED) != 0


def test_jitr_cache_bank_traces_always_sorted_by_head_pc():
    """
    JITCacheBank.traces backs jit_runtime.md §3.3's JitEntryIndex (a
    flat_map_view over a sorted array): insertion order must never leak
    into iteration order, or the O(log n) binary-search claim over it
    would be false. Removal tombstones the slot in place rather than
    shifting the array; a later re-insert of that same key must reuse the
    tombstoned slot (not append a second entry) and the trace it returns
    must be the new one, not the tombstoned original.
    """
    bank = JITCacheBank(0, capacity_bytes=2048)
    for pc in (0x300, 0x100, 0x500, 0x200, 0x400):
        bank.allocate(JITTrace(head_pc=pc, native_fn=lambda: 0, size_bytes=64))
    assert [pc for pc, _ in bank.traces] == [0x100, 0x200, 0x300, 0x400, 0x500]
    bank.remove_trace(0x300)
    assert [pc for pc, _ in bank.traces] == [0x100, 0x200, 0x400, 0x500]
    assert bank.get_trace(0x300) is None
    replacement = JITTrace(head_pc=0x300, native_fn=lambda: 1, size_bytes=64)
    bank.allocate(replacement)
    assert [pc for pc, _ in bank.traces] == [0x100, 0x200, 0x300, 0x400, 0x500]
    assert bank.get_trace(0x300) is replacement, "re-insert must reuse the tombstoned slot"


def test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain():
    """
    Promoting a trace out of Oldest must carry its inbound chain-source
    registrations to wherever it lands. Without this, a later rotate()
    looks for them on the bank the trace used to live in -- which no
    longer holds it -- and never unlinks a source chained into it once the
    trace is genuinely purged from its new bank, leaving a dangling
    `chain_next`.
    """
    cache = JITMultiBufferCache(bank_capacity=512)
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 -> Active
    cache.rotate()  # t2's bank -> Warm
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 -> new Active, chains into Warm-resident t2
    assert t1.chain_next == 0x200
    old_bank = cache.find_bank(0x200)
    assert 0x100 in old_bank.inbound_sources

    cache.rotate()  # t2's bank -> Oldest
    promoted = cache.lookup(0x200)  # promote t2 out of Oldest
    assert promoted is t2
    new_bank = cache.find_bank(0x200)
    assert new_bank is not old_bank
    assert 0x100 not in old_bank.inbound_sources, (
        "stale registration must not remain on the bank the trace left"
    )
    assert 0x100 in new_bank.inbound_sources, "the inbound source must follow the promoted trace"


def test_jitr_bitmap_checked_before_cache_lookup():
    """
    RuntimeEngine.run() must check the O(1) card bitmap before ever calling
    cache.lookup(): most blocks are never compiled, so a miss must be
    rejected in O(1) without touching the cache's per-bank search, or the
    miss penalty on the overwhelmingly common path would dwarf the win a
    hit gets.
    """
    wat = """
    (module
      (func (export "sum_to") (param $n i32) (result i32)
        (local $i i32) (local $acc i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $acc (i32.add (local.get $acc) (local.get $i)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (local.get $acc)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, skipping test_jitr_bitmap_checked_before_cache_lookup"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("sum_to")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    lookup_calls = []
    real_lookup = engine.cache.lookup

    def spy(pc):
        lookup_calls.append((pc, engine.bitmap.get_state(pc)))
        return real_lookup(pc)

    engine.cache.lookup = spy
    engine.run(interp, fn_idx, [50])

    assert lookup_calls, (
        "the loop must have gotten hot enough to compile and hit the cache at least once"
    )
    for pc, state in lookup_calls:
        assert state == CardState.COMPILED, (
            f"cache.lookup({pc:#x}) was called while its card was {state}, not COMPILED -- "
            "the bitmap must be checked first so a miss never reaches the cache search"
        )


def test_jitr_31_to_35_trace_chaining_and_ok_unlinking():
    """JITR-31..35: Direct chaining into resident Active/Warm successors and O(k) unlinking on Oldest purge."""
    cache = JITMultiBufferCache(bank_capacity=512)
    # t1 falls through to t2
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 in Active
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 chains directly into resident t2 (Active)
    assert t1.chain_next == 0x200
    # Rotate 1: t1, t2 -> Warm
    cache.rotate()
    assert cache.warm.has_trace(0x100)
    assert t1.chain_next == 0x200  # Preserved
    # Rotate 2: t1, t2 -> Oldest
    cache.rotate()
    assert cache.oldest.has_trace(0x100)
    assert t1.chain_next == 0x200  # Preserved in Oldest
    # Rotate 3: Oldest is purged. O(k) unlinking resets chain_next of source pointing to purged targets
    cache.rotate()
    assert not cache.oldest.has_trace(0x200)


def test_jitc_20_trace_header_16byte_physical_layout():
    """JITC-20: Trace header is strictly 16 bytes: u32 pc, u16 size, u8 flags, u8 variant, u32 next, u32 target."""
    hdr = JITTraceHeader(head_wasm_pc=0x12345678, trace_byte_size=128, flags=0x01, variant_id=0x02)
    hdr.chain_next_pc = 0x87654321
    hdr.chain_target_addr = 0x20001000
    raw = hdr.pack()
    assert len(raw) == 16

    pc, size, flags, var, next_pc, target = struct.unpack("<IHBBII", raw)
    assert pc == 0x12345678
    assert size == 128
    assert flags == 0x01
    assert var == 0x02
    assert next_pc == 0x87654321
    assert target == 0x20001000


def test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card():
    """HOTSPOT-05: Oldest bank eviction unlinks inbound sources and resets card state to UNEXECUTED."""
    bitmap = HotspotBitmap()
    cache = JITMultiBufferCache(bank_capacity=256)
    cache.on_evict = lambda pcs: [bitmap.mark_evicted(p) for p in pcs]
    t_evict = JITTrace(0x50, lambda: 50, size_bytes=64)
    cache.insert(t_evict)
    bitmap.mark_compiled(0x50)
    assert bitmap.get_state(0x50) == CardState.COMPILED
    # Rotate 3 times without lookup -> evicted from Oldest
    cache.rotate()
    cache.rotate()
    cache.rotate()
    assert bitmap.get_state(0x50) == CardState.UNEXECUTED, (
        "Evicted trace must revert card state to UNEXECUTED (00), forcing a full "
        "re-warm-up rather than jumping straight back to HOT after one touch"
    )


def test_hotspot_06_short_blocks_never_tracked_avoiding_card_aliasing():
    """
    HOTSPOT-06: a card's 2-bit state can only ever describe one block. Two
    distinct block heads sharing a card would otherwise let compiling one
    falsely read back as "already compiled" for the other, or let evicting
    one falsely reset the other's still-resident COMPILED state. Blocks
    shorter than one card's worth of bytes must never be recorded at all,
    so two tracked blocks can never land on the same card.
    """
    wat = """
    (module
      (func (export "f")
        (block (i32.const 1) (drop) (br 0))
        (block (i32.const 2) (drop) (br 0))
      )
    )
    """
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(lambda pc: None), card_shift=3)
    assert engine.min_trace_bytes == 8
    mod = engine.load_wasm(wasm_bytes)
    h0 = mod.blocks[0].head_pc
    h1 = mod.blocks[1].head_pc
    for _ in range(engine.yield_threshold * 2):
        engine.record_block_head(h0)
        engine.record_block_head(h1)
    assert engine.bitmap.get_state(h0) == CardState.UNEXECUTED
    assert engine.bitmap.get_state(h1) == CardState.UNEXECUTED
    assert engine.compile_queue == [], "short blocks must never reach the compile queue"


def test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace():
    """
    HOTSPOT-07: if a pc is queued for compilation while a trace already
    resides in the cache under that exact pc (e.g. re-queued before an
    earlier compile's mark_compiled() landed), idle_hook must trust the
    cache -- the authority on whether *this* pc has a trace -- over the
    coarse per-card bitmap, and skip recompiling it.
    """
    compile_calls = []

    def fake_compile(pc):
        compile_calls.append(pc)
        return JITTrace(pc, lambda: 0, size_bytes=64)

    wat = '(module (func (export "f") i32.const 1 drop i32.const 2 drop return))'
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(fake_compile), card_shift=3)
    mod = engine.load_wasm(wasm_bytes)
    pc = mod.blocks[0].head_pc
    engine.cache.insert(JITTrace(pc, lambda: 0, size_bytes=64))
    engine.compile_queue.push_back(pc)

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert engine.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_compile_queue_overflow_compiles_on_the_spot():
    """JITR: When compile_queue reaches capacity, all queued traces are compiled on the spot."""
    compile_calls = []

    def fake_compile(pc):
        compile_calls.append(pc)
        return JITTrace(pc, lambda: 0, size_bytes=64)

    wat = """
    (module
      (func (export "f0") i32.const 1 drop i32.const 2 drop return)
      (func (export "f1") i32.const 1 drop i32.const 2 drop return)
      (func (export "f2") i32.const 1 drop i32.const 2 drop return)
    )
    """
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    engine = RuntimeEngine(
        jit_compiler=PcOnlyCompiler(fake_compile),
        card_shift=3,
        compile_queue_capacity=3,
    )
    mod = engine.load_wasm(wasm_bytes)
    pcs = [b.head_pc for b in mod.blocks]
    for pc in pcs:
        engine.bitmap.touch(pc)
        engine.bitmap.touch(pc)
        assert engine.bitmap.get_state(pc) == CardState.HOT

    for pc in pcs:
        engine.ring.record(pc)
    engine.on_yield()

    assert len(compile_calls) == 3
    assert len(engine.compile_queue) == 0
    for pc in pcs:
        assert engine.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_control_skip_radix_tree_chaining():
    """JITR: Loader creates a RadixBinaryTreeView with bswap32 keys mapping delimiter PCs
    to fallthrough basic block head PCs, and JIT chaining successfully resolves successors."""
    delim_pcs = [0x110, 0x120]
    fallthrough_pcs = [0x114, 0x124]

    # Stored keys must have byte-order inverted via bswap32
    inv_keys = [bswap32(k) for k in delim_pcs]
    sorted_pairs = sorted(zip(inv_keys, fallthrough_pcs, strict=False), key=lambda p: p[0])
    keys = [p[0] for p in sorted_pairs]
    vals = [p[1] for p in sorted_pairs]

    radix_shift = 28
    table = build_radix_table(keys, radix_shift=radix_shift)
    skip_tree = RadixBinaryTreeView(
        keys=keys, values=vals, radix_table=table, radix_shift=radix_shift
    )

    cache = JITMultiBufferCache(bank_capacity=256)
    cache.control_skip_tree = skip_tree

    trace_c = JITTrace(head_pc=0x124, fn=lambda: 0, size_bytes=64, next_pc=None)
    trace_b = JITTrace(head_pc=0x114, fn=lambda: 0, size_bytes=64, next_pc=0x120)
    trace_a = JITTrace(head_pc=0x100, fn=lambda: 0, size_bytes=64, next_pc=0x110)

    # Reverse order insertion (LIFO queue style): C, then B, then A
    assert cache.insert(trace_c)
    assert cache.insert(trace_b)
    assert trace_b.chain_next == 0x124

    assert cache.insert(trace_a)
    assert trace_a.chain_next == 0x114

    # Test forward chaining: A inserted first, then B
    cache2 = JITMultiBufferCache(bank_capacity=256)
    cache2.control_skip_tree = skip_tree
    trace_a2 = JITTrace(head_pc=0x100, fn=lambda: 0, size_bytes=64, next_pc=0x110)
    trace_b2 = JITTrace(head_pc=0x114, fn=lambda: 0, size_bytes=64, next_pc=0x120)

    assert cache2.insert(trace_a2)
    assert trace_a2.chain_next is None
    assert cache2.insert(trace_b2)
    assert trace_a2.chain_next == 0x114


def test_jitr_26_direct_mapped_folding_xor_jit_cache():
    """JITR-26 & JITR-GOTCHA-05: Direct-Mapped Folding XOR JIT Cache[16] O(1) hit and rotation invalidation."""
    cache = JITMultiBufferCache(bank_capacity=1024)
    # PC with function index 1, offset 0x20 -> (1 << 16) | 0x20 = 0x00010020
    pc1 = 0x00010020
    pc2 = 0x00020020
    t1 = JITTrace(head_pc=pc1, native_fn=lambda: 10, size_bytes=64)
    t2 = JITTrace(head_pc=pc2, native_fn=lambda: 20, size_bytes=64)

    # 1. Verify hash slot uniformly folds all 4 bytes
    h1 = cache._hash_slot(pc1)
    h2 = cache._hash_slot(pc2)
    expected_h1 = ((pc1 >> 24) ^ (pc1 >> 16) ^ (pc1 >> 8) ^ pc1) & 0x0F
    assert h1 == expected_h1
    assert h1 != h2, "Different function index should produce distinct hash slot"

    # 2. Insert populates fast slot
    cache.insert(t1)
    assert cache._fast_slots[h1] == (pc1, t1)

    # 3. Lookup hits fast slot
    assert cache.lookup(pc1) is t1

    # 4. Rotation invalidates fast slot (JITR-GOTCHA-05)
    cache.rotate()  # t1 moves to Warm
    for slot in cache._fast_slots:
        assert slot is None, (
            "All fast slots must be cleared on rotate to prevent dangling old bank references"
        )

    # 5. Lookup refills fast slot from Warm (without promotion)
    assert cache.lookup(pc1) is t1
    assert cache.promotions == 0
    assert cache._fast_slots[h1] == (pc1, t1)

    # 6. Rotate again: t1 moves to Oldest
    cache.rotate()
    for slot in cache._fast_slots:
        assert slot is None

    # 7. Lookup from Oldest: must trigger promotion to Active and update fast slot
    promoted = cache.lookup(pc1)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.active.has_trace(pc1)
    assert cache._fast_slots[h1] == (pc1, t1)

    # 8. Flush all clears fast slots
    cache.flush_all()
    for slot in cache._fast_slots:
        assert slot is None


def test_jitr_block_capacity_from_wasm_loader_and_no_set():
    """JITR: RuntimeEngine takes block capacity from WASM loader and strictly forbids set."""
    from wasm_reader import parse

    wat = "(module (func (i32.const 42) (return)) (func (i32.const 99) (return)))"
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        return
    mod = parse(wasm_bytes)

    # 1. WASM loader provides total_basic_blocks metadata and owns block_storage & block_tree
    assert mod.total_basic_blocks == 2
    assert mod.block_storage is not None
    assert len(mod.block_storage.keys) == mod.total_basic_blocks
    assert isinstance(mod.block_storage, ReadOnlyRadixBinaryTreeStorage)
    assert mod.block_tree is not None
    assert len(mod.blocks) == 2

    # 2. RuntimeEngine binds loader-owned blocks and resolves them seamlessly
    engine = RuntimeEngine()
    engine.register_module_blocks(mod)
    first_block = mod.blocks[0]
    assert engine.get_block(first_block.head_pc) is first_block

    # 3. Strictly verify no python set is used anywhere in engine
    for attr, val in engine.__dict__.items():
        assert not isinstance(val, set), (
            f"Attribute {attr} must not be a set! Use system containers."
        )


# ===========================================================================
# 7. Tier 2 vMMIO: 3-Tier Gate & FC=14 SHM Ownership (runtime_vmmio_test_spec.md)
# ===========================================================================


# ===========================================================================
# 8. RuntimeEngine._invoke_trace: branch/skip resolution and interpreter
#    hand-off correctness -- covers the compiled-trace <-> interpreter
#    boundary that `run_step`/`IntegratedHybridEngine` tests never exercise,
#    since RuntimeEngine.run() is a separate driver with its own
#    _invoke_trace (see jit_runtime.md's tiered execution loop).
# ===========================================================================


def test_jitr_br_if_loop_exit_jit_result_correct():
    """
    JITR-40: once RuntimeEngine._invoke_trace compiles the loop's br_if
    exit-condition block, the native trace's boolean result must still
    decide between looping (trace.next_pc) and exiting (trace.loops_to) --
    a regression guard for a `_invoke_trace` that always took next_pc and
    discarded the condition, which never terminates the loop.
    """
    wat = """
    (module
      (func (export "sum_to") (param $n i32) (result i32)
        (local $i i32) (local $acc i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $acc (i32.add (local.get $acc) (local.get $i)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (local.get $acc)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, skipping test_jitr_br_if_loop_exit_jit_result_correct"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("sum_to")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 50
    results = engine.run(interp, fn_idx, [n])
    assert results == [sum(range(n))], (
        f"sum_to({n}) via JIT-driven RuntimeEngine.run() = {results}, "
        f"expected [{sum(range(n))}] -- the compiled loop-exit trace's "
        "condition must gate the branch, not be discarded"
    )
    assert len(engine.cache.active.traces) > 0, (
        "the loop must have actually gotten hot enough to compile"
    )


def test_jitr_backward_branch_block_byte_span_not_disqualified():
    """
    JITR-41 / JITR-GOTCHA-07: the loop body block (sum/increment, ending in
    an unconditional `br` back to the loop's own condition-check block) has
    `next_pc < head_pc` -- a regression guard for `record_block_head` sizing
    this block via `next_pc - pc` (negative for any backward branch), which
    reads as "shorter than min_trace_bytes" and permanently disqualifies the
    function's hottest block from ever compiling. Both of the loop's blocks
    must end up compiled, not just the forward-only condition check.
    """
    wat = """
    (module
      (func (export "sum_to") (param $n i32) (result i32)
        (local $i i32) (local $acc i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $acc (i32.add (local.get $acc) (local.get $i)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (local.get $acc)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, "
            "skipping test_jitr_backward_branch_block_byte_span_not_disqualified"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("sum_to")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 50
    results = engine.run(interp, fn_idx, [n])
    assert results == [sum(range(n))]
    compiled_heads = {pc for pc, _ in engine.cache.active.traces}
    assert len(compiled_heads) >= 2, (
        f"only {len(compiled_heads)} block(s) compiled ({[hex(pc) for pc in compiled_heads]}) -- "
        "the backward-branching loop body must compile too, not just the forward condition check"
    )


def test_jitr_if_then_skipped_when_condition_false_after_jit():
    """
    JITR-42: once the `if (cond) (then ...)` condition-check block compiles,
    the then-body must run only when the native trace's condition is true --
    a regression guard for a `_invoke_trace` that treated IF exactly like an
    unconditional fallthrough, always executing the then-body regardless of
    the computed condition.
    """
    wat = """
    (module
      (func (export "abs_sum") (param $n i32) (result i32)
        (local $i i32) (local $x i32) (local $acc i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $x (i32.sub (local.get $i) (i32.const 5)))
            (if (i32.lt_s (local.get $x) (i32.const 0))
              (then (local.set $x (i32.sub (i32.const 0) (local.get $x))))
            )
            (local.set $acc (i32.add (local.get $acc) (local.get $x)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (local.get $acc)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, "
            "skipping test_jitr_if_then_skipped_when_condition_false_after_jit"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("abs_sum")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=4)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.run(interp, fn_idx, [n])
    expected = sum(abs(i - 5) for i in range(n))
    assert results == [expected], (
        f"abs_sum({n}) via JIT-driven RuntimeEngine.run() = {results}, expected [{expected}] -- "
        "an unconditionally-taken then-body (or an unconditionally-skipped one) throws this off"
    )
    assert len(engine.cache.active.traces) > 0, (
        "the if-condition-check block must have gotten hot enough to compile"
    )


def test_jitr_nested_loop_in_if_frame_stack_reconciliation():
    """
    JITR-42: a loop nested inside an if nested inside an outer loop --
    RuntimeEngine._invoke_trace's computed jumps bypass _h_block/_h_loop/
    _h_if entirely, so once the inner loop's exit-condition block compiles,
    the frame.frames pushed for it during any earlier cold (interpreted)
    pass are never popped by the interpreter's own _do_branch. A regression
    guard for exactly that: once the outer loop's own unconditional `br`
    later resolves via the interpreter, a stale inner frame on top of
    frame.frames misdirects depth-relative branch resolution.
    """
    wat = """
    (module
      (func (export "nested") (param $n i32) (result i32)
        (local $i i32) (local $j i32) (local $count i32)
        (block $outer_exit
          (loop $outer
            (br_if $outer_exit (i32.ge_s (local.get $i) (local.get $n)))
            (if (i32.eq (i32.rem_u (local.get $i) (i32.const 2)) (i32.const 0))
              (then
                (local.set $j (i32.const 0))
                (block $inner_exit
                  (loop $inner
                    (br_if $inner_exit (i32.ge_s (local.get $j) (i32.const 3)))
                    (local.set $count (i32.add (local.get $count) (i32.const 1)))
                    (local.set $j (i32.add (local.get $j) (i32.const 1)))
                    (br $inner)
                  )
                )
              )
            )
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $outer)
          )
        )
        (local.get $count)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, "
            "skipping test_jitr_nested_loop_in_if_frame_stack_reconciliation"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("nested")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=4)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.run(interp, fn_idx, [n])
    even_count = sum(1 for i in range(n) if i % 2 == 0)
    expected = even_count * 3
    assert results == [expected], (
        f"nested({n}) via JIT-driven RuntimeEngine.run() = {results}, expected [{expected}] -- "
        "a desynced frame.frames misresolves the outer loop's `br` once JIT skips the inner "
        "loop/if exit without popping the frames the interpreter pushed for them"
    )
    assert len(engine.cache.active.traces) > 0, (
        "the inner loop's exit-condition block must have compiled"
    )


def test_jitr_return_terminated_block_jit_result_correct():
    """
    JITR-43: a JIT-compiled block whose terminator is RETURN has
    `trace.next_pc is None` (the function is ending, not falling through to
    another block). `_invoke_trace` must resolve this via O(1)
    `len(frame.code)` -- the same "past the end" sentinel `current_pc()`
    already checks for -- never by decoding an `Instr` at runtime, which
    `{DirectBytecodeExecution}` (INTP-GOTCHA-05) forbids: no
    instruction-object generation at runtime, ever.
    """
    wat = """
    (module
      (func (export "f") (param $n i32) (result i32)
        (local $i i32)
        (local.set $i (i32.const 0))
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (i32.mul (local.get $i) (i32.const 3))
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, "
            "skipping test_jitr_return_terminated_block_jit_result_correct"
        )
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("f")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=2)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.run(interp, fn_idx, [n])
    assert results == [n * 3], (
        f"f({n}) via JIT-driven RuntimeEngine.run() = {results}, expected [{n * 3}]"
    )
    assert len(engine.cache.active.traces) > 0, (
        "the RETURN-terminated tail block must have compiled"
    )


if __name__ == "__main__":
    test_hotspot_01_2bit_card_marking_state_transitions()
    test_jitr_01_card_marking_granularity()
    test_hotspot_02_history_ring_buffered_yield_drain()
    test_hotspot_03_lifo_compile_queue_batch_drain()
    test_hotspot_04_3bank_cache_oldest_only_promotion()
    test_jitr_cache_bank_traces_always_sorted_by_head_pc()
    test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain()
    test_jitr_bitmap_checked_before_cache_lookup()
    test_jitr_31_to_35_trace_chaining_and_ok_unlinking()
    test_jitc_20_trace_header_16byte_physical_layout()
    test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card()
    test_hotspot_06_short_blocks_never_tracked_avoiding_card_aliasing()
    test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace()
    test_jitr_compile_queue_overflow_compiles_on_the_spot()
    test_jitr_control_skip_radix_tree_chaining()
    test_jitr_26_direct_mapped_folding_xor_jit_cache()
    test_jitr_block_capacity_from_wasm_loader_and_no_set()
    test_jitr_br_if_loop_exit_jit_result_correct()
    test_jitr_backward_branch_block_byte_span_not_disqualified()
    test_jitr_if_then_skipped_when_condition_false_after_jit()
    test_jitr_nested_loop_in_if_frame_stack_reconciliation()
    test_jitr_return_terminated_block_jit_result_correct()
    print("[PASS] All 22 JIT Hotspot Profiling & 3-Bank Cache tests passed.")
