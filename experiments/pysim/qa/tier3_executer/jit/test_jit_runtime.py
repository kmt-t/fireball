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
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


import wasmtime
from config import (
    FB_CONF_JIT_CACHE_SIZE,
    JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES,
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CACHE_COMMON_CODE_BYTES,
    JIT_CACHE_COMMON_CODE_OFFSET_BYTES,
    JIT_CACHE_OLDEST_OFFSET_BYTES,
    JIT_CACHE_PAGE_BYTES,
    JIT_CACHE_REGION_BYTES,
    JIT_CACHE_REGION_PAGE_COUNT,
    JIT_CACHE_WARM_OFFSET_BYTES,
    JIT_TRACE_HEADER_BYTES,
    JIT_X64_CHAIN_TARGET_OFFSET,
)
from execution_context import WASMContext
from helpers import make_interpreter as Interpreter
from helpers import wat_to_wasm
from system_containers import ReadOnlyRadixBinaryTreeStorage, StaticVector
from test_support import (
    PcOnlyCompiler,
    make_pc_only_functions_module,
    make_pc_only_module,
    make_runtime_engine,
)
from tier3_executer.interpreter.interpreter import InterpreterBindings
from tier3_executer.jit.common_code import (
    TRACE_ENTRY_STUB_BYTES,
)
from tier3_executer.jit.jit_cache import (
    CardState,
    HistoryRing,
    HotspotBitmap,
    JITCacheBank,
    JITMultiBufferCache,
    JITTrace,
    JITTraceHeader,
)
from tier3_executer.jit.jit_runtime import JITInterpreter
from tier3_executer.jit.runtime_engine import RuntimeEngine
from tier3_executer.jit.x64_jit import TraceCompiler
from wasm_module import I32, LocalWidthMap
from wasm_opcodes import BR_TABLE, I32_ADD, I32_CONST, LOCAL_GET, LOCAL_SET
from wasm_reader import parse


def test_jitr_00_cache_region_is_two_pages_with_fixed_common_area():
    """The 8 KiB region reserves 2 KiB for common code and three 2 KiB banks."""
    assert JIT_CACHE_REGION_BYTES == 8192
    assert FB_CONF_JIT_CACHE_SIZE == JIT_CACHE_REGION_BYTES
    assert JIT_CACHE_REGION_PAGE_COUNT == 2
    assert JIT_CACHE_PAGE_BYTES * JIT_CACHE_REGION_PAGE_COUNT == JIT_CACHE_REGION_BYTES
    assert JIT_CACHE_COMMON_CODE_BYTES == 2048
    assert JIT_CACHE_COMMON_CODE_OFFSET_BYTES == 0
    assert JIT_CACHE_BANK_COUNT == 3
    assert JIT_CACHE_BANK_CAPACITY_BYTES == 2048
    assert JIT_CACHE_ACTIVE_OFFSET_BYTES == 2048
    assert JIT_CACHE_WARM_OFFSET_BYTES == 4096
    assert JIT_CACHE_OLDEST_OFFSET_BYTES == 6144
    assert (
        JIT_CACHE_COMMON_CODE_BYTES + JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_CAPACITY_BYTES
        == JIT_CACHE_REGION_BYTES
    )


def test_jitr_00_common_apccs_area_survives_rotation_and_flush():
    """x64 common helper stubs stay in the shared 2KB prefix while banks rotate; ARMv8-M placement is TBD."""
    cache = JITMultiBufferCache()
    common = cache.common_code
    assert common.region_bytes == JIT_CACHE_REGION_BYTES
    assert common.common_code_bytes == JIT_CACHE_COMMON_CODE_BYTES
    assert common.prologue_offset == JIT_CACHE_COMMON_CODE_OFFSET_BYTES
    assert common.prologue_size > 0
    assert common.epilogue_size > 0
    assert common.helper_size > 0
    assert common.absolute_pool_size == JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES
    assert common.absolute_pool_offset + common.absolute_pool_size <= common.common_code_bytes
    snapshot = common.read_common()

    trace_blob = bytes(JIT_TRACE_HEADER_BYTES + 16)
    trace = JITTrace(head_pc=0x100, size_bytes=len(trace_blob), code_blob=trace_blob)
    assert cache.insert(trace)
    assert trace.code_offset == JIT_CACHE_ACTIVE_OFFSET_BYTES
    assert common.buffer.read(trace.code_offset, len(trace_blob)) == trace_blob

    cache.rotate()
    cache.flush_all()
    assert common.read_common() == snapshot


def test_hotspot_01_2bit_card_marking_state_transitions():
    """TEST-HOTSPOT-01 / TEST-JITR-02: 2-bit state machine: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)."""
    bitmap = HotspotBitmap(card_shift=4, code_lengths=(512,))
    pc = 0x100
    assert bitmap.card_of(pc) == pc >> 4
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
    assert bitmap.touch(pc) == CardState.COMPILED  # TEST-JITR-03: COMPILED touch remains COMPILED


def test_jitr_01_card_marking_granularity():
    """TEST-JITR-01: Card marking granularity is 64-byte card, not individual instruction."""
    bitmap = HotspotBitmap(card_shift=6, code_lengths=(0x1100,))  # 64-byte cards
    pc1 = 0x1000
    pc2 = 0x1020  # Same 64-byte card (0x1000..0x103F)
    assert bitmap.get_state(pc1) == CardState.UNEXECUTED
    assert bitmap.get_state(pc2) == CardState.UNEXECUTED
    bitmap.touch(pc1)
    # pc2 reflects the state change because both share the same card
    assert bitmap.get_state(pc2) == CardState.EXECUTED


def test_hotspot_02_history_ring_buffered_yield_drain():
    """TEST-HOTSPOT-02 / TEST-JITR-05: Interpreter records basic-block heads to HistoryRing, drained on yield."""
    ring = HistoryRing(capacity=8)
    assert ring.capacity == 8
    for i in range(10):
        ring.record(0x1000 + i * 4)

    assert ring.dropped == 2
    drained = ring.drain()
    assert len(drained) == 8
    assert len(ring.drain()) == 0


def test_hotspot_03_lifo_compile_queue_batch_drain():
    """TEST-HOTSPOT-03 / TEST-JITR-12: HOT traces are queued to LIFO compile queue and batch-compiled into Active bank."""
    compiled_traces = []

    def dummy_compiler(pc: int) -> JITTrace:
        t = JITTrace(head_pc=pc, native_fn=lambda: pc * 2, size_bytes=64)
        compiled_traces.append(pc)
        return t

    engine = make_runtime_engine(jit_compiler=PcOnlyCompiler(dummy_compiler), code_lengths=(0x400,))
    engine.register_module_blocks(make_pc_only_module((0x100, 0x200, 0x300)))
    engine.jit_runtime.compile_queue = StaticVector.of(
        [0x100, 0x200, 0x300], capacity=engine.jit_runtime.compile_queue_capacity
    )
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_traces == [0x300, 0x200], (
        "LIFO compilation order required {JIT_ReverseCompilationOrder}"
    )
    assert engine.jit_runtime.cache.active.has_trace(0x300)
    assert engine.jit_runtime.cache.active.has_trace(0x200)
    assert not engine.jit_runtime.cache.active.has_trace(0x100)


def test_hotspot_04_3bank_cache_oldest_only_promotion():
    """TEST-HOTSPOT-04 / TEST-JITR-22, 23: 3-bank cache: Warm hit never promotes; Oldest hit promotes to Active."""
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
    trace = JITTrace(head_pc=0x400, native_fn=lambda *_args: 7, size_bytes=64)
    assert trace.native_fn is not None
    assert trace(0, 0, 0, 0) == 7
    trace.execute(0, 0, 0, 0)


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
    RuntimeEngine.call() must check the O(1) card bitmap before ever calling
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    lookup_calls = []
    real_lookup = JITMultiBufferCache.lookup

    def spy(cache, pc):
        lookup_calls.append((pc, engine.jit_runtime.bitmap.get_state(pc)))
        return real_lookup(cache, pc)

    JITMultiBufferCache.lookup = spy
    try:
        engine.call(interp, fn_idx, [50])
    finally:
        JITMultiBufferCache.lookup = real_lookup

    assert lookup_calls, (
        "the loop must have gotten hot enough to compile and hit the cache at least once"
    )
    for pc, state in lookup_calls:
        assert state == CardState.COMPILED, (
            f"cache.lookup({pc:#x}) was called while its card was {state}, not COMPILED -- "
            "the bitmap must be checked first so a miss never reaches the cache search"
        )


def test_jitr_31_to_35_trace_chaining_and_ok_unlinking():
    """TEST-JITR-31..35: Successor metadata for resident traces and O(k) unlinking on Oldest purge."""
    cache = JITMultiBufferCache(bank_capacity=512)
    # t1 falls through to t2
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 in Active
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 records t2 as its logical successor in Active
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


def test_jitc_20_trace_header_24byte_x64_physical_layout():
    """TEST-JITC-20: x64 header stores only trace identity and native targets."""
    hdr = JITTraceHeader(head_wasm_pc=0x12345678, trace_byte_size=128, flags=0x01, variant_id=0x02)
    hdr.chain_target_addr = 0x20001000
    hdr.helper_target_addr = 0x0123456789ABCDEF
    raw = hdr.pack()
    assert len(raw) == 24

    fields = struct.unpack("<IHBBQQ", raw)
    pc, size, flags, var, target, helper_target = fields
    assert pc == 0x12345678
    assert size == 128
    assert flags == 0x01
    assert var == 0x02
    assert target == 0x20001000
    assert helper_target == 0x0123456789ABCDEF


def test_jitr_native_header_chain_executes_successor_body_once():
    """A resolved x64 header chain jumps to the successor body without re-entry."""

    compiler = TraceCompiler()
    source = compiler.compile_trace(
        0x100,
        ((I32_CONST, 1), (LOCAL_SET, 0)),
        0x200,
        None,
        8,
        LocalWidthMap((I32,)),
    )
    target = compiler.compile_trace(
        0x200,
        ((LOCAL_GET, 0), (I32_CONST, 2), (I32_ADD, None), (LOCAL_SET, 0)),
        None,
        None,
        12,
        LocalWidthMap((I32,)),
    )
    assert source is not None and target is not None

    cache = JITMultiBufferCache()
    assert cache.insert(target)
    assert cache.insert(source)
    assert source.chain_next == 0x200
    assert source.header.chain_target_addr != 0
    assert source.code_offset is not None
    assert target.raw_addr is not None
    raw_target = int.from_bytes(
        cache.common_code.buffer.read(
            source.code_offset + JIT_X64_CHAIN_TARGET_OFFSET,
            8,
        ),
        "little",
    )
    assert raw_target == target.raw_addr + 15

    context = WASMContext()
    context.locals = (0,)
    source.execute(context.context_ptr, context.sp_ptr, context.locals_ptr, 0)
    assert context.locals[0] == 3
    assert context.native_context.ip == 0x200
    cache.flush_all()
    assert source.chain_next is None
    assert source.header.chain_target_addr == 0
    assert (
        int.from_bytes(
            cache.common_code.buffer.read(
                source.code_offset + JIT_X64_CHAIN_TARGET_OFFSET,
                8,
            ),
            "little",
        )
        == 0
    )


def test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card():
    """TEST-HOTSPOT-05: Oldest bank eviction unlinks inbound sources and resets card state to UNEXECUTED."""
    bitmap = HotspotBitmap(code_lengths=(128,))
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
    TEST-HOTSPOT-06: a card's 2-bit state can only ever describe one block. Two
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
    engine = make_runtime_engine(jit_compiler=PcOnlyCompiler(lambda pc: None))
    assert engine.jit_runtime.min_trace_bytes == 4
    mod = engine.load_wasm(wasm_bytes)
    h0 = mod.blocks[0].head_pc
    h1 = mod.blocks[1].head_pc
    for _ in range(engine.jit_runtime.yield_threshold * 2):
        engine.record_block_head(h0)
        engine.record_block_head(h1)
    assert engine.jit_runtime.bitmap.get_state(h0) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(h1) == CardState.UNEXECUTED
    assert engine.jit_runtime.compile_queue == [], "short blocks must never reach the compile queue"


def test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace():
    """
    TEST-HOTSPOT-07: if a pc is queued for compilation while a trace already
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
    engine = make_runtime_engine(jit_compiler=PcOnlyCompiler(fake_compile), card_shift=3)
    mod = engine.load_wasm(wasm_bytes)
    pc = mod.blocks[0].head_pc
    engine.jit_runtime.cache.insert(JITTrace(pc, lambda: 0, size_bytes=64))
    engine.jit_runtime.compile_queue.push_back(pc)

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED


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
    engine = make_runtime_engine(
        jit_compiler=PcOnlyCompiler(fake_compile),
        card_shift=3,
        compile_queue_capacity=3,
    )
    mod = engine.load_wasm(wasm_bytes)
    pcs = [b.head_pc for b in mod.blocks]
    for pc in pcs:
        engine.jit_runtime.bitmap.touch(pc)
        engine.jit_runtime.bitmap.touch(pc)
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.HOT

    for pc in pcs:
        engine.jit_runtime.ring.record(pc)
    engine.on_yield()

    assert len(compile_calls) == 3
    assert len(engine.jit_runtime.compile_queue) == 0
    for pc in pcs:
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_compile_failure_unmarks_candidate_without_faking_compiled():
    """TEST-JITR-14: failed compilation permanently clears only candidate eligibility."""
    engine = make_runtime_engine(
        jit_compiler=PcOnlyCompiler(lambda _pc: None),
        card_shift=2,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    module = make_pc_only_module((0x10,))
    engine.register_module_blocks(module)
    pc = module.blocks[0].head_pc

    assert engine.jit_runtime.trackable.is_marked(pc)
    assert engine.jit_runtime.bitmap.touch(pc) == CardState.EXECUTED
    assert engine.jit_runtime.bitmap.touch(pc) == CardState.HOT
    engine.jit_runtime.compile_queue.push_back(pc)

    assert engine.idle_hook(budget=1) == 0
    assert engine.jit_runtime.bitmap.get_state(pc) == CardState.HOT
    assert not engine.jit_runtime.trackable.is_marked(pc)
    assert not engine.jit_runtime.compile_queue
    assert not engine.record_block_head(pc), "a failed candidate must never be re-queued"


def test_jitr_26_direct_mapped_folding_xor_jit_cache():
    """TEST-JITR-26 & GOTCHA-JITR-05: Native fixed-slot lookup and rotation invalidation."""
    cache = JITMultiBufferCache(bank_capacity=1024)
    # PC with function index 1, offset 0x20 -> (1 << 16) | 0x20 = 0x00010020
    pc1 = 0x00010020
    pc2 = 0x00000003
    t1 = JITTrace(head_pc=pc1, native_fn=lambda: 10, size_bytes=64)
    t2 = JITTrace(head_pc=pc2, native_fn=lambda: 20, size_bytes=64)

    # 1. These PCs collide after exactly three folds and 16 slots. A fourth
    # fold produces a different slot for pc1 and would hide the old mismatch.
    def slot_after_three_folds(pc: int) -> int:
        folded = pc ^ (pc >> 16)
        folded ^= folded >> 8
        folded ^= folded >> 4
        return folded & (cache.NUM_FAST_SLOTS - 1)

    def slot_after_four_folds(pc: int) -> int:
        folded = pc ^ (pc >> 16)
        folded ^= folded >> 8
        folded ^= folded >> 4
        folded ^= folded >> 2
        return folded & (cache.NUM_FAST_SLOTS - 1)

    assert slot_after_three_folds(pc1) == slot_after_three_folds(pc2)
    assert slot_after_four_folds(pc1) != slot_after_four_folds(pc2)

    # The native fixed table owns one strong reference and replaces collisions.
    cache._fast_cache.store(pc1, t1)
    t2_refcount = sys.getrefcount(t2)
    cache._fast_cache.store(pc2, t2)
    assert sys.getrefcount(t2) == t2_refcount + 1
    assert cache._fast_cache.lookup(pc1) is None
    assert cache._fast_cache.lookup(pc2) is t2
    cache._fast_cache.clear()
    assert sys.getrefcount(t2) == t2_refcount

    # 2. Insert populates the native fast slot.
    cache.insert(t1)
    assert cache._fast_cache.lookup(pc1) is t1

    # 3. Lookup hits the fast slot.
    assert cache.lookup(pc1) is t1

    # 4. Rotation invalidates native slots (GOTCHA-JITR-05).
    cache.rotate()  # t1 moves to Warm
    assert cache._fast_cache.lookup(pc1) is None

    # 5. Lookup refills from Warm (without promotion).
    assert cache.lookup(pc1) is t1
    assert cache.promotions == 0
    assert cache._fast_cache.lookup(pc1) is t1

    # 6. Rotate again: t1 moves to Oldest.
    cache.rotate()
    assert cache._fast_cache.lookup(pc1) is None

    # 7. Lookup from Oldest promotes to Active and fills the fast slot.
    promoted = cache.lookup(pc1)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.active.has_trace(pc1)
    assert cache._fast_cache.lookup(pc1) is t1

    # 8. Flush releases all native fast-slot references.
    cache.flush_all()
    assert cache._fast_cache.lookup(pc1) is None


def test_jitr_block_capacity_from_wasm_loader_and_no_set():
    """JITR: RuntimeEngine takes block capacity from WASM loader and strictly forbids set."""
    from wasm_reader import parse

    wat = "(module (func (i32.const 42) (return)) (func (i32.const 99) (return)))"
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        return
    mod = parse(wasm_bytes)

    # 1. WASM loader provides total_basic_blocks metadata and owns block_storage
    assert mod.total_basic_blocks == 2
    assert mod.block_storage is not None
    assert len(mod.block_storage.keys) == mod.total_basic_blocks
    assert isinstance(mod.block_storage, ReadOnlyRadixBinaryTreeStorage)
    assert len(mod.blocks) == 2

    # 2. RuntimeEngine binds loader-owned blocks and resolves them seamlessly
    engine = make_runtime_engine()
    engine.register_module_blocks(mod)
    first_block = mod.blocks[0]
    assert engine.get_block(first_block.head_pc) is first_block

    # 3. Strictly verify no python set is used anywhere in engine
    for attr in RuntimeEngine.__slots__:
        val = getattr(engine, attr)
        assert not isinstance(val, set), (
            f"Attribute {attr} must not be a set! Use system containers."
        )


# ===========================================================================
# 7. Tier 2 vMMIO: 3-Tier Gate & FC=14 SHM Ownership (runtime_vmmio_test_spec.md)
# ===========================================================================


# ===========================================================================
# 8. RuntimeEngine._invoke_trace: branch/skip resolution and interpreter
#    hand-off correctness -- covers the compiled-trace <-> interpreter
#    boundary that block-at-a-time debugger tests never exercise,
#    since RuntimeEngine.call() is the integrated execution driver with its own
#    _invoke_trace (see jit_runtime.md's tiered execution loop).
# ===========================================================================


def test_jitr_br_if_loop_exit_jit_result_correct():
    """
    TEST-JITR-40: once RuntimeEngine._invoke_trace compiles the loop's br_if
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 50
    results = engine.call(interp, fn_idx, [n])
    assert results == [sum(range(n))], (
        f"sum_to({n}) via JIT-driven RuntimeEngine.call() = {results}, "
        f"expected [{sum(range(n))}] -- the compiled loop-exit trace's "
        "condition must gate the branch, not be discarded"
    )
    assert len(engine.jit_runtime.cache.active.traces) > 0, (
        "the loop must have actually gotten hot enough to compile"
    )
    assert engine.stat_native_control_handlers > 0, (
        "JIT br_if terminators must run through C++ interpreter handlers"
    )


def test_jitr_loop_backedge_stays_in_cpp_until_coos_yield():
    """TEST-JITR-63: C++ runs LOOP handlers until the taken-backedge threshold."""
    wat = """
    (module
      (func (export "sum") (param i32) (result i32)
        (local i32)
        (loop $loop
          local.get 1
          local.get 0
          i32.add
          local.set 1
          local.get 0
          i32.const 1
          i32.sub
          local.tee 0
          br_if $loop
        )
        local.get 1
      )
    )
    """
    module = parse(wat_to_wasm(wat))
    function_index = module.export_func_index("sum")
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=2)
    engine.register_module_blocks(module)
    loop_block = next(
        block
        for block in module.blocks
        if block.loops_to == block.head_pc and block.head_pc >> 16 == function_index
    )
    trace = engine.jit_runtime._compile_trace(loop_block.head_pc, loop_block)
    assert trace is not None
    assert trace.next_pc is None
    assert trace.chain_next is None
    assert engine.jit_runtime.cache.insert(trace)
    engine.jit_runtime.bitmap.mark_compiled(loop_block.head_pc)
    interpreter = Interpreter(module)
    call_state = interpreter.start(function_index, [5])
    first_boundary = engine.run(interpreter, call_state)
    assert first_boundary.yield_requested
    assert first_boundary.call_state.current_pc() == loop_block.head_pc
    assert engine.stat_native_control_handlers >= 2
    assert engine.stat_jit_invocations >= 2
    assert engine.stat_native_dispatch_trace_transitions > 0
    assert first_boundary.call_state._frame is not None
    assert first_boundary.call_state._frame.context.native_context.loop_jump_count == 0

    results = engine.complete_call(interpreter, first_boundary.call_state)
    assert list(results) == [15]


def test_jitr_native_dispatch_snapshot_is_cached_and_hotspot_collection_is_configurable():
    """Stable trace snapshots are reused, and steady-state runs can skip profiling."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (export "sum") (param i32) (result i32)
                (local i32)
                (loop $loop
                  local.get 1
                  local.get 0
                  i32.add
                  local.set 1
                  local.get 0
                  i32.const 1
                  i32.sub
                  local.tee 0
                  br_if $loop
                )
                local.get 1))"""
        )
    )
    engine = make_runtime_engine(jit_compiler=TraceCompiler())
    engine.register_module_blocks(module)
    function_index = module.export_func_index("sum")
    manager = engine.jit_runtime

    initial_snapshot = manager.native_dispatch_state(function_index)
    cached_snapshot = manager.native_dispatch_state(function_index)
    assert cached_snapshot is initial_snapshot
    assert cached_snapshot.entries is initial_snapshot.entries
    assert cached_snapshot.trackable_blocks is initial_snapshot.trackable_blocks
    assert initial_snapshot.entry_count == 0
    assert initial_snapshot.trackable_count > 0

    loop_block = next(
        block
        for block in module.blocks
        if block.loops_to == block.head_pc and block.head_pc >> 16 == function_index
    )
    trace = manager._compile_trace(loop_block.head_pc, loop_block)
    assert trace is not None and manager.cache.insert(trace)
    manager.mark_compiled(loop_block.head_pc)
    compiled_snapshot = manager.native_dispatch_state(function_index)
    assert compiled_snapshot.entry_count == 1
    assert compiled_snapshot is not initial_snapshot
    assert compiled_snapshot.trackable_count == initial_snapshot.trackable_count
    assert compiled_snapshot.entries[0].head_pc == loop_block.head_pc
    assert compiled_snapshot.entries[0].entry_address == trace.raw_addr

    manager.set_hotspot_profiling_enabled(False)
    steady_snapshot = manager.native_dispatch_state(function_index)
    assert steady_snapshot.entry_count == compiled_snapshot.entry_count
    assert steady_snapshot.entries[0].head_pc == compiled_snapshot.entries[0].head_pc
    assert steady_snapshot.trackable_count == 0
    assert manager.lookup(loop_block.head_pc) is trace
    assert not manager.record_block_head(loop_block.head_pc)
    assert not manager.record_native_block_visits((), 0)
    manager.cache.rotate()
    manager.cache.rotate()
    oldest_snapshot = manager.native_dispatch_state(function_index)
    oldest_entry = next(
        oldest_snapshot.entries[index]
        for index in range(oldest_snapshot.entry_count)
        if oldest_snapshot.entries[index].head_pc == loop_block.head_pc
    )
    assert oldest_entry.promote_on_hit == 1
    assert list(engine.call(Interpreter(module), function_index, [5])) == [15]
    assert manager.cache.active.has_trace(loop_block.head_pc)


def test_jitr_cross_frame_loop_branch_skips_special_link_but_keeps_trace_body():
    """A legal branch across a nested frame uses the C++ handler, not the common helper."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (export "count_down") (param $n i32) (result i32)
                (loop $top
                  (local.get $n)
                  (i32.const 1)
                  (i32.sub)
                  (local.set $n)
                  (block $inner
                    (local.get $n)
                    (br_if $top)
                  )
                )
                (local.get $n)))"""
        )
    )
    function_index = module.export_func_index("count_down")
    engine = make_runtime_engine(jit_compiler=TraceCompiler())
    engine.register_module_blocks(module)
    branch_block = next(
        block
        for block in module.blocks
        if block.loops_to is not None and block.head_pc >> 16 == function_index
    )
    target_block = engine.get_block(branch_block.loops_to)
    assert target_block is not None
    assert target_block.frame_depth < branch_block.frame_depth
    trace = engine.jit_runtime._compile_trace(branch_block.head_pc, branch_block)
    assert trace is not None
    assert trace.next_pc is None
    assert engine.jit_runtime.insert_trace(trace)
    engine.jit_runtime.mark_compiled(branch_block.head_pc)

    result = engine.call(Interpreter(module), function_index, [3])

    assert list(result) == [0]
    if engine.collect_runtime_stats:
        assert engine.stat_native_control_handlers > 0
        assert engine.stat_native_dispatch_trace_transitions > 0


def test_interpreter_only_runtime_uses_the_shared_backedge_yield_threshold():
    """A non-JIT RuntimeEngine returns to COOS at the same counted boundary."""
    module = parse(
        wat_to_wasm(
            """
            (module
              (func (export "sum") (param $n i32) (result i32)
                (local $sum i32)
                (block $exit
                  (loop $top
                    (br_if $exit (i32.eqz (local.get $n)))
                    (local.set $sum (i32.add (local.get $sum) (local.get $n)))
                    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
                    (br $top)
                  )
                )
                (local.get $sum)
              )
            )
            """
        )
    )
    function_index = module.export_func_index("sum")
    engine = RuntimeEngine(yield_threshold=2)
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    first = engine.run(interpreter, interpreter.start(function_index, [5]))

    assert first.yield_requested
    loop_head_pc = next(
        block.next_pc
        for block in module.blocks
        if block.next_pc is not None and block.next_pc < block.head_pc
    )
    assert first.call_state.current_pc() == (function_index << 16) | loop_head_pc
    if engine.collect_runtime_stats:
        assert engine.stat_native_control_handlers >= 2
    assert list(engine.complete_call(interpreter, first.call_state)) == [15]


def test_runtime_profile_counters_are_disabled_by_default_without_changing_results():
    """Production defaults omit diagnostic counts while preserving dispatch semantics."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (export "sum") (param i32) (result i32)
                (local i32)
                (loop $loop
                  local.get 1
                  local.get 0
                  i32.add
                  local.set 1
                  local.get 0
                  i32.const 1
                  i32.sub
                  local.tee 0
                  br_if $loop
                )
                local.get 1))"""
        )
    )
    engine = RuntimeEngine(yield_threshold=2)
    function_index = module.export_func_index("sum")
    engine.register_module_blocks(module)
    result = engine.call(Interpreter(module), function_index, [5])

    assert list(result) == [15]
    assert not engine.collect_runtime_stats
    assert engine.stat_interp_steps == 0
    assert engine.stat_jit_invocations == 0
    assert engine.stat_native_control_handlers == 0
    assert engine.stat_native_dispatch_trace_transitions == 0


def test_jit_interpreter_uses_interpreter_call_template_method():
    """The JIT variant must preserve Interpreter.call's public call boundary."""
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
    module = parse(wat_to_wasm(wat))
    function_index = module.export_func_index("sum_to")
    expected = Interpreter(module).call(function_index, [50])
    jit_interpreter = JITInterpreter(
        module,
        InterpreterBindings.empty(),
        make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8),
    )

    assert jit_interpreter.call(function_index, [50]) == expected
    assert jit_interpreter.runtime_engine.stat_jit_invocations > 0


def test_jitr_backward_branch_block_byte_span_not_disqualified():
    """
    TEST-JITR-41 / GOTCHA-JITR-07: the loop body block (sum/increment, ending in
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
    # Use the production 4-byte card so both deliberately short loop blocks
    # remain eligible.
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8, card_shift=2)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 50
    results = engine.call(interp, fn_idx, [n])
    assert results == [sum(range(n))]
    compiled_heads = {pc for pc, _ in engine.jit_runtime.cache.active.traces}
    assert len(compiled_heads) >= 2, (
        f"only {len(compiled_heads)} block(s) compiled ({[hex(pc) for pc in compiled_heads]}) -- "
        "the backward-branching loop body must compile too, not just the forward condition check"
    )


def test_jitr_if_then_skipped_when_condition_false_after_jit():
    """
    TEST-JITR-42: once the `if (cond) (then ...)` condition-check block compiles,
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=4)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.call(interp, fn_idx, [n])
    expected = sum(abs(i - 5) for i in range(n))
    assert results == [expected], (
        f"abs_sum({n}) via JIT-driven RuntimeEngine.call() = {results}, expected [{expected}] -- "
        "an unconditionally-taken then-body (or an unconditionally-skipped one) throws this off"
    )
    assert len(engine.jit_runtime.cache.active.traces) > 0, (
        "the if-condition-check block must have gotten hot enough to compile"
    )


def test_jitr_nested_loop_in_if_frame_stack_reconciliation():
    """
    TEST-JITR-42: a loop nested inside an if nested inside an outer loop --
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=4)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.call(interp, fn_idx, [n])
    even_count = sum(1 for i in range(n) if i % 2 == 0)
    expected = even_count * 3
    assert results == [expected], (
        f"nested({n}) via JIT-driven RuntimeEngine.call() = {results}, expected [{expected}] -- "
        "a desynced frame.frames misresolves the outer loop's `br` once JIT skips the inner "
        "loop/if exit without popping the frames the interpreter pushed for them"
    )
    assert len(engine.jit_runtime.cache.active.traces) > 0, (
        "the inner loop's exit-condition block must have compiled"
    )


def test_jitr_return_terminated_block_jit_result_correct():
    """
    A JIT-compiled block whose terminator is RETURN has
    `trace.next_pc is None` (the function is ending, not falling through to
    another block). `_invoke_trace` resumes at the raw RETURN opcode after
    spilling the result to the shared operand stack. The interpreter's return
    handler publishes the sentinel and performs frame finalization. The JIT
    must never decode an `Instr` at runtime, which
    `{DirectBytecodeExecution}` (GOTCHA-INTP-05) forbids: no instruction-object
    generation at runtime, ever.
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
    # The tail is intentionally compact; use the smaller test card so its
    # RETURN-terminated block remains a valid JIT candidate.
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=2, card_shift=2)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    n = 20
    results = engine.call(interp, fn_idx, [n])
    assert results == [n * 3], (
        f"f({n}) via JIT-driven RuntimeEngine.call() = {results}, expected [{n * 3}]"
    )
    assert len(engine.jit_runtime.cache.active.traces) > 0, (
        "the RETURN-terminated tail block must have compiled"
    )


def test_jitr_terminal_trace_returns_to_interpreter_return_handler():
    """TEST-JITR-44: a terminal trace invokes the C++ RETURN handler once."""
    from tier3_executer.interpreter.interpreter import RETURN_SENTINEL_IP

    module = parse(wat_to_wasm("(module (func (result i32) i32.const 7 return))"))
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        card_shift=0,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    block = engine.get_block(0)
    assert block is not None
    trace = engine.jit_runtime._compile_trace(block.head_pc, block)
    assert trace is not None
    assert trace.next_pc is None

    interp = Interpreter(module)
    call_state = interp.start(0, [])
    call_state = engine._invoke_trace(interp, call_state, trace)
    assert not call_state.finished
    assert call_state.cont is not None
    return_ip, frame, _, _ = call_state.cont
    assert return_ip == RETURN_SENTINEL_IP
    assert frame.values.raw_top() == 7

    call_state = interp.step(call_state)
    assert call_state.finished
    assert call_state.results == [7]


def test_jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack():
    """TEST-JITR-45: nested WASM return restores the caller on the shared stack."""
    wat = """
    (module
      (func $inc (param i32) (result i32)
        local.get 0
        i32.const 1
        i32.add
        return
      )
      (func (export "caller") (param $n i32) (result i32)
        (local $last i32)
        (block $exit
          (loop $loop
            local.get $n
            call $inc
            local.set $last
            local.get $n
            i32.const 1
            i32.sub
            local.tee $n
            br_if $loop
          )
        )
        local.get $last
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, "
            "skipping test_jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack"
        )
        return
    module = parse(wasm_bytes)
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=4,
        card_shift=2,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    caller = module.export_func_index("caller")
    assert engine.call(interp, caller, [20]) == [2]
    assert any(pc >> 16 == 0 for pc, _ in engine.jit_runtime.cache.active.traces), (
        "the repeatedly called callee should be eligible for JIT execution"
    )


def test_jitr_if_else_loop_matches_interpreter_after_jit_compilation():
    """TEST-JITR-49: both if/else arms and loop exits match the interpreter after JIT."""
    wat = """
    (module
      (func (export "alternating_sum") (param $n i32) (result i32)
        (local $i i32) (local $acc i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
            (if (i32.eqz (i32.rem_u (local.get $i) (i32.const 2)))
              (then (local.set $acc (i32.add (local.get $acc) (local.get $i))))
              (else (local.set $acc (i32.sub (local.get $acc) (local.get $i))))
            )
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        (local.get $acc)
      )
    )
    """
    module = parse(wat_to_wasm(wat))
    function_index = module.export_func_index("alternating_sum")
    interpreter_engine = make_runtime_engine()
    interpreter_engine.register_module_blocks(module)
    jit_engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=4)
    jit_engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    jit_interpreter = Interpreter(module)

    inputs = (0, 1, 2, 17, 30)
    expected = [sum(i if i % 2 == 0 else -i for i in range(n)) for n in inputs]
    reference = [list(interpreter_engine.call(interpreter, function_index, [n])) for n in inputs]
    actual = [list(jit_engine.call(jit_interpreter, function_index, [n])) for n in inputs]

    assert reference == [[value] for value in expected]
    assert actual == reference
    assert jit_engine.stat_jit_invocations > 0


def test_jitr_br_table_uses_native_handler_and_preserves_every_target():
    """TEST-JITR-50: BR_TABLE uses its C++ handler for every target."""
    from control_flow import iter_scan_instrs

    wat = """
    (module
      (func (param i32) (result i32)
        (block $done
          (block $case1
            (block $case0
              local.get 0
              br_table $case0 $case1 $done
            )
            i32.const 10
            return
          )
          i32.const 20
          return
        )
        i32.const 30
      )
    )
    """
    module = parse(wat_to_wasm(wat))
    interpreter_engine = make_runtime_engine()
    interpreter_engine.register_module_blocks(module)
    jit_engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=1,
        card_shift=0,
        candidate_threshold=0,
        min_trace_bytes=1,
    )
    jit_engine.register_module_blocks(module)
    table_pc = next(
        (
            instruction.offset
            for instruction in iter_scan_instrs(module.code_for(0))
            if instruction.opcode == BR_TABLE
        ),
        None,
    )
    assert table_pc is not None
    assert jit_engine.get_block(table_pc) is None
    predecessor = next(
        (block for block in module.blocks if block.next_pc == table_pc),
        None,
    )
    assert predecessor is not None
    trace = jit_engine.jit_runtime._compile_trace(predecessor.head_pc, predecessor)
    assert trace is not None
    assert trace.next_pc is None
    assert trace.loops_to is None

    interpreter = Interpreter(module)
    jit_interpreter = Interpreter(module)
    for selector, expected in ((0, 10), (1, 20), (2, 30), (3, 30)):
        reference = list(interpreter_engine.call(interpreter, 0, [selector]))
        actual = list(jit_engine.call(jit_interpreter, 0, [selector]))
        assert reference == [expected]
        assert actual == reference
    assert jit_engine.stat_native_control_handlers > 0, (
        "BR_TABLE terminators must run through C++ interpreter handlers"
    )


def test_jitr_mixed_typed_stack_declines_jit_without_losing_drop_widths():
    """TEST-JITR-51: mixed i32/f32/i64/f64 values use typed DROP and safe JIT fallback."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (result i32)
                i32.const 11
                f32.const 1.5
                drop
                i64.const 70
                drop
                f64.const 2.5
                drop
                i32.const 1
                i32.add
                return))"""
        )
    )
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=1,
        card_shift=0,
        candidate_threshold=0,
        min_trace_bytes=1,
    )
    engine.register_module_blocks(module)
    mixed_block = engine.get_block(module.blocks[0].head_pc)
    assert mixed_block is not None
    assert engine.jit_runtime._compile_trace(mixed_block.head_pc, mixed_block) is None
    result = engine.call(Interpreter(module), 0, [])

    assert list(result) == [12]
    assert engine.stat_jit_invocations == 0
    assert len(engine.jit_runtime.cache.active.traces) == 0


def test_jitr_mixed_typed_callee_returns_keep_the_shared_stack_synchronized():
    """TEST-JITR-52: mixed-width callee results survive JIT execution in a nested frame."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (result f32) f32.const 1.5)
              (func (result i32) i32.const 20)
              (func (result i64) i64.const 70)
              (func (result f64) f64.const 2.5)
              (func (result i32)
                i32.const 11
                call 0
                call 1
                call 2
                call 3
                drop
                drop
                drop
                drop
                i32.const 1
                i32.add
                return))"""
        )
    )
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=1,
        card_shift=0,
        candidate_threshold=0,
        min_trace_bytes=1,
    )
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    actual = [list(engine.call(interpreter, 4, [])) for _ in range(6)]
    compiled_heads = {pc for pc, _ in engine.jit_runtime.cache.active.traces}

    assert actual == [[12]] * 6
    assert 1 << 16 in compiled_heads
    assert engine.stat_jit_invocations > 0


def test_jitr_runtime_engine_preserves_typed_top_level_results():
    """TEST-JITR-46: JIT-enabled RuntimeEngine preserves scalar results and fallback types."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (result i32) i32.const 7 return)
              (func (result i64) i64.const 70 return)
              (func (result f32) f32.const 7.5 return)
              (func (result f64) f64.const 8.5 return))"""
        )
    )
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=1,
        card_shift=0,
        candidate_threshold=0,
        min_trace_bytes=1,
    )
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    expected = ([7], [70], [7.5], [8.5])
    for function_index, values in enumerate(expected):
        for _ in range(3):
            result = engine.call(interp, function_index, [])
        assert list(result) == list(values)
    assert engine.stat_jit_invocations > 0


def test_jitr_host_import_stays_on_interpreter_runtime_boundary():
    """TEST-JITR-47: host imports are interpreted boundaries with shared results."""
    module = parse(
        wat_to_wasm(
            """(module
              (import "env" "increment" (func $increment (param i32) (result i32)))
              (func (result i32) i32.const 41 call $increment i32.const 1 i32.add))"""
        )
    )
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        card_shift=0,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    call_block = engine.get_block(0x1_0000)
    assert call_block is not None
    assert engine.jit_runtime._compile_trace(call_block.head_pc, call_block) is None

    host_functions = StaticVector.of((lambda value: int(value) + 1,), capacity=1)
    interp = Interpreter(module, host_functions=host_functions)
    assert engine.call(interp, 1, []) == [43]


def test_jitr_runtime_engine_surfaces_guest_trap_at_sync_boundary():
    """TEST-JITR-48: RuntimeEngine.call must not expose a guest trap as a None result."""
    module = parse(wat_to_wasm("(module (func (result i32) i32.const 1 i32.const 0 i32.div_s))"))
    engine = make_runtime_engine()
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    try:
        engine.call(interp, 0, [])
    except AssertionError as trap:
        assert trap.args == (15,)
    else:
        assert False, "RuntimeEngine.call returned normally after a guest trap"


def _pc(i, func=0):
    """Block i of function `func`; every block's card is distinct (card = 8 * i)."""
    return (func << 16) | (0x20 * i)


def _aging_engine(counts, compile_fn=None, **engine_kwargs):
    """RuntimeEngine over a PC-only module with `counts[f]` blocks in function f."""

    def default_compile(pc):
        return JITTrace(pc, lambda: 0, size_bytes=64)

    # The expectations below fix the sweep parameters; they do not follow the tuned defaults.
    engine_kwargs.setdefault("aging_step_units", 1)
    engine_kwargs.setdefault("aging_scan_bytes", 16)
    engine = make_runtime_engine(
        jit_compiler=PcOnlyCompiler(compile_fn if compile_fn is not None else default_compile),
        **engine_kwargs,
    )
    module = make_pc_only_functions_module(
        tuple(tuple(0x20 * i for i in range(count)) for count in counts)
    )
    engine.register_module_blocks(module)
    return engine, module


def _touch_via_yield(engine, pc):
    """Record one block head and let the yield handler touch its card."""
    engine.jit_runtime.ring.record(pc)
    engine.on_yield()


def _aging_full_lap(engine):
    # Every step processes at least one non-zero byte while any exists.
    for _ in range(engine.jit_runtime.update_bitmap.unit_count):
        engine.age_step()


def test_jitr_aging_decays_only_executed_cards():
    """TEST-JITR-16: the sweep turns every EXECUTED card of a marked function into UNEXECUTED, never HOT/COMPILED."""
    engine, _module = _aging_engine((4, 1))
    _touch_via_yield(engine, _pc(0))  # function 0: EXECUTED
    _touch_via_yield(engine, _pc(1))
    _touch_via_yield(engine, _pc(1))  # function 0: HOT, queued
    _touch_via_yield(engine, _pc(2))
    _touch_via_yield(engine, _pc(2))  # function 0: HOT, queued
    _touch_via_yield(engine, _pc(3))  # function 0: EXECUTED
    _touch_via_yield(engine, _pc(0, 1))  # function 1: EXECUTED
    engine.jit_runtime.bitmap.mark_compiled(_pc(2))  # stand-in for a resident trace
    assert engine.jit_runtime.bitmap.get_state(_pc(1)) == CardState.HOT
    queued = len(engine.jit_runtime.compile_queue)

    _aging_full_lap(engine)

    for pc in (_pc(0), _pc(3), _pc(0, 1)):
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.UNEXECUTED, (
            f"{pc:#x} must decay"
        )
    assert engine.jit_runtime.bitmap.get_state(_pc(1)) == CardState.HOT, (
        "HOT belongs to the compile queue"
    )
    assert engine.jit_runtime.bitmap.get_state(_pc(2)) == CardState.COMPILED, (
        "COMPILED belongs to the cache"
    )
    assert len(engine.jit_runtime.compile_queue) == queued
    assert not engine.jit_runtime.update_bitmap.is_marked(
        0
    ) and not engine.jit_runtime.update_bitmap.is_marked(1)
    # A decayed card restarts its warm-up: one touch gives EXECUTED, not HOT.
    _touch_via_yield(engine, _pc(0))
    assert engine.jit_runtime.bitmap.get_state(_pc(0)) == CardState.EXECUTED


def test_jitr_update_bitmap_covers_every_executed_card():
    """TEST-JITR-17: an update bit of 0 implies the function has no EXECUTED card, under arbitrary touches and sweeps."""
    engine, _module = _aging_engine((2,) * 20)
    seed = 12345
    for _ in range(400):
        seed = (seed * 1103515245 + 12345) & 0x7FFF_FFFF
        if seed % 5 == 0:
            engine.age_step()
        else:
            _touch_via_yield(engine, _pc((seed >> 4) % 2, (seed >> 8) % 20))
        for func in range(20):
            if not engine.jit_runtime.update_bitmap.is_marked(func):
                for index in range(2):
                    state = engine.jit_runtime.bitmap.get_state(_pc(index, func))
                    assert state != CardState.EXECUTED, (
                        f"function {func} has an EXECUTED card but its update bit is clear"
                    )


def test_jitr_aging_advances_once_per_rotation():
    """TEST-JITR-18: only a bank rotation (explicit or caused by a full bank) advances the sweep."""
    fail_pc = _pc(0, 4)

    def compile_fn(pc):
        return None if pc == fail_pc else JITTrace(pc, lambda: 0, size_bytes=64)

    engine, _module = _aging_engine((1,) * 41, compile_fn)  # 6 update-bitmap bytes
    _touch_via_yield(engine, _pc(0, 24))  # a dirty function in byte 3 makes each step observable
    _touch_via_yield(engine, _pc(0, 40))  # a later dirty function in byte 5

    assert engine.idle_hook() == 0  # empty queue
    engine.jit_runtime.compile_queue.push_back(_pc(0, 5))  # a successful compile
    assert engine.idle_hook() == 1
    engine.jit_runtime.bitmap.mark_compiled(_pc(0, 0))  # COMPILED-card skip
    engine.jit_runtime.compile_queue.push_back(_pc(0, 0))
    assert engine.idle_hook() == 0
    engine.jit_runtime.compile_queue.push_back(fail_pc)  # a failed compile
    assert engine.idle_hook() == 0
    assert engine.jit_runtime.aging_steps == 0 and engine.jit_runtime.update_bitmap.cursor == 0, (
        "compilation, whatever its outcome, does not age"
    )

    engine.jit_runtime.cache.flush_all()
    assert engine.jit_runtime.aging_steps == 0, "an explicit flush is not a rotation"

    engine.jit_runtime.cache.rotate()
    assert engine.jit_runtime.aging_steps == 1
    assert engine.jit_runtime.update_bitmap.cursor == 4, (
        "the dirty byte was processed and the cursor moved on"
    )
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 24)) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 40)) == CardState.EXECUTED, (
        "byte 5 lies beyond this step"
    )

    inserted = 0
    while engine.jit_runtime.aging_steps == 1:  # a full bank rotates by itself
        assert inserted < 30
        engine.jit_runtime.cache.insert(JITTrace(_pc(0, 6 + inserted), lambda: 0, size_bytes=512))
        inserted += 1
    assert engine.jit_runtime.aging_steps == 2 and inserted >= 2


def test_jitr_aging_cursor_wraps_and_bounds_each_step():
    """TEST-JITR-19: zero bytes do not count, a step ends at N units or O scanned bytes, the cursor wraps."""
    engine, _module = _aging_engine((1,) * 18)  # a 3-byte table
    _touch_via_yield(engine, _pc(0, 0))  # byte 0
    _touch_via_yield(engine, _pc(0, 17))  # byte 2
    assert engine.age_step() == 1 and engine.jit_runtime.update_bitmap.cursor == 1
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 0)) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 17)) == CardState.EXECUTED
    assert engine.age_step() == 1, "the zero byte 1 is skipped, byte 2 is processed"
    assert engine.jit_runtime.update_bitmap.cursor == 0, (
        "byte 2 was the last byte: the cursor wraps"
    )
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 17)) == CardState.UNEXECUTED
    scanned_before = engine.jit_runtime.aging_bytes_scanned
    assert engine.age_step() == 0, "nothing is dirty"
    assert engine.jit_runtime.aging_bytes_scanned - scanned_before == 3, (
        "one full pass, then the step ends"
    )
    assert engine.jit_runtime.update_bitmap.cursor == 0, (
        "a full pass returns the cursor to where it started"
    )
    assert engine.jit_runtime.aging_units_processed == 2

    # Two non-zero bytes per step; the zero byte in between does not count.
    wide, _ = _aging_engine((1,) * 18, aging_step_units=2)
    _touch_via_yield(wide, _pc(0, 0))
    _touch_via_yield(wide, _pc(0, 17))
    assert wide.age_step() == 2 and wide.jit_runtime.aging_units_processed == 2
    assert wide.jit_runtime.update_bitmap.cursor == 0, (
        "the step ended right after the second unit (byte 2)"
    )
    assert wide.jit_runtime.bitmap.get_state(_pc(0, 0)) == CardState.UNEXECUTED
    assert wide.jit_runtime.bitmap.get_state(_pc(0, 17)) == CardState.UNEXECUTED

    # The scan-byte limit ends a step even when no unit was processed.
    limited, _ = _aging_engine((1,) * 170, aging_step_units=100, aging_scan_bytes=4)  # 22 bytes
    _touch_via_yield(limited, _pc(0, 160))  # byte 20
    for expected_byte in (4, 8, 12, 16, 20):
        assert limited.age_step() == 0, "a limited scan must not reach the dirty byte yet"
        assert limited.jit_runtime.update_bitmap.cursor == expected_byte
    assert limited.jit_runtime.aging_bytes_scanned == 20
    assert limited.age_step() == 1, "the dirty byte lies inside the sixth scan window"
    assert limited.jit_runtime.update_bitmap.cursor == 2, (
        "bytes 20, 21, then 0 and 1 filled the window"
    )
    assert limited.jit_runtime.bitmap.get_state(_pc(0, 160)) == CardState.UNEXECUTED


def test_jitr_aging_processes_every_set_function_of_a_byte_and_ignores_imports():
    """TEST-JITR-19: one non-zero byte is one unit; an import function never gets its bit set."""
    engine, _module = _aging_engine((2, 2, 0, 0, 0, 0, 0, 0, 0, 1))  # 10 functions, 2 bytes
    _touch_via_yield(engine, _pc(1, 0))  # function 0, byte 0
    _touch_via_yield(engine, _pc(0, 1))  # function 1, byte 0
    _touch_via_yield(engine, _pc(0, 9))  # function 9, byte 1
    assert engine.age_step() == 2, "the EXECUTED cards of functions 0 and 1 decay in one step"
    assert (
        engine.jit_runtime.update_bitmap.cursor == 1
        and engine.jit_runtime.aging_units_processed == 1
    )
    assert engine.jit_runtime.bitmap.get_state(_pc(1, 0)) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 1)) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 9)) == CardState.EXECUTED, (
        "function 9 belongs to byte 1"
    )
    assert engine.age_step() == 1
    assert engine.jit_runtime.bitmap.get_state(_pc(0, 9)) == CardState.UNEXECUTED

    wat = """
    (module
      (import "env" "h" (func $h))
      (func (export "a") (i32.const 1) (drop) (i32.const 2) (drop))
      (func (export "b") (i32.const 3) (drop) (i32.const 4) (drop))
    )
    """
    wasm_engine = make_runtime_engine(
        jit_compiler=PcOnlyCompiler(lambda pc: None), aging_step_units=1, aging_scan_bytes=16
    )
    module = wasm_engine.load_wasm(bytes(wasmtime.wat2wasm(wat)))
    update = wasm_engine.jit_runtime.update_bitmap
    assert update.function_count == 3 and update.unit_count == 1
    pc_a = module.blocks[0].head_pc
    pc_b = module.blocks[len(module.blocks) - 1].head_pc
    assert pc_a >> 16 == 1 and pc_b >> 16 == 2
    _touch_via_yield(wasm_engine, pc_a)
    _touch_via_yield(wasm_engine, pc_b)
    assert not update.is_marked(0), "an import function has no cards, so its bit is never set"
    assert update.is_marked(1) and update.is_marked(2)
    assert wasm_engine.age_step() == 2, "both functions share byte 0 and are processed together"
    assert wasm_engine.jit_runtime.bitmap.get_state(pc_a) == CardState.UNEXECUTED
    assert wasm_engine.jit_runtime.bitmap.get_state(pc_b) == CardState.UNEXECUTED


def test_gotcha_jitr_09_aging_never_drops_compiled_or_hot():
    """GOTCHA-JITR-09: the sweep keeps a resident trace's card COMPILED and a queued card HOT."""
    pc_res, pc_hot, pc_exec = _pc(0), _pc(1), _pc(2)
    engine, _module = _aging_engine((3,))
    engine.jit_runtime.compile_queue.push_back(pc_res)
    assert engine.idle_hook(budget=1) == 1
    assert engine.jit_runtime.cache.find_trace(pc_res) is not None
    assert engine.jit_runtime.bitmap.get_state(pc_res) == CardState.COMPILED
    _touch_via_yield(engine, pc_hot)
    _touch_via_yield(engine, pc_hot)  # HOT, queued
    _touch_via_yield(engine, pc_exec)  # EXECUTED

    _aging_full_lap(engine)

    assert engine.jit_runtime.bitmap.get_state(pc_res) == CardState.COMPILED, (
        "a resident trace stays reachable"
    )
    assert engine.jit_runtime.cache.lookup(pc_res) is not None
    assert engine.jit_runtime.bitmap.get_state(pc_hot) == CardState.HOT
    assert engine.jit_runtime.compile_queue.contains(pc_hot), (
        "the pending request keeps its HOT card"
    )
    assert engine.jit_runtime.bitmap.get_state(pc_exec) == CardState.UNEXECUTED


CHAIN_STRESS_TRACES = 14
CHAIN_STRESS_HEAD = 0x100
CHAIN_STRESS_STRIDE = 0x10
CHAIN_STRESS_EXIT_PC = 0x9990


def _chain_stress_compile(compiler: TraceCompiler, index: int) -> JITTrace:
    """Trace `index` adds `1 << index` to local 0 and falls through to trace `index + 1`."""
    head_pc = CHAIN_STRESS_HEAD + index * CHAIN_STRESS_STRIDE
    is_last = index == CHAIN_STRESS_TRACES - 1
    next_pc = CHAIN_STRESS_EXIT_PC if is_last else head_pc + CHAIN_STRESS_STRIDE
    trace = compiler.compile_trace(
        head_pc,
        ((LOCAL_GET, 0), (I32_CONST, 1 << index), (I32_ADD, None), (LOCAL_SET, 0)),
        next_pc,
        None,
        12,
        LocalWidthMap((I32,)),
    )
    assert trace is not None
    return trace


def _chain_stress_check_links(cache: JITMultiBufferCache) -> None:
    """Every chain pointer, Python and native, must name a resident trace's live entry."""
    for bank in cache.banks:
        for pc, trace in bank.traces:
            assert trace.head_pc == pc
            assert trace.raw_addr is not None and trace.code_offset is not None
            native = int.from_bytes(
                cache.common_code.buffer.read(trace.code_offset + JIT_X64_CHAIN_TARGET_OFFSET, 8),
                "little",
            )
            assert native == trace.header.chain_target_addr, "native header diverged from Python"
            holders = sum(1 for other in cache.banks if other.has_trace(pc))
            assert holders == 1, f"trace {pc:#x} resident in {holders} banks"
            if trace.chain_next is None:
                assert native == 0, f"unchained trace {pc:#x} keeps a native jump"
                continue
            target = cache.find_trace(trace.chain_next)
            assert target is not None, f"{pc:#x} chains into evicted {trace.chain_next:#x}"
            assert target.raw_addr is not None
            assert native == target.raw_addr + TRACE_ENTRY_STUB_BYTES, (
                f"{pc:#x} jumps to a stale entry of {trace.chain_next:#x}"
            )


def _chain_stress_run(cache: JITMultiBufferCache, start: JITTrace) -> None:
    """Run the chain natively; the touched traces must equal the Python chain walk."""
    expected_mask = 0
    walked = start
    while True:
        expected_mask |= 1 << ((walked.head_pc - CHAIN_STRESS_HEAD) // CHAIN_STRESS_STRIDE)
        if walked.chain_next is None:
            break
        successor = cache.find_trace(walked.chain_next)
        assert successor is not None
        walked = successor
    context = WASMContext()
    context.locals = (0,)
    start.execute(context.context_ptr, context.sp_ptr, context.locals_ptr, 0)
    assert context.locals[0] == expected_mask, (
        f"native chain ran {context.locals[0]:#x}, chain pointers say {expected_mask:#x}"
    )
    assert walked.next_pc is not None
    assert context.native_context.ip == walked.next_pc


def test_jitr_62_chain_links_stay_valid_across_rotation_and_promotion():
    """
    TEST-JITR-62: randomized insert / promote / rotate sequences over a chain of
    traces keep every chain pointer on a live entry.

    After every step, each `chain_next` names a resident trace, the Python header and the
    native header agree, and no trace is resident in two banks.  Every trace looked up is
    then run natively; the traces the native chain touches match the pointers' walk, so a
    jump to a promoted trace's old code cannot pass.
    """
    import random

    compiler = TraceCompiler()
    promoted_total = 0
    evicted_total = 0
    chained_runs = 0
    for seed in range(40):
        rng = random.Random(seed)
        cache = JITMultiBufferCache(bank_capacity=512)
        for _ in range(90):
            index = rng.randrange(CHAIN_STRESS_TRACES)
            pc = CHAIN_STRESS_HEAD + index * CHAIN_STRESS_STRIDE
            action = rng.randrange(10)
            if action < 5:
                if cache.find_trace(pc) is None:
                    assert cache.insert(_chain_stress_compile(compiler, index))
            elif action < 9:
                found = cache.lookup(pc)
                if found is not None:
                    assert found is cache.find_trace(pc)
                    assert cache.find_bank(pc) is not cache.oldest, "promotion left it in Oldest"
                    _chain_stress_check_links(cache)
                    _chain_stress_run(cache, found)
                    chained_runs += found.chain_next is not None
            else:
                cache.rotate()
            _chain_stress_check_links(cache)
        promoted_total += cache.promotions
        evicted_total += cache.evictions
    assert promoted_total > 0, "sequences never promoted a trace"
    assert evicted_total > 0, "sequences never evicted a trace"
    assert chained_runs > 0, "sequences never ran a chained trace"


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
    test_jitc_20_trace_header_24byte_x64_physical_layout()
    test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card()
    test_hotspot_06_short_blocks_never_tracked_avoiding_card_aliasing()
    test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace()
    test_jitr_compile_queue_overflow_compiles_on_the_spot()
    test_jitr_compile_failure_unmarks_candidate_without_faking_compiled()
    test_jitr_26_direct_mapped_folding_xor_jit_cache()
    test_jitr_block_capacity_from_wasm_loader_and_no_set()
    test_jitr_br_if_loop_exit_jit_result_correct()
    test_jitr_backward_branch_block_byte_span_not_disqualified()
    test_jitr_if_then_skipped_when_condition_false_after_jit()
    test_jitr_nested_loop_in_if_frame_stack_reconciliation()
    test_jitr_return_terminated_block_jit_result_correct()
    test_jitr_terminal_trace_returns_to_interpreter_return_handler()
    test_jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack()
    test_jitr_if_else_loop_matches_interpreter_after_jit_compilation()
    test_jitr_br_table_uses_native_handler_and_preserves_every_target()
    test_jitr_mixed_typed_stack_declines_jit_without_losing_drop_widths()
    test_jitr_mixed_typed_callee_returns_keep_the_shared_stack_synchronized()
    test_jitr_runtime_engine_preserves_typed_top_level_results()
    test_jitr_host_import_stays_on_interpreter_runtime_boundary()
    test_jitr_runtime_engine_surfaces_guest_trap_at_sync_boundary()
    test_jitr_aging_decays_only_executed_cards()
    test_jitr_update_bitmap_covers_every_executed_card()
    test_jitr_aging_advances_once_per_rotation()
    test_jitr_aging_cursor_wraps_and_bounds_each_step()
    test_jitr_aging_processes_every_set_function_of_a_byte_and_ignores_imports()
    test_gotcha_jitr_09_aging_never_drops_compiled_or_hot()
    test_jitr_62_chain_links_stay_valid_across_rotation_and_promotion()
    print("[PASS] All 38 JIT Runtime & Cache tests passed.")
