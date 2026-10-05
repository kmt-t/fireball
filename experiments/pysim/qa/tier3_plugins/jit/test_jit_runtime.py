from __future__ import annotations

from qa.shared.jit_abi import dispatch_for_test

"""
Unit tests for Tier 3 JIT: JIT Hotspot Profiling & 3-Bank Cache
Traceability: jit_compiler_test_spec.md, jit_runtime_test_spec.md
"""

import ctypes
import struct
import sys
from collections.abc import Iterable
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


import pytest
import wasmtime
from config import (
    FB_CONF_JIT_CACHE_SIZE,
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CACHE_BANK_ENTRY_CAPACITY,
    JIT_CACHE_COMMON_CODE_BYTES,
    JIT_CACHE_COMMON_CODE_OFFSET_BYTES,
    JIT_CACHE_FAST_SLOT_COUNT,
    JIT_CACHE_OLDEST_OFFSET_BYTES,
    JIT_CACHE_PAGE_BYTES,
    JIT_CACHE_REGION_BYTES,
    JIT_CACHE_REGION_PAGE_COUNT,
    JIT_CACHE_WARM_OFFSET_BYTES,
    JIT_TRACE_HEADER_BYTES,
    JIT_X64_CHAIN_TARGET_OFFSET,
)
from qa.shared.common_code import (
    TRACE_ENTRY_STUB_BYTES,
)
from qa.shared.helpers import make_native_interpreter as Interpreter
from qa.shared.helpers import wat_to_wasm
from qa.shared.jit_abi import (
    NativeBlockVisitHistory,
    NativeDispatchSnapshot,
    NativeTraceDispatchEntry,
)
from qa.shared.jit_cache import (
    BlockCardMask,
    CardState,
    CardUpdateBitmap,
    HotspotBitmap,
    JitRuntimeBoundary,
    JITTrace,
    JITTraceHeader,
)
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.runtime_support import (
    PC_ONLY_FUNCTION_BASE,
    PC_ONLY_FUNCTION_STRIDE,
    compile_module_block,
    compile_runtime_block,
    make_pc_only_functions_module,
    make_runtime_engine,
    resident_module_traces,
)
from qa.shared.x64_jit import TraceCompiler
from system_containers import ReadOnlyRadixBinaryTreeStorage, StaticVector
from tier2_runtime.abi.interpreter_abi import EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
from tier2_runtime.interpreter.interpreter import (
    ExecutionContext,
    InterpreterBindings,
    NativeInterpreter,
    select_native_dispatch_entry,
)
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.wasm.module import I32, LocalWidthMap, Module, WasmOperand
from tier2_runtime.wasm.opcodes import (
    BR_IF,
    BR_TABLE,
    I32_ADD,
    I32_CONST,
    IF,
    LOCAL_GET,
    LOCAL_SET,
    RETURN,
)
from tier2_runtime.wasm.reader import parse


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
    cache = JitRuntimeBoundary()
    trace_blob = bytes(JIT_TRACE_HEADER_BYTES + 16)
    trace = JITTrace(head_pc=0x100, size_bytes=len(trace_blob), code_blob=trace_blob)
    assert cache.insert(trace)
    assert trace.code_offset == JIT_CACHE_ACTIVE_OFFSET_BYTES
    assert trace.raw_addr is not None
    region_base = trace.raw_addr - JIT_TRACE_HEADER_BYTES - trace.code_offset
    snapshot = ctypes.string_at(region_base, JIT_CACHE_COMMON_CODE_BYTES)
    assert ctypes.string_at(region_base + trace.code_offset, len(trace_blob)) == trace_blob

    cache.rotate()
    cache.flush_all()
    assert ctypes.string_at(region_base, JIT_CACHE_COMMON_CODE_BYTES) == snapshot


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


def test_hotspot_and_trackable_bitmaps_share_one_code_region_card_space():
    """Hotspot and candidate state use flat code-region storage across function boundaries."""
    lengths = (3, 3)
    bases = (0, 3)
    hotspot = HotspotBitmap(card_shift=2, code_lengths=lengths, function_pc_bases=bases)
    trackable = BlockCardMask(card_shift=2, code_lengths=lengths, function_pc_bases=bases)
    first_function_pc = 2
    second_function_pc = 3

    assert hotspot.card_count == 2
    assert trackable.card_count == 2
    assert hotspot.storage.count == hotspot.card_count
    assert trackable.storage.count == trackable.card_count
    assert hotspot.card_of(first_function_pc) == hotspot.card_of(second_function_pc) == 0
    assert hotspot.touch(first_function_pc) == CardState.EXECUTED
    assert hotspot.get_state(second_function_pc) == CardState.EXECUTED
    assert hotspot.touch(second_function_pc) == CardState.HOT
    trackable.mark(first_function_pc)
    assert trackable.is_marked(second_function_pc)
    trackable.unmark(second_function_pc)
    assert not trackable.is_marked(first_function_pc)


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
    """TEST-HOTSPOT-02 / TEST-JITR-05: Native history keeps the newest heads and consumes each record once."""
    manager = JITRuntimeManager(code_lengths=(0x1100,), card_shift=0, history_capacity=8)
    pcs = tuple(0x1000 + i * 4 for i in range(10))
    for pc in pcs:
        manager.trackable.mark(pc)
        manager.record_block_head(pc)
    manager.on_interpreter_exit(False)
    assert manager.exec_counter == 10
    assert manager.history_overwritten_count == 2
    assert manager.last_history_analysis_approximate
    assert all(manager.card_state(pc) == CardState.UNEXECUTED for pc in pcs[:2])
    assert all(manager.card_state(pc) == CardState.EXECUTED for pc in pcs[2:])
    manager.on_interpreter_exit(False)
    assert all(manager.card_state(pc) == CardState.EXECUTED for pc in pcs[2:])
    assert not manager.has_pending_compilation()


def test_hotspot_03_lifo_compile_queue_batch_drain():
    """TEST-HOTSPOT-03 / TEST-JITR-12: HOT traces are queued to LIFO compile queue and batch-compiled into Active bank."""
    compiler = TraceCompiler()
    engine = make_runtime_engine(
        jit_compiler=compiler,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    wat = """(module
      (func (export "f0") (result i32) i32.const 10 i32.const 20 i32.add)
      (func (export "f1") (result i32) i32.const 30 i32.const 40 i32.add)
      (func (export "f2") (result i32) i32.const 50 i32.const 60 i32.add))"""
    module = parse(wat_to_wasm(wat))
    engine.register_module_blocks(module)
    pcs = [block.head_pc for block in module.blocks]
    assert len(pcs) == 3
    for pc in pcs:
        engine.jit_runtime.record_block_head(pc)
        engine.jit_runtime.record_block_head(pc)
    engine.on_yield()
    assert engine.jit_runtime.has_pending_compilation()
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert engine.jit_runtime.compilation_pcs == (pcs[2], pcs[1]), (
        "LIFO compilation order required {JIT_ReverseCompilationOrder}"
    )
    assert engine.jit_runtime.cache.find_trace(pcs[2]) is not None
    assert engine.jit_runtime.cache.find_trace(pcs[1]) is not None
    assert engine.jit_runtime.cache.find_trace(pcs[0]) is None
    assert engine.jit_runtime.has_pending_compilation()
    assert engine.idle_hook(budget=2) == 1
    assert not engine.jit_runtime.has_pending_compilation()


def test_hotspot_04_3bank_cache_oldest_only_promotion():
    """TEST-HOTSPOT-04 / TEST-JITR-22, 23: 3-bank cache: Warm hit never promotes; Oldest hit promotes to Active."""
    cache = JitRuntimeBoundary(bank_capacity=512)
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64)
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t1)  # In Active
    cache.rotate()  # t1 moved to Warm
    cache.insert(t2)  # t2 in Active
    # Warm hit on t1: zero promotion overhead {JIT_OldestOnly_Promote}
    assert cache.lookup(0x100) is t1
    assert cache.promotions == 0
    assert cache.find_trace(0x100) is not None
    cache.rotate()  # t1 moved to Oldest, t2 moved to Warm
    assert cache.find_trace(0x100) is not None
    # Oldest hit on t1: must promote to Active
    promoted = cache.lookup(0x100)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.find_trace(0x100) is not None
    assert cache.lookup(0x100) is t1 and cache.promotions == 1
    trace = JITTrace(head_pc=0x400, native_fn=lambda *_args: 7, size_bytes=64)
    assert trace.native_fn is not None
    assert trace(0, 0, 0, 0) == 7
    trace.execute(0, 0, 0, 0)


def test_jitr_cache_lookup_accepts_unsorted_insertions_and_promoted_slots():
    """Sorted native index preserves lookup after insertion, replacement and promotion."""
    cache = JitRuntimeBoundary(bank_capacity=2048)
    for pc in (0x300, 0x100, 0x500, 0x200, 0x400):
        assert cache.insert(JITTrace(head_pc=pc, native_fn=lambda: 0, size_bytes=64))
    assert cache.resident_count == 5
    for pc in (0x100, 0x200, 0x300, 0x400, 0x500):
        assert cache.find_trace(pc).head_pc == pc
    replacement = JITTrace(head_pc=0x300, native_fn=lambda: 1, size_bytes=64)
    assert cache.insert(replacement)
    assert cache.resident_count == 5 and cache.find_trace(0x300) is replacement
    cache.rotate()
    cache.rotate()
    assert cache.lookup(0x300) is replacement
    assert cache.resident_count == 5 and cache.find_trace(0x300) is not None
    for pc in (0x100, 0x200, 0x400, 0x500):
        assert cache.find_trace(pc) is not None
    cache.rotate()
    assert cache.resident_count == 1 and cache.find_trace(0x300) is replacement


def test_jitr_bank_entry_limit_preserves_state_and_reuses_existing_slots():
    """Replacement reuses the existing slot; a third distinct PC rotates a full index."""
    cache = JitRuntimeBoundary(bank_capacity=128, entry_capacity=2)
    first = JITTrace(head_pc=0x100, native_fn=lambda: 0, size_bytes=JIT_TRACE_HEADER_BYTES)
    second = JITTrace(head_pc=0x200, native_fn=lambda: 0, size_bytes=JIT_TRACE_HEADER_BYTES)
    assert cache.insert(first) and cache.insert(second)
    assert cache.entry_capacity == 6
    used = cache.resident_bytes
    replacement = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=JIT_TRACE_HEADER_BYTES)
    assert cache.insert(replacement)
    assert replacement.code_offset == first.code_offset
    assert cache.resident_bytes == used and cache.resident_count == 2
    newcomer = JITTrace(head_pc=0x300, native_fn=lambda: 0, size_bytes=JIT_TRACE_HEADER_BYTES)
    assert cache.insert(newcomer)
    assert cache.rotations == 1
    assert cache.find_trace(first.head_pc) is replacement
    assert cache.find_trace(second.head_pc) is second
    assert cache.find_trace(newcomer.head_pc) is newcomer
    assert cache.resident_count == 3 and cache.resident_bytes == used + newcomer.size_bytes
    cache.rotate()
    assert cache.find_trace(first.head_pc) is replacement
    assert cache.find_trace(second.head_pc) is second
    cache.rotate()
    assert cache.find_trace(first.head_pc) is None
    assert cache.find_trace(second.head_pc) is None
    assert cache.find_trace(newcomer.head_pc) is newcomer
    cache.rotate()
    assert cache.find_trace(newcomer.head_pc) is None


def test_jitr_entry_limit_rotates_before_code_region_is_full():
    cache = JitRuntimeBoundary()
    for pc in range(JIT_CACHE_BANK_ENTRY_CAPACITY):
        assert cache.insert(JITTrace(head_pc=pc, size_bytes=JIT_TRACE_HEADER_BYTES))
    assert cache.resident_bytes < JIT_CACHE_BANK_CAPACITY_BYTES
    assert cache.resident_count == JIT_CACHE_BANK_ENTRY_CAPACITY
    newcomer = JITTrace(head_pc=0x300, size_bytes=JIT_TRACE_HEADER_BYTES)
    assert cache.insert(newcomer)
    assert cache.rotations == 1
    assert cache.find_trace(0x300) is newcomer
    assert all(cache.find_trace(pc) is not None for pc in range(JIT_CACHE_BANK_ENTRY_CAPACITY))
    assert (
        cache.resident_bytes
        == JIT_CACHE_BANK_ENTRY_CAPACITY * JIT_TRACE_HEADER_BYTES + newcomer.size_bytes
    )
    cache.rotate()
    assert all(cache.find_trace(pc) is not None for pc in range(JIT_CACHE_BANK_ENTRY_CAPACITY))
    cache.rotate()
    assert all(cache.find_trace(pc) is None for pc in range(JIT_CACHE_BANK_ENTRY_CAPACITY))
    assert cache.find_trace(newcomer.head_pc) is newcomer


def test_jitr_insert_preserves_one_resident_trace_per_pc():
    cache = JitRuntimeBoundary()
    trace = JITTrace(head_pc=0x100)
    assert cache.insert(trace)
    for _ in range(2):
        cache.rotate()
        before = (cache.resident_bytes, cache.resident_count, cache.generation)
        with pytest.raises(AssertionError):
            cache.insert(JITTrace(head_pc=trace.head_pc))
        assert (
            cache.resident_bytes,
            cache.resident_count,
            cache.generation,
        ) == before
        assert cache.find_trace(trace.head_pc) is trace


def test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain():
    """
    Promoting a trace out of Oldest must carry its inbound chain-source
    registrations to wherever it lands. Without this, a later rotate()
    looks for them on the bank the trace used to live in -- which no
    longer holds it -- and never unlinks a source chained into it once the
    trace is genuinely purged from its new bank, leaving a dangling
    `chain_next`.
    """
    cache = JitRuntimeBoundary(bank_capacity=512)
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 -> Active
    cache.rotate()  # t2's bank -> Warm
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 -> new Active, chains into Warm-resident t2
    assert t1.chain_next == 0x200
    assert cache.find_trace(t2.head_pc) is t2 and t1.chain_next == t2.head_pc

    cache.rotate()  # t2's bank -> Oldest
    promoted = cache.lookup(0x200)  # promote t2 out of Oldest
    assert promoted is t2
    assert cache.promotions == 1 and t1.chain_next == t2.head_pc
    # Promote the source while it is Oldest, before another rotation purges it.
    cache.rotate()
    assert cache.find_trace(t1.head_pc) is not None
    assert cache.lookup(t1.head_pc) is t1
    assert t1.chain_next == t2.head_pc
    cache.rotate()
    assert cache.find_trace(t2.head_pc) is not None
    assert t1.chain_next == t2.head_pc
    cache.rotate()
    assert cache.find_trace(t2.head_pc) is None
    assert cache.find_trace(t1.head_pc) is t1 and t1.chain_next is None
    assert t1.header.chain_target_addr == 0


def test_jitr_native_trace_lookup_uses_resident_snapshot():
    """
    Normal dispatch searches only resident traces in C++, without a Python
    cache lookup at each execution boundary.
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
        print("    [SKIP] wasmtime not installed, skipping native trace lookup test")
        return
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("sum_to")
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    interp = Interpreter(module)

    lookup_calls = []
    real_lookup = JitRuntimeBoundary.lookup

    def spy(cache, pc):
        lookup_calls.append((pc, engine.jit_runtime.bitmap.get_state(pc)))
        return real_lookup(cache, pc)

    JitRuntimeBoundary.lookup = spy
    try:
        assert engine.call(interp, fn_idx, [50]) == [1225]
    finally:
        JitRuntimeBoundary.lookup = real_lookup

    assert engine.stat_jit_invocations > 0
    assert lookup_calls == []
    snapshot = engine.jit_runtime.native_dispatch_state()
    assert snapshot.entry_count > 0
    for index in range(snapshot.entry_count):
        pc = snapshot.entries[index].head_pc
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED
        assert engine.jit_runtime.cache.find_trace(pc) is not None


def test_jitr_31_to_35_trace_chaining_and_ok_unlinking():
    """TEST-JITR-31..35: Successor metadata for resident traces and O(k) unlinking on Oldest purge."""
    cache = JitRuntimeBoundary(bank_capacity=512)
    # t1 falls through to t2
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 in Active
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 records t2 as its logical successor in Active
    assert t1.chain_next == 0x200
    # Rotate 1: t1, t2 -> Warm
    cache.rotate()
    assert cache.find_trace(0x100) is not None
    assert t1.chain_next == 0x200  # Preserved
    # Rotate 2: t1, t2 -> Oldest
    cache.rotate()
    assert cache.find_trace(0x100) is not None
    assert t1.chain_next == 0x200  # Existing chain is preserved in Oldest
    # Rotate 3: Oldest is purged. O(k) unlinking resets chain_next of source pointing to purged targets
    cache.rotate()
    assert cache.find_trace(t1.head_pc) is None
    assert cache.find_trace(t2.head_pc) is None
    assert t1.chain_next is None and t1.header.chain_target_addr == 0


def test_jitc_20_trace_header_16byte_x64_physical_layout():
    """TEST-JITC-20/23: only native code targets belong in the physical header."""
    hdr = JITTraceHeader(helper_target_addr=0x0123456789ABCDEF)
    hdr.chain_target_addr = 0x20001000
    raw = hdr.pack()
    assert raw == struct.pack("<QQ", 0x20001000, 0x0123456789ABCDEF)
    assert len(raw) == 16
    assert not hasattr(hdr, "head_wasm_pc")
    assert not hasattr(hdr, "trace_byte_size")
    assert not hasattr(hdr, "flags")
    assert not hasattr(hdr, "variant_id")


@pytest.mark.parametrize("entry_name", ("execute", "call", "fn", "native_fn"))
def test_jitr_native_header_chain_executes_successor_body_once(entry_name: str):
    """A resolved x64 header chain jumps to the successor body without re-entry."""

    compiler = TraceCompiler()
    source = compiler.compile_instructions(
        head_pc=0x100,
        instructions=((I32_CONST, 1), (LOCAL_SET, 0)),
        next_pc=0x200,
        loops_to=None,
        byte_length=8,
        local_layout=LocalWidthMap((I32,)),
    )
    target = compiler.compile_instructions(
        head_pc=0x200,
        instructions=((LOCAL_GET, 0), (I32_CONST, 2), (I32_ADD, None), (LOCAL_SET, 0)),
        next_pc=None,
        loops_to=None,
        byte_length=12,
        local_layout=LocalWidthMap((I32,)),
    )
    assert source is not None and target is not None

    cache = JitRuntimeBoundary()
    assert cache.insert(target)
    assert cache.insert(source)
    assert source.chain_next == 0x200
    assert source.header.chain_target_addr != 0
    assert source.code_offset is not None
    assert target.raw_addr is not None
    raw_target = int.from_bytes(
        ctypes.string_at(
            source.raw_addr - JIT_TRACE_HEADER_BYTES + JIT_X64_CHAIN_TARGET_OFFSET,
            8,
        ),
        "little",
    )
    assert raw_target == target.raw_addr + 15

    context = ExecutionContext()
    assert context.local_stack.extend((0,))
    if entry_name == "execute":
        entry = source.execute
    elif entry_name == "call":
        entry = source
    elif entry_name == "fn":
        entry = source.fn
    else:
        entry = source.native_fn
    assert entry is not None
    entry(context.context_ptr, context.sp_ptr, context.locals_ptr, 0)
    assert context.local_stack[0] == 3
    assert context.ip == 0x200
    cache.flush_all()
    assert source.chain_next is None
    assert source.header.chain_target_addr == 0
    assert (
        int.from_bytes(
            ctypes.string_at(
                source.raw_addr - JIT_TRACE_HEADER_BYTES + JIT_X64_CHAIN_TARGET_OFFSET,
                8,
            ),
            "little",
        )
        == 0
    )


def test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card():
    """TEST-HOTSPOT-05: Oldest bank eviction unlinks inbound sources and resets card state to UNEXECUTED."""
    bitmap = HotspotBitmap(code_lengths=(128,))
    cache = JitRuntimeBoundary(bank_capacity=256)
    update = CardUpdateBitmap(card_count=bitmap.card_count)
    cache.bind_cards(bitmap, update, units=2, scan_bytes=8)
    t_evict = JITTrace(0x50, lambda: 50, size_bytes=64)
    cache.insert(t_evict)
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
    engine = make_runtime_engine(yield_threshold=17)
    assert engine.jit_runtime.min_trace_bytes == 4
    mod = engine.load_wasm(wasm_bytes)
    h0 = mod.blocks[0].head_pc
    h1 = mod.blocks[1].head_pc
    for _ in range(engine.jit_runtime.yield_threshold * 2):
        engine.jit_runtime.record_block_head(h0)
        engine.jit_runtime.record_block_head(h1)
    assert engine.jit_runtime.bitmap.get_state(h0) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(h1) == CardState.UNEXECUTED
    assert not engine.jit_runtime.has_pending_compilation(), (
        "short blocks must never reach the compile queue"
    )


def test_hotspot_07_idle_hook_skips_recompiling_an_already_resident_trace():
    """
    TEST-HOTSPOT-07: if a pc is queued for compilation while a trace already
    resides in the cache under that exact pc (e.g. re-queued before an
    earlier compile's mark_compiled() landed), idle_hook must trust the
    cache -- the authority on whether *this* pc has a trace -- over the
    coarse per-card bitmap, and skip recompiling it.
    """
    wat = '(module (func (export "f") i32.const 1 drop i32.const 2 drop return))'
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), card_shift=3)
    mod = engine.load_wasm(wasm_bytes)
    pc = mod.blocks[0].head_pc
    engine.jit_runtime.trackable.mark(pc)
    engine.jit_runtime.record_block_head(pc)
    engine.jit_runtime.record_block_head(pc)
    engine.on_yield()
    engine.jit_runtime.cache.insert(JITTrace(pc, lambda: 0, size_bytes=64))

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_compile_queue_overflow_compiles_on_the_spot():
    """JITR: When compile_queue reaches capacity, all queued traces are compiled on the spot."""
    wat = """
    (module
      (func (export "f0") i32.const 1 drop i32.const 2 drop return)
      (func (export "f1") i32.const 1 drop i32.const 2 drop return)
      (func (export "f2") i32.const 1 drop i32.const 2 drop return)
    )
    """
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    compiler = TraceCompiler()
    engine = make_runtime_engine(
        jit_compiler=compiler,
        card_shift=3,
        compile_queue_capacity=3,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    mod = engine.load_wasm(wasm_bytes)
    pcs = [b.head_pc for b in mod.blocks]
    for pc in pcs:
        engine.jit_runtime.bitmap.touch(pc)
        engine.jit_runtime.bitmap.touch(pc)
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.HOT

    for pc in pcs:
        engine.jit_runtime.record_block_head(pc)
    engine.on_yield()

    assert len(engine.jit_runtime.compilation_pcs) == 3
    assert not engine.jit_runtime.has_pending_compilation()
    for pc in pcs:
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_compile_failure_unmarks_candidate_without_faking_compiled():
    """TEST-JITR-14: failed compilation permanently clears only candidate eligibility."""
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        card_shift=2,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    module = parse(
        wat_to_wasm(
            "(module (func (result i32) i32.const 11 f32.const 1.5 drop "
            "i64.const 70 drop f64.const 2.5 drop i32.const 1 i32.add return))"
        )
    )
    engine.register_module_blocks(module)
    block = module.blocks[0]
    pc = block.head_pc

    assert engine.jit_runtime.trackable.is_marked(pc)
    assert engine.jit_runtime.bitmap.touch(pc) == CardState.EXECUTED
    assert engine.jit_runtime.bitmap.touch(pc) == CardState.HOT
    engine.jit_runtime.record_block_head(pc)
    engine.jit_runtime.record_block_head(pc)
    engine.on_yield()

    assert engine.idle_hook(budget=1) == 0
    assert engine.jit_runtime.bitmap.get_state(pc) == CardState.HOT
    assert not engine.jit_runtime.trackable.is_marked(pc)
    assert not engine.jit_runtime.has_pending_compilation()
    assert not engine.jit_runtime.record_block_head(pc), (
        "a failed candidate must never be re-queued"
    )


def test_jitr_26_direct_mapped_folding_xor_jit_cache():
    """TEST-JITR-26 & GOTCHA-JITR-05: Native fixed-slot lookup and rotation invalidation."""
    cache = JitRuntimeBoundary(bank_capacity=1024)
    # Code-section-relative instruction PC used to exercise hash folding.
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
        return folded & (JIT_CACHE_FAST_SLOT_COUNT - 1)

    def slot_after_four_folds(pc: int) -> int:
        folded = pc ^ (pc >> 16)
        folded ^= folded >> 8
        folded ^= folded >> 4
        folded ^= folded >> 2
        return folded & (JIT_CACHE_FAST_SLOT_COUNT - 1)

    assert slot_after_three_folds(pc1) == slot_after_three_folds(pc2)
    assert slot_after_four_folds(pc1) != slot_after_four_folds(pc2)

    # The host lifetime registry retains resident descriptors. Fast slots
    # borrow them, so a collision or slot invalidation cannot retire a trace.
    cache.insert(t1)
    t2_refcount = sys.getrefcount(t2)
    cache.insert(t2)
    assert sys.getrefcount(t2) == t2_refcount + 1
    assert cache.lookup(pc1) is t1
    assert cache.lookup(pc2) is t2
    assert sys.getrefcount(t2) == t2_refcount + 1
    cache.flush_all()
    assert sys.getrefcount(t2) == t2_refcount

    # 2. Insert populates the native fast slot.
    cache.insert(t1)
    assert cache.lookup(pc1) is t1

    # 3. Lookup hits the fast slot.
    assert cache.lookup(pc1) is t1

    # 4. Rotation invalidates native slots (GOTCHA-JITR-05).
    cache.rotate()  # t1 moves to Warm
    assert cache.find_trace(pc1) is not None

    # 5. Lookup refills from Warm (without promotion).
    assert cache.lookup(pc1) is t1
    assert cache.promotions == 0
    assert cache.lookup(pc1) is t1

    # 6. Rotate again: t1 moves to Oldest.
    cache.rotate()
    assert cache.find_trace(pc1) is not None

    # 7. Lookup from Oldest promotes to Active and fills the fast slot.
    promoted = cache.lookup(pc1)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.find_trace(pc1) is not None
    assert cache.lookup(pc1) is t1

    # 8. Flush releases all native fast-slot references.
    cache.flush_all()
    assert cache.lookup(pc1) is None


def test_jitr_block_capacity_from_wasm_loader_and_no_set():
    """JITR: RuntimeEngine takes block capacity from WASM loader and strictly forbids set."""
    from tier2_runtime.wasm.reader import parse

    wat = "(module (func (i32.const 42) (return)) (func (i32.const 99) (return)))"
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        return
    mod = parse(wasm_bytes)

    # 1. WASM loader provides total_basic_blocks metadata and owns block_storage
    assert mod.total_basic_blocks == 2
    assert mod.block_storage is not None
    assert isinstance(mod.block_storage, ReadOnlyRadixBinaryTreeStorage)
    assert len(mod.block_storage.entries) == mod.total_basic_blocks
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
# 8. Native C++ dispatcher: compiled-trace and interpreter-handler integration.
# ===========================================================================


def test_jitr_br_if_loop_exit_jit_result_correct():
    """
    TEST-JITR-40: once a loop's br_if exit-condition block is compiled, the
    native C++ dispatcher must preserve its result through the Interpreter
    branch handler so the loop exits on the correct iteration.
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
    assert len(resident_module_traces(engine.jit_runtime.cache, module)) > 0, (
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
        if block.loops_to == block.head_pc and block.func_index == function_index
    )
    trace = compile_runtime_block(engine.jit_runtime, loop_block)
    assert trace is not None
    assert trace.next_pc is None
    assert trace.chain_next is None
    assert engine.jit_runtime.cache.insert(trace)
    interpreter = Interpreter(module)
    call_state = interpreter.start(function_index, [5])
    first_boundary = engine.run(interpreter, call_state)
    assert first_boundary.yield_requested
    assert first_boundary.call_state.current_pc() == loop_block.head_pc
    assert engine.stat_native_control_handlers >= 2
    assert engine.stat_jit_invocations >= 2
    assert engine.stat_native_dispatch_trace_transitions > 0
    assert first_boundary.call_state._frame is not None
    assert first_boundary.call_state._frame.context.loop_jump_count == 0

    results = engine.complete_call(interpreter, first_boundary.call_state)
    assert list(results) == [15]


def test_jitr_native_dispatch_snapshot_is_cached_per_hotspot_configuration():
    """Each JIT Runtime keeps the snapshot shape selected during composition."""
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), collect_runtime_stats=False)
    engine.register_module_blocks(module)
    function_index = module.export_func_index("sum")
    manager = engine.jit_runtime
    loop_block = next(
        block
        for block in module.blocks
        if block.loops_to == block.head_pc and block.func_index == function_index
    )

    initial_snapshot = manager.native_dispatch_state()
    cached_snapshot = manager.native_dispatch_state()
    assert cached_snapshot is initial_snapshot
    assert cached_snapshot.entries is initial_snapshot.entries
    assert cached_snapshot.trackable_mask is initial_snapshot.trackable_mask
    assert initial_snapshot.entry_count == 0
    assert initial_snapshot.trackable_card_count == manager.trackable.card_count
    assert ctypes.addressof(initial_snapshot.trackable_mask) == ctypes.addressof(
        (ctypes.c_uint8 * len(manager.trackable.storage.buffer)).from_buffer(
            manager.trackable.storage.buffer
        )
    )
    block_count = len(module.blocks)
    assert len(initial_snapshot.entries) == min(block_count, manager.cache.entry_capacity)
    assert len(initial_snapshot.trackable_mask) == len(manager.trackable.storage.buffer)
    assert initial_snapshot.arena_size == (
        len(initial_snapshot.entries) * ctypes.sizeof(NativeTraceDispatchEntry)
        + manager.history_capacity * ctypes.sizeof(ctypes.c_uint32)
    )
    arena_used = engine.bump_allocator.offset
    trace = compile_runtime_block(manager, loop_block)
    assert trace is not None and manager.cache.insert(trace)
    compiled_snapshot = manager.native_dispatch_state()
    assert compiled_snapshot.entry_count == 1
    assert compiled_snapshot is not initial_snapshot
    assert compiled_snapshot.trackable_card_count == initial_snapshot.trackable_card_count
    assert compiled_snapshot.entries[0].head_pc == loop_block.head_pc
    assert compiled_snapshot.entries[0].entry_address == trace.raw_addr
    assert engine.bump_allocator.offset == arena_used
    manager.unmark_trackable(loop_block.head_pc)
    excluded_snapshot = manager.native_dispatch_state()
    assert excluded_snapshot.trackable_card_count == initial_snapshot.trackable_card_count
    card = loop_block.head_pc >> excluded_snapshot.trackable_shift
    assert not (excluded_snapshot.trackable_mask[card >> 3] & (1 << (card & 7)))
    assert excluded_snapshot.arena_offset == initial_snapshot.arena_offset
    assert engine.bump_allocator.offset == arena_used
    manager.flush_all()
    flushed_snapshot = manager.native_dispatch_state()
    assert flushed_snapshot.entry_count == 0
    assert flushed_snapshot.arena_offset == initial_snapshot.arena_offset
    assert engine.bump_allocator.offset == arena_used

    steady_engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        collect_runtime_stats=False,
        hotspot_profiling_enabled=False,
    )
    steady_engine.register_module_blocks(module)
    steady_manager = steady_engine.jit_runtime
    steady_trace = compile_runtime_block(steady_manager, loop_block)
    assert steady_trace is not None and steady_manager.cache.insert(steady_trace)
    steady_snapshot = steady_manager.native_dispatch_state()
    assert steady_snapshot.entry_count == 1
    assert steady_snapshot.entries[0].head_pc == compiled_snapshot.entries[0].head_pc
    assert steady_snapshot.trackable_card_count == 0
    assert len(steady_snapshot.trackable_mask) == 0
    assert steady_manager.lookup(loop_block.head_pc) is steady_trace
    assert not steady_manager.record_block_head(loop_block.head_pc)
    assert not steady_manager.record_native_block_visits(steady_snapshot.block_history, 0)
    steady_manager.cache.rotate()
    steady_manager.cache.rotate()
    oldest_snapshot = steady_manager.native_dispatch_state()
    assert oldest_snapshot.entry_count == 1
    promotions_before = steady_manager.cache.promotions
    assert list(steady_engine.call(Interpreter(module), function_index, [5])) == [15]
    assert steady_manager.cache.promotions == promotions_before + 1


def test_jitr_native_compiler_scans_queued_traces_without_python_opcode_marshalling():
    """The Tier 3 extension passes original WASM bytes to the C++ compiler once per trace."""

    class RuntimeCompilerOnly(TraceCompiler):
        __slots__ = ()

        def compile_instructions(
            self,
            *,
            head_pc: int,
            instructions: Iterable[tuple[int, WasmOperand]],
            next_pc: int | None,
            loops_to: int | None,
            byte_length: int,
            local_layout: LocalWidthMap,
            context_helper: bool = False,
            helper_address: int = 0,
        ) -> JITTrace | None:
            raise AssertionError("runtime traces must not be decoded into Python opcode records")

    module = parse(
        wat_to_wasm('(module (func (export "add") (result i32) i32.const 19 i32.const 23 i32.add))')
    )
    engine = make_runtime_engine(
        jit_compiler=RuntimeCompilerOnly(),
        yield_threshold=1,
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)

    assert list(engine.call(interpreter, module.export_func_index("add"), [])) == [42]
    assert list(engine.call(interpreter, module.export_func_index("add"), [])) == [42]

    manager = engine.jit_runtime
    assert manager is not None
    head_pc = module.function_pc_offset(module.export_func_index("add"))
    trace = manager.cache.find_trace(head_pc)
    assert trace is not None and trace.raw_addr is not None
    assert manager.card_state(head_pc) == CardState.COMPILED


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
        if block.loops_to is not None and block.func_index == function_index
    )
    target_block = engine.get_block(branch_block.loops_to)
    assert target_block is not None
    assert target_block.frame_depth < branch_block.frame_depth
    trace = compile_runtime_block(engine.jit_runtime, branch_block)
    assert trace is not None
    assert trace.next_pc is None
    assert engine.jit_runtime.cache.insert(trace)

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
    assert first.call_state.current_pc() == loop_head_pc
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


def test_native_interpreter_with_jit_runtime_matches_interpreter_only_execution():
    """RuntimeEngine composes the C++ interpreter with native JIT compilation."""
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
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), yield_threshold=8)
    engine.register_module_blocks(module)
    native_interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        bump_allocator=engine.bump_allocator,
    )

    assert engine.call(native_interpreter, function_index, [50]) == expected
    assert engine.stat_jit_invocations > 0


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
    compiled_heads = {pc for pc, _ in resident_module_traces(engine.jit_runtime.cache, module)}
    assert len(compiled_heads) >= 2, (
        f"only {len(compiled_heads)} block(s) compiled ({[hex(pc) for pc in compiled_heads]}) -- "
        "the backward-branching loop body must compile too, not just the forward condition check"
    )


def _install_single_control_trace(
    engine: RuntimeEngine, module: Module, terminator: int, required_bytes: bytes = b""
) -> int:
    """対象traceだけを配置し、native実行件数を対象PCの実行証拠にする。"""
    manager = engine.jit_runtime
    assert manager is not None and not manager.hotspot_profiling_enabled
    candidates = []
    for block in module.blocks:
        function_index = block.func_index
        code = module.code_for(function_index)
        offset = block.head_pc - module.function_pc_offset(function_index)
        end = offset + block.byte_span
        if (
            end < len(code)
            and code[end] == terminator
            and required_bytes in bytes(code[offset:end])
        ):
            candidates.append(block)
    assert len(candidates) == 1, "fixture must identify one required control block"
    block = candidates[0]
    trace = compile_runtime_block(manager, block)
    assert trace is not None
    assert manager.cache.insert(trace)
    assert manager.cache.resident_count == 1
    assert manager.cache.find_trace(block.head_pc) is not None
    assert engine.stat_jit_invocations == 0
    return block.head_pc


def _assert_single_control_trace_executed(engine: RuntimeEngine, pc: int) -> None:
    manager = engine.jit_runtime
    assert manager is not None
    assert manager.cache.resident_count == 1 and manager.cache.find_trace(pc) is not None
    assert engine.stat_jit_invocations > 0, f"required native trace {pc:#x} was never executed"


def test_jitr_if_then_skipped_when_condition_false_after_jit():
    """TEST-JITR-33: IF直前のtraceだけを配置し、真偽両方の分岐結果を検査する。

    Native実行件数を対象PCの到達証拠にする。hotnessの生成規則は別の試験が扱う。
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
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(), yield_threshold=4, hotspot_profiling_enabled=False
    )
    engine.register_module_blocks(module)
    target_pc = _install_single_control_trace(engine, module, IF)
    interp = Interpreter(module)

    n = 20
    results = engine.call(interp, fn_idx, [n])
    expected = sum(abs(i - 5) for i in range(n))
    assert results == [expected], (
        f"abs_sum({n}) via JIT-driven RuntimeEngine.call() = {results}, expected [{expected}] -- "
        "an unconditionally-taken then-body (or an unconditionally-skipped one) throws this off"
    )
    _assert_single_control_trace_executed(engine, target_pc)


def test_jitr_nested_loop_in_if_frame_stack_reconciliation():
    """TEST-JITR-33: 内側loopの条件traceを実行し、制御frameと外側loopの結果を守る。"""
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
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(), yield_threshold=4, hotspot_profiling_enabled=False
    )
    engine.register_module_blocks(module)
    target_pc = _install_single_control_trace(
        engine, module, BR_IF, bytes.fromhex("20 02 41 03 4e")
    )
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
    _assert_single_control_trace_executed(engine, target_pc)


def test_jitr_return_terminated_block_jit_result_correct():
    """TEST-JITR-44: RETURN直前のtraceを実行し、共有stackから結果を返す。

    対象traceだけを配置する。Native実行件数と最終結果をともに検査する。
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
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=2,
        card_shift=2,
        hotspot_profiling_enabled=False,
    )
    engine.register_module_blocks(module)
    target_pc = _install_single_control_trace(engine, module, RETURN)
    interp = Interpreter(module)

    n = 20
    results = engine.call(interp, fn_idx, [n])
    assert results == [n * 3], (
        f"f({n}) via JIT-driven RuntimeEngine.call() = {results}, expected [{n * 3}]"
    )
    _assert_single_control_trace_executed(engine, target_pc)


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
    assert any(
        module.function_index_for_pc(pc) == 0
        for pc, _ in resident_module_traces(engine.jit_runtime.cache, module)
    ), "the repeatedly called callee should be eligible for JIT execution"


def test_jitr_hotspot_collection_continues_defined_calls_in_cpp():
    """TEST-JITR-68: collect callee visits without a Python boundary at each call."""
    wat = """
    (module
      (func $inc (param i32) (result i32)
        local.get 0
        i32.const 1
        i32.add
      )
      (func (export "sum_inc") (param $n i32) (result i32)
        (local $i i32) (local $sum i32)
        (block $exit
          (loop $top
            (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
            (local.set $sum
              (i32.add (local.get $sum) (call $inc (local.get $i))))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $top)
          )
        )
        local.get $sum
      )
    )
    """
    module = parse(wat_to_wasm(wat))
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        yield_threshold=64,
        card_shift=2,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    manager = engine.jit_runtime
    assert manager is not None
    callee_pc = next(block.head_pc for block in module.blocks if block.func_index == 0)
    assert manager.trackable.is_marked(callee_pc)

    function_index = module.export_func_index("sum_inc")
    interpreter = Interpreter(module)
    assert engine.call(interpreter, function_index, [8]) == [36]
    assert manager.cache.find_trace(callee_pc) is not None
    assert engine.stat_trace_exits_to_interp < 8, (
        "defined guest calls must not return to Python once per invocation"
    )
    engine.reset_stats()
    assert engine.call(interpreter, function_index, [8]) == [36]
    assert engine.stat_jit_invocations > 0
    assert engine.stat_trace_exits_to_interp < 8


def test_jitr_native_history_overwrite_does_not_yield():
    """TEST-JITR-69: overflow keeps recent visits without changing the execution boundary."""
    wat = """
    (module
      (func $inc (param i32) (result i32)
        local.get 0
        i32.const 1
        i32.add)
      (func (export "calls") (result i32)
        i32.const 1
        call $inc
        drop
        i32.const 2
        call $inc
        drop
        i32.const 3
        call $inc
        drop
        i32.const 4
        call $inc)
    )
    """
    module = parse(wat_to_wasm(wat))
    manager = JITRuntimeManager(
        jit_compiler=TraceCompiler(),
        yield_threshold=3,
        history_capacity=2,
        card_shift=2,
        candidate_threshold=0,
    )
    engine = RuntimeEngine(jit_runtime=manager)
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    call_state = interpreter.start(module.export_func_index("calls"), [])

    boundaries = 0
    while not call_state.finished:
        boundary = engine.run(interpreter, call_state)
        assert not boundary.yield_requested
        call_state = boundary.call_state
        boundaries += 1
        assert boundaries < 4

    assert call_state.results == [5]
    assert manager.exec_counter > manager.history_capacity
    assert manager.history_overwritten_count == manager.exec_counter - manager.history_capacity
    assert manager.last_history_analysis_approximate
    callee_pc = next(block.head_pc for block in module.blocks if block.func_index == 0)
    assert manager.card_state(callee_pc) == CardState.EXECUTED
    assert not manager.has_pending_compilation()


@pytest.mark.parametrize("collect_stats", (False, True))
@pytest.mark.parametrize(
    "body,expected",
    (
        ("(block (block nop)) i32.const 42", [42]),
        ("(block (result i32) (block (result i32) nop nop i32.const 42))", [42]),
        ("(block (result i32) (block (result i32) f64.const 3 drop i32.const 42))", [42]),
        ("f32.const 42 i32.trunc_sat_f32_s return", [42]),
        ("f32.const 42 i32.trunc_sat_f32_s i32.const 0 i32.add", [42]),
        ("i32.const 0 if (result i32) i32.const 42 else i32.const 17 end", [17]),
        ("i32.const 1 if (result i32) i32.const 42 else i32.const 17 end", [42]),
        ("call $helper", [42]),
        ("i32.const 0 i32.const 0 i32.const 0 memory.fill i32.const 42", [42]),
        ("i32.const 0 i32.const 0 i32.const 0 memory.copy i32.const 42", [42]),
        ("(block (block))", []),
    ),
)
def test_jitr_mask_card_collision_does_not_profile_structural_pc(body, expected, collect_stats):
    """TEST-JITR-29: control-only intervals never cause Loader lookups or visits."""

    class CountingManager(JITRuntimeManager):
        __slots__ = ("block_lookups",)

        def __init__(self) -> None:
            super().__init__(jit_compiler=TraceCompiler(), candidate_threshold=0)
            self.block_lookups = []

        def get_block(self, pc: int):
            self.block_lookups.append(pc)
            return super().get_block(pc)

    result_type = "(result i32)" if expected else ""
    helper = "(func $helper (result i32) (block (block)) i32.const 42)" if "call" in body else ""
    memory = "(memory 1)" if "memory." in body else ""
    module = parse(
        wasmtime.wat2wasm(f'(module {memory} (func (export "run") {result_type} {body}) {helper})')
    )
    manager = CountingManager()
    engine = RuntimeEngine(jit_runtime=manager, collect_runtime_stats=collect_stats)
    engine.register_module_blocks(module)
    # Deliberately mark every card, including control-only and NOP-only
    # intervals. Eligibility must come from execution, not an exact-PC list.
    base = module.function_pc_offset(0)
    for function_index in range(len(module.functions)):
        function_base = module.function_pc_offset(function_index)
        for pc in range(function_base, function_base + len(module.code_for(function_index))):
            manager.trackable.mark(pc)
    manager.block_lookups.clear()
    snapshot = manager.native_dispatch_state()
    interpreter = Interpreter(module)
    call = interpreter.start(0, [])
    native_result = dispatch_for_test(
        interpreter,
        call,
        snapshot,
        manager.yield_threshold,
        manager.exec_counter,
        native_dispatcher=select_native_dispatch_entry(collect_stats, True),
    )
    visits = native_result[5]
    history = native_result[7]
    assert native_result[0] == 1
    interpreter.step_native(call)
    assert call.finished
    assert call.results == expected
    assert call.context.runtime_flags == 0
    if " if " in body:
        # Both arms are registered; only the selected arm may be recorded.
        selected_offset = bytes(module.code_for(0)).index(bytes((I32_CONST, expected[0])))
        expected_heads = (base, base + selected_offset)
    elif "call" in body:
        expected_heads = tuple(block.head_pc for block in module.blocks if block.func_index == 1)
    else:
        expected_heads = tuple(block.head_pc for block in module.blocks)
    assert tuple(history[index] for index in range(visits)) == expected_heads
    manager.record_native_block_visits(history, visits)
    assert manager.exec_counter == len(expected_heads)
    assert manager.block_lookups == []
    # Compilation also uses the registered native Loader descriptors.
    manager.record_native_block_visits(history, visits)
    manager.record_native_block_visits(history, visits)
    manager.on_interpreter_exit(False)
    assert manager.block_lookups == []
    if expected_heads:
        assert manager.has_pending_compilation()
        manager.idle_hook(budget=len(expected_heads))
        assert manager.block_lookups == []
    else:
        assert not manager.has_pending_compilation()


@pytest.mark.parametrize("collect_stats", (False, True))
@pytest.mark.parametrize(
    "body",
    (
        "memory.size drop i32.const 42",
        "i32.const 0 memory.grow drop i32.const 42",
        "(block memory.size drop) i32.const 42",
        "memory.size drop call $host i32.const 42",
        "memory.size drop i32.const 0 call_indirect (type $host_type) i32.const 42",
        "memory.size drop i32.const 0 i32.const 0 i32.const 0 memory.fill i32.const 42",
    ),
)
def test_jitr_memory_boundary_continuation_does_not_become_a_block_head(body, collect_stats):
    """TEST-JITR-29: a memory-helper return keeps the existing block observation."""
    function_index = 1 if "call" in body else 0
    imports = (
        (
            '(type $host_type (func)) (import "env" "noop" (func $host (type $host_type)))'
            "(table 1 funcref) (elem (i32.const 0) $host)"
        )
        if function_index
        else ""
    )
    host_functions = StaticVector.of((lambda: None,), capacity=1) if function_index else None
    module = parse(
        wasmtime.wat2wasm(
            f'(module {imports} (memory 1) (func (export "run") (result i32) {body}))'
        )
    )
    manager = JITRuntimeManager(jit_compiler=TraceCompiler(), candidate_threshold=0)
    engine = RuntimeEngine(jit_runtime=manager, collect_runtime_stats=collect_stats)
    engine.register_module_blocks(module)
    base = module.function_pc_offset(function_index)
    for pc in range(base, base + len(module.code_for(function_index))):
        manager.trackable.mark(pc)
    snapshot = manager.native_dispatch_state()
    interpreter = Interpreter(module, host_functions=host_functions)
    call = interpreter.start(function_index, [])
    recorded = []
    while True:
        result = dispatch_for_test(
            interpreter,
            call,
            snapshot,
            manager.yield_threshold,
            manager.exec_counter,
            native_dispatcher=select_native_dispatch_entry(collect_stats, True),
        )
        visits, history = result[5], result[7]
        recorded.extend(history[index] for index in range(visits))
        manager.record_native_block_visits(history, visits)
        if result[0] == 1:
            break
        assert result[0] == 0
        interpreter.resolve_native_call_boundary(call)
        assert len(recorded) <= len(module.blocks)
    interpreter.step_native(call)
    assert call.finished and call.results == [42]
    assert call.context.runtime_flags == 0
    expected_heads = tuple(block.head_pc for block in module.blocks)
    assert tuple(recorded) == expected_heads
    assert manager.exec_counter == len(expected_heads)


@pytest.mark.parametrize("collect_stats", (False, True))
def test_jitr_native_step_consumes_pending_head_before_memory_fallback(collect_stats):
    """TEST-JITR-29: switching native APIs cannot profile a memory continuation as a head."""
    module = parse(
        wasmtime.wat2wasm(
            '(module (memory 1) (func (export "run") (result i32) '
            "memory.size drop (block nop) i32.const 42))"
        )
    )
    manager = JITRuntimeManager(jit_compiler=TraceCompiler(), candidate_threshold=0)
    engine = RuntimeEngine(jit_runtime=manager, collect_runtime_stats=collect_stats)
    engine.register_module_blocks(module)
    base = module.function_pc_offset(0)
    for pc in range(base, base + len(module.code_for(0))):
        manager.trackable.mark(pc)
    interpreter = Interpreter(module)
    call = interpreter.start(0, [])
    assert call._frame is not None
    call._frame.set_runtime_boundary(base + len(module.code_for(0)) - 1, None)
    call.context.runtime_flags |= EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
    assert not interpreter._try_native_step_to_boundary(call)
    assert call.context.runtime_flags == 0
    interpreter.resolve_native_call_boundary(call)
    assert call._ip > 0
    result = dispatch_for_test(
        interpreter,
        call,
        manager.native_dispatch_state(),
        manager.yield_threshold,
        manager.exec_counter,
        native_dispatcher=select_native_dispatch_entry(collect_stats, True),
    )
    assert result[0] == 1
    expected_heads = tuple(block.head_pc for block in module.blocks if block.head_pc != base)
    assert expected_heads
    assert tuple(result[7][index] for index in range(result[5])) == expected_heads
    interpreter.step_native(call)
    assert call.finished and call.results == [42]
    assert call.context.runtime_flags == 0


def test_jitr_memory_helper_trap_clears_block_continuation():
    """TEST-JITR-29: a failed runtime memory operation cannot retain resume state."""
    module = parse(wasmtime.wat2wasm("(module (memory 1) (func i32.const -1 i32.load drop))"))
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), candidate_threshold=0)
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    call = interpreter.start(0, [])
    result = dispatch_for_test(
        interpreter,
        call,
        engine.jit_runtime.native_dispatch_state(),
        engine.yield_threshold,
        0,
        native_dispatcher=select_native_dispatch_entry(True, True),
    )
    assert result[0] == 0
    assert call.context.runtime_flags == 0
    interpreter.resolve_native_call_boundary(call)
    assert call.finished and call.trap is not None
    assert call.results is None
    assert call.context.runtime_flags == 0


def test_jitr_empty_yield_skips_python_control_work():
    """TEST-JITR-70: empty yields avoid Python JIT work while preserving the result."""

    class CountingManager(JITRuntimeManager):
        __slots__ = ("idle_calls", "lookup_calls", "record_calls", "snapshot_calls")

        def __init__(self) -> None:
            super().__init__(yield_threshold=2, candidate_threshold=1_000_000)
            self.idle_calls = 0
            self.lookup_calls = 0
            self.record_calls = 0
            self.snapshot_calls = 0

        def idle_hook(self, budget: int = 4) -> int:
            self.idle_calls += 1
            return super().idle_hook(budget)

        def lookup(self, pc: int) -> JITTrace | None:
            self.lookup_calls += 1
            return super().lookup(pc)

        def record_native_block_visits(
            self, visits: NativeBlockVisitHistory, total_visits: int
        ) -> bool:
            self.record_calls += 1
            return super().record_native_block_visits(visits, total_visits)

        def native_dispatch_state(self) -> NativeDispatchSnapshot:
            self.snapshot_calls += 1
            return super().native_dispatch_state()

    module = parse(
        wat_to_wasm(
            """
            (module
              (func (export "sum") (param $n i32) (result i32)
                (local $sum i32)
                (loop $top
                  (local.set $sum (i32.add (local.get $sum) (local.get $n)))
                  (local.set $n (i32.sub (local.get $n) (i32.const 1)))
                  (br_if $top (local.get $n)))
                local.get $sum))
            """
        )
    )
    manager = CountingManager()
    engine = RuntimeEngine(jit_runtime=manager)
    engine.register_module_blocks(module)

    assert engine.call(Interpreter(module), module.export_func_index("sum"), [5]) == [15]
    assert manager.snapshot_calls == 0
    assert manager.idle_calls == 1
    assert manager.record_calls == 0
    assert manager.lookup_calls == 0
    assert not manager.has_pending_compilation()


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
    interpreter_engine = make_runtime_engine(bump_allocator=module.allocator)
    interpreter_engine.register_module_blocks(module)
    jit_engine = make_runtime_engine(
        jit_compiler=TraceCompiler(), yield_threshold=4, bump_allocator=module.allocator
    )
    jit_module = jit_engine.load_wasm(wat_to_wasm(wat))
    interpreter = Interpreter(module)
    jit_interpreter = Interpreter(jit_module)

    inputs = (0, 1, 2, 17, 30)
    expected = [sum(i if i % 2 == 0 else -i for i in range(n)) for n in inputs]
    reference = [list(interpreter_engine.call(interpreter, function_index, [n])) for n in inputs]
    actual = [list(jit_engine.call(jit_interpreter, function_index, [n])) for n in inputs]

    assert reference == [[value] for value in expected]
    assert actual == reference
    assert jit_engine.stat_jit_invocations > 0


def test_jitr_br_table_uses_native_handler_and_preserves_every_target():
    """TEST-JITR-50: BR_TABLE uses its C++ handler for every target."""
    from tier2_runtime.interpreter.control_flow import iter_scan_instrs

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
    interpreter_engine = make_runtime_engine(bump_allocator=module.allocator)
    interpreter_engine.register_module_blocks(module)
    jit_engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        bump_allocator=module.allocator,
        yield_threshold=1,
        card_shift=0,
        candidate_threshold=0,
        min_trace_bytes=1,
    )
    jit_module = jit_engine.load_wasm(wat_to_wasm(wat))
    table_offset = next(
        (
            instruction.offset
            for instruction in iter_scan_instrs(module.code_for(0))
            if instruction.opcode == BR_TABLE
        ),
        None,
    )
    assert table_offset is not None
    table_pc = module.function_pc_offset(0) + table_offset
    assert jit_engine.get_block(table_pc) is None
    predecessor = next(
        (block for block in module.blocks if block.next_pc == table_pc),
        None,
    )
    assert predecessor is not None
    trace = compile_runtime_block(jit_engine.jit_runtime, predecessor)
    assert trace is not None
    assert trace.next_pc is None
    assert trace.loops_to is None

    interpreter = Interpreter(module)
    jit_interpreter = Interpreter(jit_module)
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
    assert compile_runtime_block(engine.jit_runtime, mixed_block) is None
    result = engine.call(Interpreter(module), 0, [])

    assert list(result) == [12]
    assert engine.stat_jit_invocations == 0
    assert len(resident_module_traces(engine.jit_runtime.cache, module)) == 0


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
    compiled_heads = {pc for pc, _ in resident_module_traces(engine.jit_runtime.cache, module)}

    assert actual == [[12]] * 6
    assert module.function_pc_offset(1) in compiled_heads
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
    call_block = engine.get_block(module.function_pc_offset(1))
    assert call_block is not None
    assert compile_runtime_block(engine.jit_runtime, call_block) is None

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
    """Synthetic Code-section PC for a test-only function and card (card = 8 * i)."""
    return PC_ONLY_FUNCTION_BASE + func * PC_ONLY_FUNCTION_STRIDE + (0x20 * i)


def _aging_engine(counts, **engine_kwargs):
    """RuntimeEngine over a PC-only module with `counts[f]` blocks in function f."""
    # The expectations below fix the sweep parameters; they do not follow the tuned defaults.
    engine_kwargs.setdefault("aging_step_units", 1)
    engine_kwargs.setdefault("aging_scan_bytes", 16)
    engine = make_runtime_engine(**engine_kwargs)
    module = make_pc_only_functions_module(
        tuple(tuple(0x20 * i for i in range(count)) for count in counts)
    )
    engine.register_module_blocks(module)
    return engine, module


def _touch_via_yield(engine, pc):
    """Record one block head and let the yield handler touch its card."""
    engine.jit_runtime.trackable.mark(pc)
    engine.jit_runtime.record_block_head(pc)
    engine.on_yield()


def _aging_full_lap(engine):
    # Every step processes at least one non-zero byte while any exists.
    for _ in range(engine.jit_runtime.update_bitmap.unit_count):
        engine.jit_runtime.age_step()


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
    queued = engine.jit_runtime.has_pending_compilation()

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
    assert engine.jit_runtime.has_pending_compilation() == queued
    for pc in (_pc(0), _pc(1), _pc(2), _pc(3), _pc(0, 1)):
        card = engine.jit_runtime.bitmap.card_of(pc)
        assert not engine.jit_runtime.update_bitmap.is_marked(card)
    # A decayed card restarts its warm-up: one touch gives EXECUTED, not HOT.
    _touch_via_yield(engine, _pc(0))
    assert engine.jit_runtime.bitmap.get_state(_pc(0)) == CardState.EXECUTED


def test_jitr_update_bitmap_covers_every_executed_card():
    """TEST-JITR-17: a clear dirty-card bit implies that code-region card is not EXECUTED."""
    engine, _module = _aging_engine((2,) * 20)
    observed_cards: StaticVector[int] = StaticVector(capacity=400)
    seed = 12345
    for _ in range(400):
        seed = (seed * 1103515245 + 12345) & 0x7FFF_FFFF
        if seed % 5 == 0:
            engine.jit_runtime.age_step()
        else:
            pc = _pc((seed >> 4) % 2, (seed >> 8) % 20)
            card = engine.jit_runtime.bitmap.card_of(pc)
            if not observed_cards.contains(card):
                observed_cards.append(card)
            _touch_via_yield(engine, pc)
        for card in observed_cards:
            if not engine.jit_runtime.update_bitmap.is_marked(card):
                assert engine.jit_runtime.bitmap.get_card_state(card) != CardState.EXECUTED, (
                    f"code-region card {card} is EXECUTED but its dirty bit is clear"
                )


def test_jitr_aging_advances_once_per_rotation():
    """TEST-JITR-18: only a bank rotation (explicit or caused by a full bank) advances the sweep."""
    fail_pc = _pc(0, 4)
    engine, _module = _aging_engine((1,) * 41, aging_scan_bytes=11000)
    first_dirty_pc = _pc(0, 24)
    second_dirty_pc = _pc(0, 40)
    first_dirty_byte = engine.jit_runtime.bitmap.card_of(first_dirty_pc) // 8
    second_dirty_byte = engine.jit_runtime.bitmap.card_of(second_dirty_pc) // 8
    _touch_via_yield(engine, first_dirty_pc)
    _touch_via_yield(engine, second_dirty_pc)

    assert engine.idle_hook() == 0  # empty queue
    _touch_via_yield(engine, _pc(0, 5))
    _touch_via_yield(engine, _pc(0, 5))  # discarded without a compiler
    assert engine.idle_hook() == 0
    engine.jit_runtime.bitmap.mark_compiled(_pc(0, 0))  # COMPILED-card skip
    _touch_via_yield(engine, _pc(0, 0))
    assert engine.idle_hook() == 0
    _touch_via_yield(engine, fail_pc)
    _touch_via_yield(engine, fail_pc)
    assert engine.idle_hook() == 0
    assert engine.jit_runtime.aging_steps == 0 and engine.jit_runtime.update_bitmap.cursor == 0, (
        "compilation, whatever its outcome, does not age"
    )

    engine.jit_runtime.cache.flush_all()
    assert engine.jit_runtime.aging_steps == 0, "an explicit flush is not a rotation"

    # HOT transitions retain their dirty bits until the sweep visits them.
    # Those earlier bytes consume a unit without decaying the HOT cards.
    for step, pc in enumerate((fail_pc, _pc(0, 5)), start=1):
        engine.jit_runtime.cache.rotate()
        assert engine.jit_runtime.aging_steps == step
        assert (
            engine.jit_runtime.update_bitmap.cursor
            == engine.jit_runtime.bitmap.card_of(pc) // 8 + 1
        )
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.HOT
        assert not engine.jit_runtime.update_bitmap.is_marked(engine.jit_runtime.bitmap.card_of(pc))
        assert engine.jit_runtime.bitmap.get_state(first_dirty_pc) == CardState.EXECUTED
        assert engine.jit_runtime.bitmap.get_state(second_dirty_pc) == CardState.EXECUTED

    engine.jit_runtime.cache.rotate()
    assert engine.jit_runtime.aging_steps == 3
    assert engine.jit_runtime.update_bitmap.cursor == first_dirty_byte + 1
    assert engine.jit_runtime.bitmap.get_state(first_dirty_pc) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(second_dirty_pc) == CardState.EXECUTED, (
        "the later dirty byte lies beyond this step"
    )
    engine.jit_runtime.cache.rotate()
    assert engine.jit_runtime.aging_steps == 4
    expected_cursor = (
        0
        if second_dirty_byte + 1 == engine.jit_runtime.update_bitmap.unit_count
        else second_dirty_byte + 1
    )
    assert engine.jit_runtime.update_bitmap.cursor == expected_cursor
    assert engine.jit_runtime.bitmap.get_state(second_dirty_pc) == CardState.UNEXECUTED

    inserted = 0
    while engine.jit_runtime.aging_steps == 4:  # a full bank rotates by itself
        assert inserted < 30
        engine.jit_runtime.cache.insert(JITTrace(_pc(0, 6 + inserted), lambda: 0, size_bytes=512))
        inserted += 1
    assert engine.jit_runtime.aging_steps == 5 and inserted >= 2


def test_jitr_aging_cursor_wraps_and_bounds_each_step():
    """TEST-JITR-19: zero bytes do not count, a step ends at N units or O scanned bytes, the cursor wraps."""
    engine, _module = _aging_engine((18,), aging_scan_bytes=32)
    first_pc = _pc(0)
    last_pc = _pc(17)
    first_byte = engine.jit_runtime.bitmap.card_of(first_pc) // 8
    _touch_via_yield(engine, first_pc)
    _touch_via_yield(engine, last_pc)
    assert engine.jit_runtime.age_step() == 1
    assert engine.jit_runtime.update_bitmap.cursor == first_byte + 1
    assert engine.jit_runtime.bitmap.get_state(first_pc) == CardState.UNEXECUTED
    assert engine.jit_runtime.bitmap.get_state(last_pc) == CardState.EXECUTED
    assert engine.jit_runtime.age_step() == 1, (
        "the sweep skips zero bytes before the later dirty card"
    )
    assert engine.jit_runtime.update_bitmap.cursor == 0, "the cursor wraps at the region end"
    assert engine.jit_runtime.bitmap.get_state(last_pc) == CardState.UNEXECUTED
    scanned_before = engine.jit_runtime.aging_bytes_scanned
    assert engine.jit_runtime.age_step() == 0, "nothing is dirty"
    assert engine.jit_runtime.aging_bytes_scanned - scanned_before == (
        engine.jit_runtime.update_bitmap.unit_count
    ), "one full pass, then the step ends"
    assert engine.jit_runtime.update_bitmap.cursor == 0, (
        "a full pass returns the cursor to where it started"
    )
    assert engine.jit_runtime.aging_units_processed == 2

    # Two non-zero bytes per step; the zero byte in between does not count.
    wide, _ = _aging_engine((18,), aging_step_units=2, aging_scan_bytes=32)
    _touch_via_yield(wide, _pc(0))
    _touch_via_yield(wide, _pc(17))
    assert wide.jit_runtime.age_step() == 2 and wide.jit_runtime.aging_units_processed == 2
    assert wide.jit_runtime.update_bitmap.cursor == 0, (
        "the step ended after processing the last dirty byte and wrapping"
    )
    assert wide.jit_runtime.bitmap.get_state(_pc(0)) == CardState.UNEXECUTED
    assert wide.jit_runtime.bitmap.get_state(_pc(17)) == CardState.UNEXECUTED

    # The scan-byte limit ends a step even when no unit was processed.
    limited, _ = _aging_engine((170,), aging_step_units=100, aging_scan_bytes=4)
    limited_pc = _pc(160)
    target_byte = limited.jit_runtime.bitmap.card_of(limited_pc) // 8
    _touch_via_yield(limited, limited_pc)
    while limited.jit_runtime.update_bitmap.cursor < target_byte:
        assert limited.jit_runtime.age_step() == 0, (
            "a limited scan must not reach the dirty byte yet"
        )
        assert limited.jit_runtime.update_bitmap.cursor <= target_byte
    assert limited.jit_runtime.aging_bytes_scanned == target_byte
    assert limited.jit_runtime.age_step() == 1, (
        "the dirty byte is processed in its own bounded window"
    )
    assert limited.jit_runtime.update_bitmap.cursor == target_byte + 4, (
        "the scan-byte limit advances the cursor across four code-region bytes"
    )
    assert limited.jit_runtime.bitmap.get_state(limited_pc) == CardState.UNEXECUTED


def test_jitr_aging_scans_region_card_bytes_and_ignores_import_code():
    """TEST-JITR-19: dirty-card bytes drive bounded aging over the module code region."""
    engine, _module = _aging_engine((2, 2, 0, 0, 0, 0, 0, 0, 0, 1), aging_scan_bytes=3000)
    pcs = (_pc(1, 0), _pc(0, 1), _pc(0, 9))
    for pc in pcs:
        _touch_via_yield(engine, pc)
    dirty_cards = {engine.jit_runtime.bitmap.card_of(pc) for pc in pcs}
    assert len(dirty_cards) == len(pcs)
    decayed = 0
    for _ in range(engine.jit_runtime.update_bitmap.unit_count):
        if not any(engine.jit_runtime.update_bitmap.is_marked(card) for card in dirty_cards):
            break
        decayed += engine.jit_runtime.age_step()
    assert decayed == len(dirty_cards)
    assert all(not engine.jit_runtime.update_bitmap.is_marked(card) for card in dirty_cards)
    for pc in pcs:
        assert engine.jit_runtime.bitmap.get_state(pc) == CardState.UNEXECUTED

    wat = """
    (module
      (import "env" "h" (func $h))
      (func (export "a") (i32.const 1) (drop) (i32.const 2) (drop))
      (func (export "b") (i32.const 3) (drop) (i32.const 4) (drop))
    )
    """
    wasm_engine = make_runtime_engine(
        aging_step_units=100,
        aging_scan_bytes=3000,
    )
    module = wasm_engine.load_wasm(bytes(wasmtime.wat2wasm(wat)))
    update = wasm_engine.jit_runtime.update_bitmap
    bitmap = wasm_engine.jit_runtime.bitmap
    assert update.card_count == bitmap.card_count
    pc_a = module.blocks[0].head_pc
    pc_b = module.blocks[len(module.blocks) - 1].head_pc
    assert module.function_index_for_pc(pc_a) == 1
    assert module.function_index_for_pc(pc_b) == 2
    assert module.function_index_for_pc(0) is None, "the imported function owns no code PC"
    _touch_via_yield(wasm_engine, pc_a)
    _touch_via_yield(wasm_engine, pc_b)
    card_a = bitmap.card_of(pc_a)
    card_b = bitmap.card_of(pc_b)
    assert card_a != card_b
    assert update.is_marked(card_a) and update.is_marked(card_b)
    assert wasm_engine.jit_runtime.age_step() == 2
    assert wasm_engine.jit_runtime.bitmap.get_state(pc_a) == CardState.UNEXECUTED
    assert wasm_engine.jit_runtime.bitmap.get_state(pc_b) == CardState.UNEXECUTED


def test_gotcha_jitr_09_aging_never_drops_compiled_or_hot():
    """GOTCHA-JITR-09: the sweep keeps a resident trace's card COMPILED and a queued card HOT."""
    pc_res, pc_hot, pc_exec = _pc(0), _pc(1), _pc(2)
    engine, _module = _aging_engine((3,))
    trace = JITTrace(head_pc=pc_res, native_fn=lambda: 1, size_bytes=64)
    assert engine.jit_runtime.cache.insert(trace)
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
    assert engine.jit_runtime.has_pending_compilation(), "the pending request keeps its HOT card"
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
    trace = compiler.compile_instructions(
        head_pc=head_pc,
        instructions=((LOCAL_GET, 0), (I32_CONST, 1 << index), (I32_ADD, None), (LOCAL_SET, 0)),
        next_pc=next_pc,
        loops_to=None,
        byte_length=12,
        local_layout=LocalWidthMap((I32,)),
    )
    assert trace is not None
    return trace


def _chain_stress_check_links(cache: JitRuntimeBoundary) -> None:
    """Every chain pointer, Python and native, must name a resident trace's live entry."""
    observed = 0
    for index in range(CHAIN_STRESS_TRACES):
        pc = CHAIN_STRESS_HEAD + index * CHAIN_STRESS_STRIDE
        trace = cache.find_trace(pc)
        if trace is None:
            continue
        observed += 1
        assert trace.head_pc == pc
        assert trace.raw_addr is not None and trace.code_offset is not None
        native = int.from_bytes(
            ctypes.string_at(
                trace.raw_addr - JIT_TRACE_HEADER_BYTES + JIT_X64_CHAIN_TARGET_OFFSET, 8
            ),
            "little",
        )
        assert native == trace.header.chain_target_addr, "native header diverged from Python"
        if trace.chain_next is None:
            assert native == 0, f"unchained trace {pc:#x} keeps a native jump"
            continue
        index = (pc - CHAIN_STRESS_HEAD) // CHAIN_STRESS_STRIDE
        assert index < CHAIN_STRESS_TRACES - 1
        assert trace.chain_next == CHAIN_STRESS_HEAD + (index + 1) * CHAIN_STRESS_STRIDE
        target = cache.find_trace(trace.chain_next)
        assert target is not None, f"{pc:#x} chains into evicted {trace.chain_next:#x}"
        assert target.raw_addr is not None
        assert native == target.raw_addr + TRACE_ENTRY_STUB_BYTES, (
            f"{pc:#x} jumps to a stale entry of {trace.chain_next:#x}"
        )

    assert observed == cache.resident_count, "duplicate or unexpected resident PC"


def _chain_stress_run(cache: JitRuntimeBoundary, start: JITTrace) -> None:
    """Run natively; derive successors and effects from the generated linear chain input."""
    expected_mask = 0
    walked = start
    while True:
        expected_mask |= 1 << ((walked.head_pc - CHAIN_STRESS_HEAD) // CHAIN_STRESS_STRIDE)
        if walked.chain_next is None:
            break
        index = (walked.head_pc - CHAIN_STRESS_HEAD) // CHAIN_STRESS_STRIDE
        expected_next = CHAIN_STRESS_HEAD + (index + 1) * CHAIN_STRESS_STRIDE
        assert walked.chain_next == expected_next
        successor = cache.find_trace(expected_next)
        assert successor is not None
        walked = successor
    context = ExecutionContext()
    assert context.local_stack.extend((0,))
    start.execute(context.context_ptr, context.sp_ptr, context.locals_ptr, 0)
    assert context.local_stack[0] == expected_mask, (
        f"native chain ran {context.local_stack[0]:#x}, chain pointers say {expected_mask:#x}"
    )
    final_index = (walked.head_pc - CHAIN_STRESS_HEAD) // CHAIN_STRESS_STRIDE
    expected_ip = (
        CHAIN_STRESS_EXIT_PC
        if final_index == CHAIN_STRESS_TRACES - 1
        else CHAIN_STRESS_HEAD + (final_index + 1) * CHAIN_STRESS_STRIDE
    )
    assert context.ip == expected_ip


def test_jitr_62_chain_links_stay_valid_across_rotation_and_promotion():
    """
    TEST-JITR-62: randomized insert / promote / rotate sequences over a chain of
    traces keep every chain pointer on a live entry.

    After every step, each `chain_next` names a resident trace, the Python header and the
    native header agree, and no trace is resident in two banks.  Every trace looked up is
    then run natively; effects match the generated logical successor sequence, so a
    jump to a promoted trace's old code cannot pass.
    """
    import random

    compiler = TraceCompiler()
    promoted_total = 0
    evicted_total = 0
    chained_runs = 0
    for seed in range(40):
        rng = random.Random(seed)
        cache = JitRuntimeBoundary(bank_capacity=512)
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
                    promotions = cache.promotions
                    assert cache.lookup(pc) is found
                    assert cache.promotions == promotions, "promotion must not repeat"
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


@pytest.mark.parametrize("collect_stats", [False, True])
@pytest.mark.parametrize("collect_hotspots", [False, True])
def test_jitr_trace_execution_counts_loop_bodies_and_survives_promotion(
    collect_stats, collect_hotspots
):
    """TEST-JITR-72: body counts survive dispatch rebuilds and Oldest promotion."""
    module = parse(
        wat_to_wasm("""(module
      (func (export "sum") (param i32) (result i32) (local i32)
        (loop $loop
          local.get 1 local.get 0 i32.add local.set 1
          local.get 0 i32.const 1 i32.sub local.tee 0 br_if $loop)
        local.get 1))""")
    )
    retired = []
    engine = make_runtime_engine(
        retire_observer=lambda pc, count: retired.append((pc, count)),
        jit_compiler=TraceCompiler(),
        collect_runtime_stats=collect_stats,
        hotspot_profiling_enabled=collect_hotspots,
    )
    engine.register_module_blocks(module)
    manager = engine.jit_runtime
    block = next(b for b in module.blocks if b.loops_to == b.head_pc)
    trace = compile_runtime_block(manager, block)
    assert trace is not None and manager.cache.insert(trace)
    snapshot = manager.native_dispatch_state()
    assert snapshot.entries[0].exec_count == trace.exec_count_address
    assert trace.exec_count == 0
    assert manager.lookup(trace.head_pc) is trace
    assert trace.exec_count == 0
    interpreter = Interpreter(module)
    function = module.export_func_index("sum")
    assert engine.call(interpreter, function, [5]) == [15]
    assert trace.exec_count == 5
    manager.cache.rotate()
    manager.cache.rotate()
    promotions_before = manager.cache.promotions
    assert manager.cache.find_trace(trace.head_pc) is trace
    assert engine.call(interpreter, function, [2]) == [3]
    assert trace.exec_count == 7
    assert manager.cache.promotions == promotions_before + 1
    assert manager.cache.find_trace(trace.head_pc) is trace
    assert manager.cache.promotions == 1
    assert manager.native_dispatch_state() is not snapshot
    assert trace.exec_count_address == snapshot.entries[0].exec_count
    trace.exec_count = 0xFFFF_FFFE
    assert engine.call(interpreter, function, [4]) == [10]
    assert trace.exec_count == 0xFFFF_FFFF
    engine.reset_stats()
    assert trace.exec_count == 0
    assert engine.call(interpreter, function, [3]) == [6]
    assert trace.exec_count == 3
    manager.flush_all()
    assert [count for pc, count in retired if pc == trace.head_pc] == [3]
    replacement = compile_runtime_block(manager, block)
    assert replacement is not None and manager.cache.insert(replacement)
    assert replacement.exec_count == 0
    assert engine.call(interpreter, function, [2]) == [3]
    assert replacement.exec_count == 2 and trace.exec_count == 3


@pytest.mark.parametrize("collect_stats", [False, True])
def test_jitr_trace_execution_counts_include_direct_chain_successors(collect_stats):
    """TEST-JITR-73: chained bodies count without their own dispatcher lookup."""
    module = parse(
        wat_to_wasm("""(module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0 i32.const 5 i32.add local.set 0 br $b)
        local.get 0 i32.const 2 i32.mul local.set 0 local.get 0 return))""")
    )
    engine = make_runtime_engine(
        jit_compiler=TraceCompiler(),
        collect_runtime_stats=collect_stats,
        hotspot_profiling_enabled=False,
    )
    engine.register_module_blocks(module)
    manager = engine.jit_runtime
    source = compile_module_block(manager.jit_compiler, module, module.blocks[0])
    target = compile_module_block(manager.jit_compiler, module, module.blocks[1])
    assert manager.cache.insert(target)
    assert manager.cache.insert(source)
    assert source.chain_next == target.head_pc
    assert engine.call(Interpreter(module), 0, [10]) == [30]
    assert source.exec_count == target.exec_count == 1
    if collect_stats:
        assert engine.stat_jit_invocations == 2


def test_jitr_trace_execution_retirement_records_zero_and_used_traces():
    """TEST-JITR-74: report counts before purge, flush and descriptor replacement."""
    records = []
    cache = JitRuntimeBoundary(retire_observer=lambda pc, count: records.append((pc, count)))
    used = JITTrace(0x100)
    used.exec_count = 7
    unused = JITTrace(0x200)
    assert cache.insert(used) and cache.insert(unused)
    cache.rotate()
    cache.rotate()
    assert cache.lookup(used.head_pc) is used
    assert used.exec_count == 7 and records == []
    cache.rotate()
    assert records == [(unused.head_pc, 0)]
    assert cache.find_trace(unused.head_pc) is None
    cache.rotate()
    assert cache.lookup(used.head_pc) is used
    replacement = JITTrace(used.head_pc)
    assert cache.insert(replacement)
    assert records == [(unused.head_pc, 0), (used.head_pc, 7)]
    assert replacement.exec_count == 0
    cache.flush_all()
    assert records == [(unused.head_pc, 0), (used.head_pc, 7), (replacement.head_pc, 0)]
    cache.flush_all()
    assert len(records) == 3


def test_jitr_trace_execution_count_excludes_pre_entry_stack_fallback():
    """TEST-JITR-75: a resident trace declined before execution keeps count zero."""
    module = parse(
        wat_to_wasm("""(module (func (export "f") (result i32)
        i32.const 2 i32.const 3 i32.add))""")
    )
    engine = make_runtime_engine(jit_compiler=TraceCompiler(), hotspot_profiling_enabled=False)
    engine.register_module_blocks(module)
    manager = engine.jit_runtime
    block = module.blocks[0]
    trace = compile_runtime_block(manager, block)
    assert trace is not None
    trace.stack_words = 0xFFFF
    assert manager.cache.insert(trace)
    assert engine.call(Interpreter(module), 0, []) == [5]
    assert trace.exec_count == 0
    assert engine.stat_jit_invocations == 0


if __name__ == "__main__":
    test_hotspot_01_2bit_card_marking_state_transitions()
    test_jitr_01_card_marking_granularity()
    test_hotspot_02_history_ring_buffered_yield_drain()
    test_hotspot_03_lifo_compile_queue_batch_drain()
    test_hotspot_04_3bank_cache_oldest_only_promotion()
    test_jitr_cache_lookup_accepts_unsorted_insertions_and_promoted_slots()
    test_jitr_promote_transfers_inbound_sources_avoiding_dangling_chain()
    test_jitr_native_trace_lookup_uses_resident_snapshot()
    test_jitr_31_to_35_trace_chaining_and_ok_unlinking()
    test_jitc_20_trace_header_16byte_x64_physical_layout()
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
    test_jitr_aging_scans_region_card_bytes_and_ignores_import_code()
    test_hotspot_and_trackable_bitmaps_share_one_code_region_card_space()
    test_gotcha_jitr_09_aging_never_drops_compiled_or_hot()
    test_jitr_62_chain_links_stay_valid_across_rotation_and_promotion()
    print("[PASS] All directly invoked JIT Runtime & Cache tests passed.")


def test_native_product_plugin_is_opaque_and_executes_compiled_code():
    from bump_allocator import BumpAllocator
    from tier3_plugins.jit.jit_manager import JITRuntimeManager as ProductPlugin

    module = parse(
        wat_to_wasm("""(module
      (func (export "sum") (param i32) (result i32) (local i32)
        (loop $loop local.get 1 local.get 0 i32.add local.set 1
          local.get 0 i32.const 1 i32.sub local.tee 0 br_if $loop) local.get 1))""")
    )
    import mmap

    allocator = BumpAllocator()

    def reserve_region(size, alignment):
        assert alignment == mmap.PAGESIZE
        allocator.allocate(size, alignment)
        return memoryview(mmap.mmap(-1, size))

    plugin = ProductPlugin(reserve_region, yield_threshold=8)
    engine = RuntimeEngine(jit_runtime=plugin, collect_runtime_stats=True, bump_allocator=allocator)
    engine.register_module_blocks(module)
    interpreter = Interpreter(module)
    function = module.export_func_index("sum")
    for value in (5, 10, 20):
        assert engine.call(interpreter, function, [value]) == [value * (value + 1) // 2]
    assert engine.stat_jit_invocations > 0
    second_interpreter = Interpreter(module)
    assert engine.call(second_interpreter, function, [11]) == [66]
    assert engine.call(interpreter, function, [12]) == [78]
    plugin.flush_all()
    assert engine.call(interpreter, function, [7]) == [28]
    plugin.close()


def test_native_product_plugin_retains_region_until_gc_close():
    """Native teardown must finish before GC releases the supplied storage."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(_PYSIM_DIR / "benchmarks/profile/bench_vtune_workload.py"),
            "--phase",
            "suite_jit",
            "--kernel",
            "k_crc32:1",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "result=0x55DB1420" in result.stdout
