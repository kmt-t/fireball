from __future__ import annotations

from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

REPO_ROOT = _PYSIM_DIR.parent.parent


from hal_dispatch import HalBufferPool
from helpers import expect_assertion, make_test_ipc_message, wat_to_wasm
from helpers import make_interpreter as Interpreter
from ipc_router import (
    IPCRouter,
    IPCStatus,
    OwnershipState,
    Role,
)
from loader import WasmLoader
from memory import FB_CONF_MEMORY_POOL_SIZE, MemoryManager
from runtime_events import RuntimeEvent
from scheduler import ChannelAction, Scheduler, Task, WaitDir
from system import System, WasiErrno
from system_containers import BitView, MutableFlatMapStorage, ReadOnlyFlatMapView, StaticVector
from tier2_runtime.logger import LogDictionary, Logger, LogLevel
from tier3_executer.interpreter.interpreter import _HANDLERS
from tier3_executer.jit.jit_cache import CardState, JITMultiBufferCache, JITTrace
from tier3_platform.drivers.hal.stream import StreamTransport


def _make_router(sched: Scheduler) -> IPCRouter:
    manager = MemoryManager(sched)
    assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    return IPCRouter(sched, manager)


def _make_memory_manager() -> tuple[MemoryManager, Scheduler]:
    scheduler = Scheduler()
    scheduler.spawn("task_1", task_id=1)
    scheduler.spawn("task_2", task_id=2)
    scheduler.current_task = scheduler.get_task(1)
    manager = MemoryManager(scheduler)
    manager.init_manager(pool_base=0x00010000, pool_size=0x40000)
    return manager, scheduler


from test_support import (
    PcOnlyCompiler,
    make_pc_only_module,
    make_runtime_engine,
)
from tier3_executer.jit.x64_jit import TraceCompiler
from vmmio import TrapCode, VMMIOController, VmmioStatus
from wasm_reader import parse

# ==============================================================================
# 1. Interpreter Gotchas (GOTCHA-INTP-01 ~ 04)
# ==============================================================================


def _activate_scheduler_task(scheduler: Scheduler, task: Task) -> None:
    scheduler.current_task = None
    scheduler.detach(task)
    scheduler.activate_task(task)


def test_intp_gotcha_01_native_stack_sync():
    """GOTCHA-INTP-01: Direct handlers update the shared Native operand stack."""
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
    ip, frame, locals_arr, _ = call_state.cont

    # Execute instruction 0 (i32.const 10) directly via the raw handler.
    call_state.context.bind_handler_state(ip, frame)
    result = _HANDLERS[frame.code[ip]](call_state.context, frame.values, locals_arr, 0)
    assert result is None
    result_ctx = call_state.context
    result_sp = frame.values
    next_tos = frame.values.raw_top()
    assert frame.values.raw_top() == 10
    assert frame.values == [10]
    ip = int(result_ctx.native_context.ip)
    frame = result_ctx.call_frame_stack[-1]

    # Execute instruction 1 (i32.const 20)
    call_state.context.bind_handler_state(ip, frame)
    result = _HANDLERS[frame.code[ip]](result_ctx, result_sp, locals_arr, next_tos)
    assert result is None
    next_tos = frame.values.raw_top()
    assert frame.values.raw_top() == 20
    assert frame.values == [10, 20]
    ip = int(result_ctx.native_context.ip)
    frame = result_ctx.call_frame_stack[-1]

    # Execute instruction 2 (i32.add) -> pops 20 and 10, pushes 30.
    call_state.context.bind_handler_state(ip, frame)
    result = _HANDLERS[frame.code[ip]](result_ctx, result_sp, locals_arr, next_tos)
    assert result is None
    assert frame.values.raw_top() == 30
    assert frame.values == [30]


def test_intp_gotcha_02_label_arity_pruning_restores_tos():
    """GOTCHA-INTP-02: block result pruning preserves the label value and TOS."""
    wat = """
    (module
      (func (export "main") (result i32)
        (block $b0 (result i32)
          i32.const 10
          i32.const 42
          br $b0
        )
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    call_state = interp.start(0, [])
    while not call_state.finished:
        call_state = interp.step(call_state)

    assert call_state.results == [42]


def test_intp_gotcha_03_if_false_no_else_no_frame_leak():
    """GOTCHA-INTP-03: if with false condition and no else does not leak a control frame on stack."""
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
    call_state = interp.step(call_state)
    depth_before = len(call_state.cont[1].frames)
    call_state = interp.step(call_state)
    depth_after = len(call_state.cont[1].frames)
    assert depth_after == depth_before  # No frame leaked!

    while not call_state.finished:
        call_state = interp.step(call_state)
    assert call_state.results == [77]


def test_intp_gotcha_04_unified_pc_multi_module():
    """GOTCHA-INTP-04: UnifiedPC ((func_index << 16) | offset) prevents cross-function collision in ReadOnlyFlatMapView."""
    pc_fn0 = (0 << 16) | 0x0010
    pc_fn1 = (1 << 16) | 0x0010
    assert pc_fn0 != pc_fn1

    keys = sorted([pc_fn0, pc_fn1])
    vals = [100 if k == pc_fn0 else 200 for k in keys]
    entries = list(zip(keys, vals, strict=False))
    view = ReadOnlyFlatMapView(entries)

    assert view.find(pc_fn0) == 100
    assert view.find(pc_fn1) == 200


# ==============================================================================
# 2. JIT Compiler & Runtime Gotchas (JITC & JITR)
# ==============================================================================


def test_jitr_gotcha_01_idle_hook_skips_recompiling_already_resident_trace():
    """GOTCHA-JITR-01: idle_hook skips recompilation if trace is already resident in cache."""
    compile_calls = []

    def fake_compile(pc):
        compile_calls.append(pc)
        return JITTrace(pc, lambda: 0, size_bytes=64)

    wat = '(module (func (export "f") i32.const 1 drop i32.const 2 drop return))'
    wasm_bytes = wat_to_wasm(wat)
    engine = make_runtime_engine(jit_compiler=PcOnlyCompiler(fake_compile), card_shift=3)
    mod = engine.load_wasm(wasm_bytes)
    pc = mod.blocks[0].head_pc
    engine.jit_runtime.cache.insert(JITTrace(pc, lambda: 0, size_bytes=64))
    engine.jit_runtime.compile_queue.push_back(pc)

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert engine.jit_runtime.bitmap.get_state(pc) == CardState.COMPILED


def test_jitr_gotcha_02_promotion_transfers_inbound_sources():
    """GOTCHA-JITR-02: Promotion preserves inbound sources for resident chain targets."""
    cache = JITMultiBufferCache(bank_capacity=512)
    t2 = JITTrace(head_pc=0x200, native_fn=lambda: 2, size_bytes=64)
    cache.insert(t2)  # t2 -> Active
    cache.rotate()  # t2's bank -> Warm
    t1 = JITTrace(head_pc=0x100, native_fn=lambda: 1, size_bytes=64, next_pc=0x200)
    cache.insert(t1)  # t1 in Active records the Warm-resident t2 as its logical successor
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
    """GOTCHA-JITR-03: LIFO compile order records later straight-line successors first."""
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
    assert compiled_traces == [0x300, 0x200], "LIFO compilation order required"
    assert engine.jit_runtime.cache.active.has_trace(0x300)
    assert engine.jit_runtime.cache.active.has_trace(0x200)
    assert not engine.jit_runtime.cache.active.has_trace(0x100)


# ==============================================================================
# 3. vSoC Gotchas (GOTCHA-VSOC-01 ~ 03)
# ==============================================================================


def test_vsoc_gotcha_01_02_stateless_interp_and_yield_in_vsoc():
    """GOTCHA-VSOC-01, 02: Interpreter is stateless; JIT check and dispatch occur in vSoC engine."""
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
    engine = make_runtime_engine(yield_threshold=3, jit_compiler=TraceCompiler())
    mod = engine.load_wasm(wasm_bytes)
    loop_pc = mod.blocks[0].head_pc
    results = engine.call(Interpreter(mod), 0, [5])
    assert results[0] == 15
    assert engine.stat_jit_invocations >= 2
    assert engine.stat_interp_steps >= 3
    assert engine.jit_runtime.bitmap.get_state(loop_pc) == CardState.COMPILED
    assert engine.jit_runtime.cache.active.has_trace(
        loop_pc
    ) or engine.jit_runtime.cache.warm.has_trace(loop_pc)


# ==============================================================================
# 4. vMMIO Gotchas (GOTCHA-VMMIO-01 ~ 03)
# ==============================================================================


def test_vmmio_gotcha_01_ram_bypass_never_touches_tlb():
    """GOTCHA-VMMIO-01: Bit 31 == 0 is Guest RAM bypass and never increments TLB hit/miss."""
    scheduler = Scheduler()
    task_id = scheduler.spawn("test_task")
    scheduler.current_task = scheduler.get_task(task_id)
    ctrl = VMMIOController(guest_ram_size=64 * 1024, scheduler=scheduler)
    hits_before = ctrl.tlb_hits
    misses_before = ctrl.tlb_misses

    stat, _ = ctrl.access(raw_addr=0x100, is_write=False)
    assert stat == VmmioStatus.OK_GUEST_RAM
    assert ctrl.tlb_hits == hits_before
    assert ctrl.tlb_misses == misses_before


def test_vmmio_gotcha_02_folding_xor_hash_disperses_function_codes():
    """GOTCHA-VMMIO-02: 5-bit Folding XOR Hash disperses different Function Codes of same lower page."""
    vpn_c = 0x8000C
    vpn_e = 0x8000E
    temp_c = vpn_c ^ (vpn_c >> 10)
    temp_c = temp_c ^ (temp_c >> 5)
    idx_c = temp_c & 0x1F
    temp_e = vpn_e ^ (vpn_e >> 10)
    temp_e = temp_e ^ (temp_e >> 5)
    idx_e = temp_e & 0x1F
    assert idx_c != idx_e


def test_vmmio_gotcha_03_revoke_invalidates_tlb_blocks_inflight():
    """GOTCHA-VMMIO-03: Revoke immediately invalidates TLB entry and blocks access in-flight."""
    scheduler = Scheduler()
    owner_id = scheduler.spawn("owner")
    scheduler.activate_task(scheduler.get_task(owner_id))
    ctrl = VMMIOController(guest_ram_size=64 * 1024, scheduler=scheduler)
    vpn = 0xE0000
    ctrl.map_shm_page(vpn=vpn, phys_page=2, owner_id=1)

    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True)
    assert stat == VmmioStatus.OK_PHYSICAL

    ctrl.revoke_shm_owner(vpn=vpn)

    stat1, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True)
    task2_id = ctrl.scheduler.spawn("rogue")
    with ctrl.scheduler.task_context(ctrl.scheduler.get_task(task2_id)):
        stat2, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True)
    assert stat1 == TrapCode.UNREGISTERED_PAGE
    assert stat2 == TrapCode.UNREGISTERED_PAGE


# ==============================================================================
# 5. IPC Router & Scheduler Gotchas (IPCR & SCHED)
# ==============================================================================


def test_ipcr_gotcha_01_no_queue_assertion_on_duplicate_send():
    """GOTCHA-IPCR-01: CSP rendezvous channel has no queue; duplicate send raises assertion, not QUEUE_FULL."""
    sched = Scheduler()
    router = _make_router(sched)
    sender_id = sched.spawn("sender", role=Role.RUNTIME)
    sched.current_task = sched.get_task(sender_id)

    status, ch = router.lookup("fireball://hal/gpio/0")
    assert status == IPCStatus.COMPLETED and ch is not None

    msg1 = make_test_ipc_message([(1, 100)], memory_manager=router.memory_manager)
    gen1 = router.send(ch, msg1)
    assert next(gen1) == (ChannelAction.BLOCK, None)
    assert msg1.ownership == OwnershipState.IN_FLIGHT

    assert ch.waiter_task is not None
    assert ch.waiter_dir == WaitDir.SEND

    # Duplicate send on the busy channel raises assertion (no queue exists)
    msg2 = make_test_ipc_message([(2, 200)], memory_manager=router.memory_manager)
    sender2_id = sched.spawn("sender2", role=Role.RUNTIME)
    sched.current_task = sched.get_task(sender2_id)
    gen2 = router.send(ch, msg2)
    with expect_assertion():
        next(gen2)


def test_ipcr_gotcha_02_preflight_rejection_preserves_sender_ownership():
    """GOTCHA-IPCR-02: Preflight rejection (RBAC denial) keeps message in SENDER_OWNS."""
    sched = Scheduler()
    router = _make_router(sched)
    sender_id = sched.spawn("sender_hal", role=Role.HAL_UART)
    sched.current_task = sched.get_task(sender_id)

    msg = make_test_ipc_message([(1, 99)], memory_manager=router.memory_manager)
    status, ch = router.lookup("fireball://dbg/manager/0")
    assert status == IPCStatus.ERR_PERMISSION_DENIED
    assert ch is None
    assert msg.ownership == OwnershipState.SENDER_OWNS

    # Even if an attacker obtains an unauthorized channel directly, send() rejects it based on TCB role
    runtime_ch = router.channel_for_edge(Role.RUNTIME, Role.HAL_UART)
    assert runtime_ch is not None
    try:
        gen = router.send(runtime_ch, msg)
        next(gen)
    except StopIteration as e:
        status, _ = e.value
        assert status == IPCStatus.ERR_PERMISSION_DENIED

    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_sched_gotcha_01_handoff_limit_forces_return_to_main_loop():
    """GOTCHA-SCHED-01: The direct handoff limit returns control to the scheduler."""
    sched = Scheduler(max_handoffs=2)
    ch1 = sched.create_channel()
    ch2 = sched.create_channel()
    ch3 = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    _activate_scheduler_task(sched, t1)
    ch1.send(1)
    _activate_scheduler_task(sched, t2)
    act1, _ = ch1.recv()
    assert act1 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 1

    _activate_scheduler_task(sched, t1)
    ch2.send(2)
    _activate_scheduler_task(sched, t2)
    act2, _ = ch2.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 2

    _activate_scheduler_task(sched, t1)
    ch3.send(3)
    _activate_scheduler_task(sched, t2)
    act3, _ = ch3.recv()
    assert act3 == ChannelAction.YIELD, (
        "Must yield back to scheduler when consecutive handoffs reach threshold"
    )
    assert sched.consecutive_handoffs == 0


# ==============================================================================
# 5. Core & Platform Implementation Gotchas
# ==============================================================================


def test_coos_gotcha_01_no_data_slot_in_channel():
    """GOTCHA-COOS-01: Channel has no internal value buffer; zero-copy handoff guarantees single ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    assert not hasattr(ch, "buffer"), "Channel must not contain a message buffer queue"
    assert not hasattr(ch, "data_slot"), "Channel must not have a data slot"

    t1 = sched.get_task(sched.spawn("sender"))
    t2 = sched.get_task(sched.spawn("receiver"))

    _activate_scheduler_task(sched, t1)
    act1, val1 = ch.send(999)
    assert act1 == ChannelAction.BLOCK
    assert t1.pending_val == 999
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND

    _activate_scheduler_task(sched, t2)
    act2, _ = ch.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert t2.received_val == 999
    assert t1.pending_val is None, "Sender pending_val must be cleared immediately upon handoff"


def test_coos_gotcha_02_single_waiter_assert():
    """GOTCHA-COOS-02: 1-channel-1-waiter constraint triggers assertion on duplicate wait direction."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("sender1"))
    t2 = sched.get_task(sched.spawn("sender2"))

    _activate_scheduler_task(sched, t1)
    ch.send(100)

    _activate_scheduler_task(sched, t2)
    with expect_assertion():
        ch.send(200)


def test_cont_gotcha_01_bit_view_power_of_two_factors():
    """GOTCHA-CONT-01: bit_view rejects non-divisor-of-8 widths (1, 2, 4 only)."""
    buf = bytearray(8)
    bv1 = BitView(buf, bits=1)
    bv2 = BitView(buf, bits=2)
    bv4 = BitView(buf, bits=4)
    assert bv1.bits == 1 and bv2.bits == 2 and bv4.bits == 4

    for invalid_bits in [3, 5, 6, 7]:
        rejected = False
        try:
            BitView(buf, bits=invalid_bits)
        except (ValueError, AssertionError):
            rejected = True
        assert rejected, f"Expected BitView to reject bits={invalid_bits}"


def test_cont_gotcha_02_narrowing_never_expands_bounds():
    """GOTCHA-CONT-02: View slicing is strictly monotonic narrowing and cannot expand outside parent span."""
    sm = MutableFlatMapStorage(capacity=16)
    for k, v in enumerate([10, 20, 30, 40, 50]):
        sm.insert(k, v)
    view = sm.view()
    sub_view = view.slice(1, 3)
    assert len(sub_view.entries) == 2

    # Attempting to expand or slice outside bounds must raise ValueError("a view may only ever shrink")
    for invalid_first, invalid_last in [(-1, 3), (0, 10), (3, 2), (2, 6)]:
        rejected = False
        try:
            view.slice(invalid_first, invalid_last)
        except (ValueError, IndexError, AssertionError):
            rejected = True
        assert rejected, f"Expected slice({invalid_first}, {invalid_last}) to fail"


def test_log_gotcha_01_no_runtime_pointer_scalar_args_only():
    """GOTCHA-LOG-01: Logging interface rejects string specifiers and accepts only scalar u32 arguments."""
    d = LogDictionary()
    for bad_fmt in ["Message: %s", "Pointer: %p", "Char: %c"]:
        with expect_assertion():
            d.register(0x10, bad_fmt)

    d.register(0x20, "Task %d event %u (0x%08X)")
    uart = StreamTransport()
    logger = Logger(uart, d, capacity=4)
    res = logger.log_event(LogLevel.INFO, dict_offset=0x20, arg0=1, arg1=2, arg2=0xABC)
    assert res == "QUEUED"


def test_log_gotcha_02_ring_buffer_oldest_overwrite():
    """GOTCHA-LOG-02: Ring buffer overwrite on full preserves system non-blocking invariant."""
    d = LogDictionary()
    d.register(0x10, "Event %d")
    uart = StreamTransport()
    cap = 4
    logger = Logger(uart, d, capacity=cap)

    for i in range(cap):
        assert logger.log_event(LogLevel.INFO, dict_offset=0x10, arg0=i) == "QUEUED"

    assert logger.log_event(LogLevel.INFO, dict_offset=0x10, arg0=100) == "OVERWRITTEN"
    assert logger.ring.count == cap
    assert logger.ring.dropped == 1


def test_mem_gotcha_01_page_granular_isolation():
    """GOTCHA-MEM-01: Page-granular permission isolation ensures distinct tasks never share the same 4KB physical page."""
    mm, scheduler = _make_memory_manager()
    b1 = mm.allocate_shared(size=64).unwrap()
    scheduler.current_task = scheduler.get_task(2)
    b2 = mm.allocate_shared(size=64).unwrap()
    assert b1.page_idx != b2.page_idx, (
        f"Task 1 (page {b1.page_idx}) and Task 2 (page {b2.page_idx}) must not share physical page"
    )


def test_mem_gotcha_02_release_and_flight_protection():
    """GOTCHA-MEM-02: Releasing a SharedBlock marks it in-flight and revokes access until claimed."""
    mm, scheduler = _make_memory_manager()
    b = mm.allocate_shared(size=64).unwrap()
    b.write_u32(0, 0x12345678)
    assert b.read_u32(0) == 0x12345678

    shm_id = b.release()
    assert b._is_in_flight
    assert not b._is_active
    with expect_assertion():
        b.read_u32(0)

    scheduler.current_task = scheduler.get_task(2)
    assert not mm.claim(shm_id).is_ok
    with expect_assertion():
        b.get_size()


def test_mem_gotcha_02b_release_owner_only():
    """GOTCHA-MEM-02: Non-owner task cannot release() or access another task's SharedBlock."""
    mm, scheduler = _make_memory_manager()
    b = mm.allocate_shared(size=64).unwrap()
    scheduler.current_task = scheduler.get_task(2)

    with expect_assertion("GOTCHA-MEM-02"):
        b.release()

    with expect_assertion("GOTCHA-MEM-02"):
        b.get_address()

    assert b._is_active
    assert b.get_owner() == 1
    scheduler.current_task = scheduler.get_task(1)
    b.release()
    assert not b._is_active


def test_hal_gotcha_01_hal_buffer_pool_bounds_violation_rejected():
    """GOTCHA-HAL-01: fixed HAL slots reject out-of-bounds slices."""
    scheduler = Scheduler()
    owner_id = scheduler.spawn("owner")
    scheduler.current_task = scheduler.get_task(owner_id)
    from vmmio import VMMIOController

    vmmio = VMMIOController(guest_ram_size=8192, scheduler=scheduler)
    pool = HalBufferPool(scheduler, vmmio)
    handle = pool.buffer(0)
    assert pool.map_for_io(handle.buffer_id).name == "MAPPED"
    assert handle.capacity == 256
    with expect_assertion("escapes fixed buffer"):
        pool.view(handle, 0, 257)
    pool.unmap_after_io(handle.buffer_id)


def test_sys_gotcha_01_undefined_syscall_returns_enosys():
    """GOTCHA-SYS-01: Undefined syscall ID safely returns WasiErrno.NOSYS instead of aborting or panicking."""
    sys_inst = System()
    sys_inst.start_runtime_task(name="test_runtime_task")
    res = sys_inst.fireball_call(0xFE, 0, 0, 0, 0, 0, 0)
    assert res == int(WasiErrno.NOSYS), f"Expected NOSYS (52), got {res}"


def test_dbg_gotcha_01_debugger_and_jit_composition_is_rejected():
    """GOTCHA-DBG-01: Debugger and JIT cannot be enabled in one runtime composition."""
    from helpers import expect_assertion
    from runtime_composer import (
        RuntimeComposer,
        RuntimeCompositionConfig,
        RuntimeExecutionKind,
        RuntimeFactories,
        RuntimePluginSelection,
    )

    class _Executor:
        def call(self, func_index: int, args: tuple[int, ...]) -> int:
            return func_index + sum(args)

    class _Observer:
        def on_runtime_event(self, event: RuntimeEvent) -> None:
            return None

    factories = RuntimeFactories(
        interpreter=_Executor,
        jit=_Executor,
        logger=_Observer,
        debugger=_Observer,
        profiler=_Observer,
    )
    with expect_assertion("debugger-enabled runtime must use interpreter-only execution"):
        RuntimeComposer.compose(
            RuntimeCompositionConfig(
                execution=RuntimeExecutionKind.JIT,
                plugins=RuntimePluginSelection(debugger=True),
            ),
            factories,
        )


def test_load_gotcha_01_non_existent_symbol_fast_rejection():
    """GOTCHA-LOAD-01: Non-existent symbol rejection is O(k) without linear scan."""
    loader = WasmLoader()
    from experiments.pysim.qa.tier2_runtime.test_loader import _build_test_wasm_binary

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
