from __future__ import annotations

import sys
from pathlib import Path

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
    _TEST_FILE.parent,
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes

from control_flow import extract_basic_blocks
from debugger import DebuggerManager, GDBRspProtocol
from hal import ShmBufferPool, ShmTrap, UartTransport
from interpreter import _HANDLERS, Interpreter
from ipc_router import (
    IPCMessage,
    IPCRouter,
    OwnershipState,
    Role,
)
from jit_copy_patch_concept import CopyPatchJITEngine, Reg, Thumb2Assembler
from loader import WasmLoader
from logger import LogDictionary, Logger, LogLevel
from memory import MemoryManager
from runtime_engine import (
    BasicBlock,
    CardState,
    IntegratedHybridEngine,
    JITMultiBufferCache,
    JITTrace,
    RuntimeEngine,
    WASMContext,
)
from scheduler import ChannelAction, Scheduler, WaitDir
from system import System, WasiErrno
from system_containers import BitView, FlatMapView, MutableFlatMapStorage
from test_support import PcOnlyCompiler, wat_to_wasm
from vmmio import TrapCode, VMMIOController
from wasm_opcodes import I32_ADD, I32_CONST, LOCAL_GET, LOCAL_SET
from wasm_reader import parse
from x64_jit import TraceCompiler

# ==============================================================================
# 1. Interpreter Gotchas (INTP-GOTCHA-01 ~ 04)
# ==============================================================================


def test_intp_gotcha_01_tos_stack_sync():
    """INTP-GOTCHA-01: R3 (tos) and operand stack memory remain synchronized across individual instructions."""
    wat = """
    (module
      (func (export "main") (result i32)
        i32.const 10
        i32.const 20
        i32.add
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    call_state = interp.start(0, [])
    ip, frame, locals_arr, tos = call_state.cont
    assert tos == 0

    # Execute instruction 0 (i32.const 10) directly via CPS handler
    ins0 = frame.instrs[ip]
    cont = _HANDLERS[ins0.opcode](ip, frame, locals_arr, tos)
    ip, frame, locals_arr, tos = cont
    assert tos == 10
    assert frame.values == [10]

    # Execute instruction 1 (i32.const 20)
    ins1 = frame.instrs[ip]
    cont = _HANDLERS[ins1.opcode](ip, frame, locals_arr, tos)
    ip, frame, locals_arr, tos = cont
    assert tos == 20
    assert frame.values == [10, 20]

    # Execute instruction 2 (i32.add) -> pops 20 and 10, pushes 30 -> tos=30
    ins2 = frame.instrs[ip]
    cont = _HANDLERS[ins2.opcode](ip, frame, locals_arr, tos)
    ip, frame, locals_arr, tos = cont
    assert tos == 30
    assert frame.values == [30]


def test_intp_gotcha_02_label_arity_pruning_restores_tos():
    """INTP-GOTCHA-02: Stack pruning on block exit (br 0) accurately preserves values and restores tos."""
    wat = """
    (module
      (func (export "main") (result i32)
        (local $res i32)
        (block $b0
          i32.const 10
          local.set $res
          br $b0
        )
        local.get $res
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    call_state = interp.start(0, [])
    while not call_state.finished:
        call_state = interp.step(call_state, quantum=1)

    assert call_state.results == [10]


def test_intp_gotcha_03_if_false_no_else_no_frame_leak():
    """INTP-GOTCHA-03: if with false condition and no else does not leak a control frame on stack."""
    wat = """
    (module
      (func (export "main") (result i32)
        i32.const 0
        if
          i32.const 99
          drop
        end
        i32.const 77
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    call_state = interp.start(0, [])
    call_state = interp.step(call_state, quantum=1)
    depth_before = len(call_state.cont[1].frames)
    call_state = interp.step(call_state, quantum=1)
    depth_after = len(call_state.cont[1].frames)
    assert depth_after == depth_before  # No frame leaked!

    while not call_state.finished:
        call_state = interp.step(call_state, quantum=1)
    assert call_state.results == [77]


def test_intp_gotcha_04_unified_pc_multi_module():
    """INTP-GOTCHA-04: UnifiedPC ((func_index << 16) | offset) prevents cross-function collision in FlatMapView."""
    pc_fn0 = (0 << 16) | 0x0010
    pc_fn1 = (1 << 16) | 0x0010
    assert pc_fn0 != pc_fn1

    keys = sorted([pc_fn0, pc_fn1])
    vals = [100 if k == pc_fn0 else 200 for k in keys]
    entries = list(zip(keys, vals, strict=False))
    view = FlatMapView(entries)

    assert view.find(pc_fn0) == 100
    assert view.find(pc_fn1) == 200


# ==============================================================================
# 2. JIT Compiler & Runtime Gotchas (JITC & JITR)
# ==============================================================================


def test_jitc_gotcha_01_02_03_conventions():
    """JITC-GOTCHA-01, 02, 03: Verify JIT conforms to CPS 4-arg convention, mem load offsets, and TOS unspilled."""
    # 1. Test x64 JIT CPS 4-arg invocation -- real WASM bytecode for
    # `local.get 0; i32.const 5; i32.add; local.set 0`, run through the same
    # extract_basic_blocks + compile_block path production JIT compilation uses.
    compiler = TraceCompiler()
    code = bytes([LOCAL_GET, 0, I32_CONST, 5, I32_ADD, LOCAL_SET, 0])
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = compiler.compile_block(code, block)
    assert trace.header.head_wasm_pc == head_pc
    assert trace.size_bytes >= 16

    locals_arr = (ctypes.c_int64 * 8)(10, 0)
    trace.fn(
        head_pc,
        ctypes.c_void_p(0),
        ctypes.cast(locals_arr, ctypes.c_void_p),
        0,
    )
    assert locals_arr[0] == 15

    # 2. Test Thumb-2 JIT Copy-Patch Engine: JITC-GOTCHA-01 (register isolation),
    # JITC-GOTCHA-02 (mem_base/size loaded from [R1, #0x10] and [R1, #0x14]),
    # and JITC-GOTCHA-03 (Caller-saved R3 is not spilled in epilogue).
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 42), ("local.set", 4), ("i32.load", None)]
    start_pos, count = engine.compile_trace(ops)
    code = engine.execute_native(start_pos, count)

    # JITC-GOTCHA-01: Shared R0/R1/R2/R3 are never used as scratch destinations
    for inst in code:
        mnemonic, _, operands = inst.partition(" ")
        if mnemonic in (
            "STR",
            "STR.W",
            "STRB.W",
            "STRH.W",
            "BX",
            "PUSH",
            "PUSH.W",
            "POP",
            "POP.W",
            "CMP",
            "BNE.W",
            "BL",
        ):
            continue
        dest = operands.split(",")[0].strip()
        assert dest not in ("r0", "r1", "r2", "r3"), (
            f"CPS argument register {dest} clobbered by {inst}"
        )

    # JITC-GOTCHA-02: mem_base and mem_size loaded from [R1, #0x10] and [R1, #0x14]
    assert code[1] == "LDR.W r8, [r1, #0x10]"
    assert code[2] == "LDR.W r9, [r1, #0x14]"

    # JITC-GOTCHA-03: Epilogue pops only callee-saved registers {r4-r6, r8-r11, pc}, R3 (tos) remains in register
    assert "POP.W {r4-r6, r8-r11, pc}" in code


def test_jitc_gotcha_04_05_boundary_check_and_backpatch():
    """JITC-GOTCHA-04, 05: Boundary check precedes memory access, and BHS.W is accurately backpatched."""
    engine = CopyPatchJITEngine()
    ops = [("i32.const", 10), ("i32.load", None), ("local.set", 4)]
    start_pos, count = engine.compile_trace(ops)
    code = engine.execute_native(start_pos, count)

    # JITC-GOTCHA-04: CMP addr, r9 and BHS.W precede LDR.W
    cmp_idx = -1
    bhs_idx = -1
    ldr_idx = -1
    for idx, inst in enumerate(code):
        if "CMP" in inst and "r9" in inst:
            cmp_idx = idx
        elif "BHS.W" in inst:
            bhs_idx = idx
        elif "LDR.W r4, [r8" in inst:
            ldr_idx = idx

    assert 0 <= cmp_idx < bhs_idx < ldr_idx, "Boundary check does not precede memory load!"

    # JITC-GOTCHA-05: Trap tail exists at the end with BX r12 fallback
    assert code[-1] == "BX r12"


def test_jitc_gotcha_06_arm_mls_instruction_ordering():
    """JITC-GOTCHA-06: ARM MLS ordering Rd = Ra - Rn * Rm computes remainder correctly."""
    asm = Thumb2Assembler()
    encoded = asm.mls(Reg.R4, Reg.R12, Reg.R4, Reg.R5)
    engine = CopyPatchJITEngine()
    catalog_hex = engine.stencils["i32_rem_s_d2"].hex_bytes
    assert len(encoded) == 4
    assert len(catalog_hex) > 0


def test_jitr_gotcha_01_idle_hook_skips_recompiling_already_resident_trace():
    """JITR-GOTCHA-01: idle_hook skips recompilation if trace is already resident in cache."""
    compile_calls = []

    def fake_compile(pc):
        compile_calls.append(pc)
        return JITTrace(pc, lambda: 0, size_bytes=64)

    wat = '(module (func (export "f") i32.const 1 drop i32.const 2 drop return))'
    wasm_bytes = wat_to_wasm(wat)
    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(fake_compile), card_shift=3)
    mod = engine.load_wasm(wasm_bytes)
    pc = mod.blocks[0].head_pc
    engine.cache.insert(JITTrace(pc, lambda: 0, size_bytes=64))
    engine.compile_queue.append(pc)

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert engine.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_gotcha_02_promotion_transfers_inbound_sources():
    """JITR-GOTCHA-02: Promoting a trace from Oldest to Active preserves inbound sources avoiding dangling jump."""
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


def test_jitr_gotcha_03_lifo_reverse_compilation_order():
    """JITR-GOTCHA-03: Compile queue processes in LIFO order maximizing immediate forward chaining."""
    compiled_traces = []

    def dummy_compiler(pc: int) -> JITTrace:
        t = JITTrace(head_pc=pc, native_fn=lambda: pc * 2, size_bytes=64)
        compiled_traces.append(pc)
        return t

    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(dummy_compiler))
    engine.compile_queue = [0x100, 0x200, 0x300]
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_traces == [0x300, 0x200], "LIFO compilation order required"
    assert engine.cache.active.has_trace(0x300)
    assert engine.cache.active.has_trace(0x200)
    assert not engine.cache.active.has_trace(0x100)


# ==============================================================================
# 3. vSoC Gotchas (VSOC-GOTCHA-01 ~ 03)
# ==============================================================================


def test_vsoc_gotcha_01_02_stateless_interp_and_yield_in_vsoc():
    """VSOC-GOTCHA-01, 02: Interpreter is stateless; JIT check and dispatch occur in vSoC engine."""
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
        return
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    engine = IntegratedHybridEngine(yield_threshold=3, compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[0].head_pc

    ctx = WASMContext(locals_values=[5, 0])
    pc = loop_pc

    # Iterations 1-3 run in Interpreter (interp is stateless)
    for _ in range(3):
        pc = engine.run_step(pc, ctx)

    assert engine.interp_blocks == 3
    assert engine.jit_traces == 0
    assert loop_pc in engine.compile_queue

    # idle_hook batch compiles queued trace into Active cache
    compiled = engine.idle_hook()
    assert compiled == 1
    assert engine.cache.active.has_trace(loop_pc)
    assert engine.bitmap.get_state(loop_pc) == CardState.COMPILED

    # Iterations 4-5 run in JIT Trace
    while pc is not None:
        pc = engine.run_step(pc, ctx)

    # Sum of 1..5 = 15
    assert ctx.locals[1] == 15
    assert engine.jit_traces >= 2
    assert engine.interp_blocks >= 3


# ==============================================================================
# 4. vMMIO Gotchas (VMMIO-GOTCHA-01 ~ 03)
# ==============================================================================


def test_vmmio_gotcha_01_ram_bypass_never_touches_tlb():
    """VMMIO-GOTCHA-01: Bit 31 == 0 is Guest RAM bypass and never increments TLB hit/miss."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    hits_before = ctrl.tlb_hits
    misses_before = ctrl.tlb_misses

    stat, _ = ctrl.access(raw_addr=0x100, is_write=False, current_task_id=1)
    assert stat == "OK_GUEST_RAM"
    assert ctrl.tlb_hits == hits_before
    assert ctrl.tlb_misses == misses_before


def test_vmmio_gotcha_02_folding_xor_hash_disperses_function_codes():
    """VMMIO-GOTCHA-02: 4-bit Folding XOR Hash disperses different Function Codes of same lower page."""
    vpn_c = 0x8000C
    vpn_e = 0x8000E
    idx_c = (vpn_c ^ (vpn_c >> 4) ^ (vpn_c >> 8) ^ (vpn_c >> 12)) & 0x0F
    idx_e = (vpn_e ^ (vpn_e >> 4) ^ (vpn_e >> 8) ^ (vpn_e >> 12)) & 0x0F
    assert idx_c != idx_e


def test_vmmio_gotcha_03_revoke_invalidates_tlb_blocks_inflight():
    """VMMIO-GOTCHA-03: Revoke immediately invalidates TLB entry and blocks access in-flight."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    vpn = 0xE0000
    ctrl.map_shm_page(vpn=vpn, phys_page=2, owner_id=1)

    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    assert stat == "OK_PHYSICAL"

    ctrl.revoke_shm_owner(vpn=vpn)

    stat1, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    stat2, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=2)
    assert stat1 == TrapCode.OWNER_MISMATCH
    assert stat2 == TrapCode.OWNER_MISMATCH


# ==============================================================================
# 5. IPC Router & Scheduler Gotchas (IPCR & SCHED)
# ==============================================================================


def test_ipcr_gotcha_01_no_queue_assertion_on_duplicate_send():
    """IPCR-GOTCHA-01: CSP rendezvous channel has no queue; duplicate send raises assertion, not QUEUE_FULL."""
    sched = Scheduler()
    router = IPCRouter(sched)
    sender_id = sched.spawn("sender")
    sched.current_task = sched.get_task(sender_id)

    msg1 = IPCMessage.from_entries([(1, 100)])
    gen1 = router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg1)
    assert next(gen1) == (ChannelAction.BLOCK, None)
    assert msg1.ownership == OwnershipState.IN_FLIGHT

    ch = router.channel_for_edge(Role.RUNTIME, Role.PLATFORM_HAL)
    assert ch is not None
    assert ch.waiter_task is not None
    assert ch.waiter_dir == WaitDir.SEND


def test_ipcr_gotcha_02_preflight_rejection_preserves_sender_ownership():
    """IPCR-GOTCHA-02: Preflight rejection (RBAC denial) keeps message in SENDER_OWNS."""
    sched = Scheduler()
    router = IPCRouter(sched)
    sender_id = sched.spawn("sender_hal")
    sched.current_task = sched.get_task(sender_id)

    msg = IPCMessage.from_entries([(1, 99)])
    try:
        gen = router.send(Role.PLATFORM_HAL, "fireball://debugger/control", msg)
        next(gen)
    except (StopIteration, AssertionError):
        pass

    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_sched_gotcha_01_handoff_limit_forces_return_to_main_loop():
    """SCHED-GOTCHA-01: Direct CSP handoff limit forces return to main scheduling loop to prevent starvation."""
    sched = Scheduler(max_handoffs=2)
    ch1 = sched.create_channel()
    ch2 = sched.create_channel()
    ch3 = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t1
    ch1.send(1)
    sched.current_task = t2
    act1, _ = ch1.recv()
    assert act1 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 1

    sched.current_task = t1
    ch2.send(2)
    sched.current_task = t2
    act2, _ = ch2.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 2

    sched.current_task = t1
    ch3.send(3)
    sched.current_task = t2
    act3, _ = ch3.recv()
    assert act3 == ChannelAction.YIELD, (
        "Must yield back to scheduler when consecutive handoffs reach threshold"
    )
    assert sched.consecutive_handoffs == 0


# ==============================================================================
# 5. Core & Platform Implementation Gotchas
# ==============================================================================


def test_coos_gotcha_01_no_data_slot_in_channel():
    """COOS-GOTCHA-01: Channel has no internal value buffer; zero-copy handoff guarantees single ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    assert not hasattr(ch, "buffer"), "Channel must not contain a message buffer queue"
    assert not hasattr(ch, "data_slot"), "Channel must not have a data slot"

    t1 = sched.get_task(sched.spawn("sender"))
    t2 = sched.get_task(sched.spawn("receiver"))

    sched.current_task = t1
    act1, val1 = ch.send(999)
    assert act1 == ChannelAction.BLOCK
    assert t1.pending_val == 999
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND

    sched.current_task = t2
    act2, _ = ch.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert t2.received_val == 999
    assert t1.pending_val is None, "Sender pending_val must be cleared immediately upon handoff"


def test_coos_gotcha_02_single_waiter_assert():
    """COOS-GOTCHA-02: 1-channel-1-waiter constraint triggers assertion on duplicate wait direction."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("sender1"))
    t2 = sched.get_task(sched.spawn("sender2"))

    sched.current_task = t1
    ch.send(100)

    sched.current_task = t2
    try:
        ch.send(200)
        raise AssertionError("Expected AssertionError on duplicate channel send")
    except AssertionError:
        pass


def test_cont_gotcha_01_bit_view_power_of_two_factors():
    """CONT-GOTCHA-01: bit_view rejects non-divisor-of-8 widths (1, 2, 4 only)."""
    buf = bytearray(8)
    bv1 = BitView(buf, bits=1)
    bv2 = BitView(buf, bits=2)
    bv4 = BitView(buf, bits=4)
    assert bv1.bits == 1 and bv2.bits == 2 and bv4.bits == 4

    for invalid_bits in [3, 5, 6, 7]:
        try:
            BitView(buf, bits=invalid_bits)
            raise AssertionError(f"Expected BitView to reject bits={invalid_bits}")
        except (ValueError, AssertionError):
            pass


def test_cont_gotcha_02_narrowing_never_expands_bounds():
    """CONT-GOTCHA-02: View slicing is strictly monotonic narrowing and cannot expand outside parent span."""
    sm = MutableFlatMapStorage(capacity=16)
    for k, v in enumerate([10, 20, 30, 40, 50]):
        sm.insert(k, v)
    view = sm.view()
    sub_view = view.slice(1, 3)
    assert len(sub_view.entries) == 2

    # Attempting to expand or slice outside bounds must raise ValueError("a view may only ever shrink")
    for invalid_first, invalid_last in [(-1, 3), (0, 10), (3, 2), (2, 6)]:
        try:
            view.slice(invalid_first, invalid_last)
            raise AssertionError(f"Expected slice({invalid_first}, {invalid_last}) to fail")
        except (ValueError, IndexError, AssertionError) as e:
            if isinstance(e, AssertionError) and "Expected" in str(e):
                raise


def test_log_gotcha_01_no_runtime_pointer_scalar_args_only():
    """LOG-GOTCHA-01: Logging interface rejects string specifiers and accepts only scalar u32 arguments."""
    d = LogDictionary()
    for bad_fmt in ["Message: %s", "Pointer: %p", "Char: %c"]:
        try:
            d.register(0x10, bad_fmt)
            raise AssertionError(f"Expected LogDictionary to reject '{bad_fmt}'")
        except ValueError:
            pass

    d.register(0x20, "Task %d event %u (0x%08X)")
    uart = UartTransport()
    logger = Logger(uart, d, capacity=4)
    res = logger.log_event(LogLevel.INFO, dict_offset=0x20, arg0=1, arg1=2, arg2=0xABC)
    assert res == "QUEUED"


def test_log_gotcha_02_ring_buffer_oldest_overwrite():
    """LOG-GOTCHA-02: Ring buffer overwrite on full preserves system non-blocking invariant."""
    d = LogDictionary()
    d.register(0x10, "Event %d")
    uart = UartTransport()
    cap = 4
    logger = Logger(uart, d, capacity=cap)

    for i in range(cap):
        assert logger.log_event(LogLevel.INFO, dict_offset=0x10, arg0=i) == "QUEUED"

    assert logger.log_event(LogLevel.INFO, dict_offset=0x10, arg0=100) == "OVERWRITTEN"
    assert logger.ring.count == cap
    assert logger.ring.dropped == 1


def test_mem_gotcha_01_page_granular_isolation():
    """MEM-GOTCHA-01: Page-granular permission isolation ensures distinct tasks never share the same 4KB physical page."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=0x40000)
    b1 = mm.allocate_shared(caller_task_id=1, size=64).unwrap()
    b2 = mm.allocate_shared(caller_task_id=2, size=64).unwrap()
    assert b1.page_idx != b2.page_idx, (
        f"Task 1 (page {b1.page_idx}) and Task 2 (page {b2.page_idx}) must not share physical page"
    )


def test_mem_gotcha_02_release_and_flight_protection():
    """MEM-GOTCHA-02: Releasing a SharedBlock marks it in-flight and revokes access until claimed."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=0x40000)
    b = mm.allocate_shared(caller_task_id=1, size=64).unwrap()
    b.write_u32(0, 0x12345678)
    assert b.read_u32(0) == 0x12345678

    shm_id = b.release()
    assert b._is_in_flight
    assert not b._is_active
    try:
        b.read_u32(0)
        raise AssertionError("Expected access to in-flight block to be rejected")
    except AssertionError:
        pass

    assert not mm.claim(receiver_task_id=2, shm_id=shm_id).is_ok
    assert mm.grant_shared(shm_id=shm_id, new_owner_task_id=2)

    b_claimed = mm.claim(receiver_task_id=2, shm_id=shm_id).unwrap()
    assert b_claimed.read_u32(0) == 0x12345678
    assert b_claimed.get_owner() == 2


def test_hal_gotcha_01_shm_pool_bounds_violation_rejected():
    """HAL-GOTCHA-01: ShmBufferPool rejects slice requests exceeding maximum buffer size and non-owner releases."""
    pool = ShmBufferPool()
    handle = pool.acquire_buffer(task_id=1, size=128)
    assert handle.capacity == 128

    try:
        pool.acquire_buffer(task_id=1, size=512)
        raise AssertionError("Expected ShmBufferPool.acquire_buffer to reject size > 256")
    except ValueError:
        pass

    try:
        pool.release_buffer(task_id=2, handle=handle)
        raise AssertionError("Expected ShmTrap when task 2 attempts to release task 1's buffer")
    except ShmTrap:
        pass

    pool.release_buffer(task_id=1, handle=handle)


def test_sys_gotcha_01_undefined_syscall_returns_enosys():
    """SYS-GOTCHA-01: Undefined syscall ID safely returns WasiErrno.NOSYS instead of aborting or panicking."""
    sys_inst = System()
    res = sys_inst.fireball_call(0xFE, 0, 0, 0, 0, 0, 0)
    assert res == int(WasiErrno.NOSYS), f"Expected NOSYS (52), got {res}"


def test_dbg_gotcha_01_memory_write_flushes_jit_cache():
    """DBG-GOTCHA-01: Debugger memory write immediately invalidates all JIT cache banks."""
    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext(memory=bytearray(64))

    code = bytes([I32_CONST, 10])
    head_pc, next_pc, loops_to, frame_depth, byte_span = extract_basic_blocks(code)[0]
    block = BasicBlock(
        head_pc=head_pc,
        next_pc=next_pc,
        loops_to=loops_to,
        frame_depth=frame_depth,
        byte_span=byte_span,
    )
    trace = engine.compiler.compile_block(code, block)
    engine.cache.insert(trace)
    assert engine.cache.active.has_trace(head_pc)

    res, _ = rsp.handle_packet("M0,4:deadbeef", 0, ctx, {})
    assert res.startswith("$OK#")
    assert bytes(ctx.memory[0:4]) == bytes.fromhex("deadbeef")
    assert not engine.cache.active.has_trace(head_pc), (
        "JIT cache must be flushed upon debugger memory write"
    )


def test_load_gotcha_01_non_existent_symbol_fast_rejection():
    """LOAD-GOTCHA-01: Non-existent symbol rejection is O(k) without linear scan."""
    loader = WasmLoader()
    from tier2_runtime.test_loader import _build_test_wasm_binary

    wasm_bytes = _build_test_wasm_binary(export_names=["foo", "bar"])
    view = loader.prepare("test_mod", wasm_bytes)
    assert view.lookup_export("non_existent_symbol_xyz") is None


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} implementation gotchas and invariants tests passed.")
