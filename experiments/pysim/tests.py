"""
experiments/pysim/tests.py

Comprehensive assert-based invariant test suite for the pysim experiment,
covering all Fireball component test specifications (*_test_spec.md):
- Tier 1 COOS & Scheduler (os_coos_test_spec.md, os_scheduler_test_spec.md)
- Tier 1 Logging & IPC Router (system_logging_test_spec.md, ipc_router_test_spec.md)
- Tier 2 vMMIO & Recovery (runtime_vmmio_test_spec.md)
- Tier 3 Platform Memory & HAL & MPU W^X (platform_memory_test_spec.md, platform_hal_test_spec.md)
- Tier 3 JIT Hotspot Profiling & 3-Bank Cache (jit_compiler_test_spec.md, jit_runtime_test_spec.md)
- Tier 1/2 Syscalls (system_syscall_test_spec.md)
- WASM Instruction Set MVP (wasm_instruction_set_test_spec.md)

Run with:  uv run python experiments/pysim/tests.py
"""

from __future__ import annotations

import os
import struct
import sys
import time

_DOCS_COMPONENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "components")
for _sub in (("tier3_platform", "concepts"), ("tier2_runtime", "concepts"), ("tier1_interface", "concepts"), ("tier1_core", "concepts")):
    _p = os.path.join(_DOCS_COMPONENTS, *_sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hal import FB_CONF_HAL_BUFFER_SIZE, FB_CONF_HAL_MAX_BUFFERS, HalError, ShmBufferPool, ShmTrap, Timer, UartTransport
from interpreter import Interpreter, Trap
from ipc_router_concept import IPCMessage, IPCRouter
from logger import ConsoleOutput, LogDictionary, Logger, LogLevel
from platform_memory_concept import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_PARTITION_SIZE,
    FB_TASK_ID_FLIGHT,
    AccessPermission,
    MemoryManager,
    PMSAv8MPU,
    PoolRef,
    RecoveryAction,
    SharedBlock,
)
from recovery import RecoveryStrategy, RetryExhausted, call_with_retry, classify_ipc_enqueue_failure
from runtime_engine import CardState, HistoryRing, HotspotBitmap, JITMultiBufferCache, JITTrace, RuntimeEngine
from scheduler import FB_CONF_MAX_TASKS, Scheduler, TaskState, WaitDir
from system import (
    FB_CONF_GUEST_RAM_SIZE,
    FB_CONF_VSOC_PASSTHROUGH_BASE,
    SYS_CONTROL_HALT,
    SYS_CONTROL_RESET,
    SYS_CONTROL_YIELD,
    FbSyscallId,
    System,
    WasiErrno,
)
from vmmio_concept import (
    FC_PASSTHROUGH,
    FC_SHM,
    FC_STATIC_DEVICE,
    TrapCode,
    VmmioAddress,
    VMMIOController,
)
from wasm_builder import ModuleBuilder
from wasm_reader import WasmParseError, WasmUnsupportedFeatureError, parse


# ===========================================================================
# 1. Tier 1 COOS: Hoare CSP Rendezvous Channel (os_coos_test_spec.md)
# ===========================================================================

def test_coos_01_send_first_suspends_csp():
    """COOS-01: Sender arriving first transitions to SUSPENDED_CSP; value stays in frame."""
    sched = Scheduler()
    ch = sched.create_channel("ch_test")
    t1_id = sched.spawn("t1")
    t1 = sched.get_task(t1_id)
    sched.current_task = t1

    action, _ = sched.channel_send("ch_test", 42)
    assert action == "BLOCK"
    assert t1.state == TaskState.SUSPENDED_CSP
    assert t1.pending_val == 42
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND


def test_coos_02_recv_after_send_completes_rendezvous():
    """COOS-02: Receiver arriving second completes rendezvous and takes ownership."""
    sched = Scheduler()
    sched.create_channel("ch_test")
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t1
    sched.channel_send("ch_test", "DATA_PAYLOAD")

    sched.current_task = t2
    action, _ = sched.channel_recv("ch_test")
    assert action in ("DIRECT_SWITCH", "YIELD")
    assert t2.received_val == "DATA_PAYLOAD"
    assert t1.pending_val is None, "Pending value must be cleared on sender (no double-ownership)"
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_03_recv_first_suspends_csp():
    """COOS-03: Receiver arriving first transitions to SUSPENDED_CSP."""
    sched = Scheduler()
    ch = sched.create_channel("ch_test")
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t2

    action, _ = sched.channel_recv("ch_test")
    assert action == "BLOCK"
    assert t2.state == TaskState.SUSPENDED_CSP
    assert ch.waiter_task == t2
    assert ch.waiter_dir == WaitDir.RECV


def test_coos_04_send_after_recv_completes_rendezvous():
    """COOS-04: Sender arriving second completes rendezvous and transfers ownership."""
    sched = Scheduler()
    sched.create_channel("ch_test")
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t2
    sched.channel_recv("ch_test")

    sched.current_task = t1
    action, _ = sched.channel_send("ch_test", 12345)
    assert action in ("DIRECT_SWITCH", "YIELD")
    assert t2.received_val == 12345
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_05_one_waiter_per_channel_enforced():
    """COOS-05: Only one waiter per channel direction; second waiter asserts."""
    sched = Scheduler()
    sched.create_channel("ch_test")
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t1
    sched.channel_send("ch_test", 1)

    sched.current_task = t2
    try:
        sched.channel_send("ch_test", 2)
        raise AssertionError("Expected AssertionError for second sender on same channel")
    except AssertionError as e:
        assert "separate channels" in str(e)


def test_coos_06_csp_handoff_direct_switch():
    """COOS-06: Rendezvous completion performs direct symmetric handoff to head of READY queue."""
    sched = Scheduler()
    sched.create_channel("ch_test")
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t1
    sched.channel_send("ch_test", 99)

    sched.current_task = t2
    action, target_id = sched.channel_recv("ch_test")
    assert action == "DIRECT_SWITCH"
    assert target_id == t1.task_id
    assert sched._ready[0] == t1, "Target task must be placed at front of READY queue"


def test_coos_07_consecutive_handoff_limit_yields():
    """COOS-07: Consecutive handoff limit (4) forces yield back to main loop."""
    sched = Scheduler(max_handoffs=2)
    sched.create_channel("ch1")
    sched.create_channel("ch2")
    sched.create_channel("ch3")

    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))

    sched.current_task = t1
    sched.channel_send("ch1", 1)
    sched.current_task = t2
    act1, _ = sched.channel_recv("ch1")
    assert act1 == "DIRECT_SWITCH"
    assert sched.consecutive_handoffs == 1

    sched.current_task = t1
    sched.channel_send("ch2", 2)
    sched.current_task = t2
    act2, _ = sched.channel_recv("ch2")
    assert act2 == "DIRECT_SWITCH"
    assert sched.consecutive_handoffs == 2

    sched.current_task = t1
    sched.channel_send("ch3", 3)
    sched.current_task = t2
    act3, _ = sched.channel_recv("ch3")
    assert act3 == "YIELD", "Must yield back to scheduler when consecutive handoffs reach threshold"
    assert sched.consecutive_handoffs == 0


def test_coos_08_interrupt_notification_and_drain():
    """COOS-08: ISR notification queues interrupt without direct mutation; drain wakes task."""
    sched = Scheduler()
    woken = []

    def irq_handler():
        sched.wait_for_interrupt(16)
        yield ("BLOCK", None)
        woken.append("IRQ_PROCESSED")

    sched.spawn("handler", irq_handler())
    sched.run_until_idle()
    assert len(woken) == 0

    sched.notify_interrupt(16)
    sched.run_until_idle()
    assert woken == ["IRQ_PROCESSED"]


def test_coos_09_interrupt_queue_overflow_drops():
    """COOS-09: Overflowing ISR queue drops notification and increments dropped_irqs counter."""
    sched = Scheduler()
    for i in range(16):
        assert sched.notify_interrupt(i)
    # 17th notification must drop
    assert not sched.notify_interrupt(17)
    assert sched.dropped_irqs == 1


# ===========================================================================
# 2. Tier 1 Scheduler: Pure Round-Robin (os_scheduler_test_spec.md)
# ===========================================================================

def test_sched_01_pure_round_robin_fifo():
    """SCHED-01: Pure round-robin execution without priority bias."""
    order: list[str] = []

    def worker(name: str, steps: int):
        for _ in range(steps):
            order.append(name)
            yield None

    sched = Scheduler()
    sched.spawn("a", worker("a", 2))
    sched.spawn("b", worker("b", 2))
    sched.run_to_completion()
    assert order == ["a", "b", "a", "b"]


def test_sched_02_task_capacity_limit():
    """SCHED-02: Scheduler enforces FB_CONF_MAX_TASKS (16) limit."""
    sched = Scheduler(max_tasks=4)
    for i in range(4):
        sched.spawn(f"t{i}")
    try:
        sched.spawn("t_overflow")
        raise AssertionError("Expected RuntimeError for task capacity overflow")
    except RuntimeError as e:
        assert "capacity exceeded" in str(e)


def test_sched_03_duplicate_task_id_rejected():
    """SCHED-03: Attempting to spawn with an existing task_id is rejected."""
    sched = Scheduler()
    sched.spawn("t1", task_id=10)
    try:
        sched.spawn("t2", task_id=10)
        raise AssertionError("Expected ValueError for duplicate task_id")
    except ValueError as e:
        assert "already exists" in str(e)


# ===========================================================================
# 3. Tier 3 Platform Memory: Partitions & SharedBlock RAII (platform_memory_test_spec.md)
# ===========================================================================

def test_mem_01_acquire_partition_fixed_size():
    """MEM-01: acquire-partition provides task-specific fixed partition (no arbitrary size)."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    res = mm.acquire_partition(owner=1)
    assert res.is_ok
    pv = res.unwrap()
    assert pv.size == FB_CONF_PARTITION_SIZE
    assert pv.owner == 1
    assert not hasattr(mm, "allocate"), "Generic heap allocate() must not exist"


def test_mem_02_recovery_strategy_on_exhaustion():
    """MEM-02: Memory exhaustion returns structured error with recovery strategy."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_PARTITION_SIZE)

    assert mm.acquire_partition(owner=1).is_ok
    r2 = mm.acquire_partition(owner=2)
    assert r2.is_err
    assert r2.error.error_code == "ERR_POOL_EXHAUSTED"
    assert r2.error.recovery.action in (RecoveryAction.DEGRADE, RecoveryAction.RETRY)


def test_mem_03_total_allocation_bound():
    """MEM-03: Total allocated bytes never exceeds FB_CONF_MEMORY_POOL_SIZE."""
    mm = MemoryManager()
    pool_size = 128 * 1024
    mm.init_manager(pool_base=0x20020000, pool_size=pool_size)

    for i in range(1, 10):
        res = mm.acquire_partition(owner=i)
        assert mm.total_allocated_bytes <= pool_size
        if res.is_err:
            break


def test_mem_04_owner_task_id_auto_set():
    """MEM-04: Caller task-id is automatically recorded on all allocations."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    p_res = mm.acquire_partition(owner=5)
    assert p_res.unwrap().owner == 5

    s_res = mm.allocate_shared(caller_task_id=5, size=1024)
    assert s_res.unwrap().owner == 5


def test_mem_05_release_and_deallocate_owner_only():
    """MEM-05: Partition release is permitted ONLY by owner task."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    mm.acquire_partition(owner=3)
    assert 3 in mm.partition_owners

    # Rogue task 4 attempts to release task 3's partition
    mm.release_partition(caller_task_id=4)
    assert 3 in mm.partition_owners

    # Owner releases
    mm.release_partition(caller_task_id=3)
    assert 3 not in mm.partition_owners


def test_mem_06_guest_ram_64kb_alignment():
    """MEM-06: pool_base is strictly 64KB aligned."""
    mm = MemoryManager()
    assert mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE).is_ok

    try:
        mm.init_manager(pool_base=0x20021000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
        raise AssertionError("Expected AssertionError for unaligned pool_base")
    except AssertionError as e:
        assert "64KB aligned" in str(e)


def test_mem_10_shared_block_ownership_transfer():
    """MEM-10: allocate-shared -> release -> claim moves ownership cleanly without double-ownership."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    sb_a = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    assert sb_a.get_owner() == 1
    page_idx = sb_a.page_idx

    shm_id = sb_a.release()
    assert not sb_a._is_active
    assert mm.vmmio_registry.get_owner(page_idx) == FB_TASK_ID_FLIGHT

    # Simulate IPC Router Grant phase
    mm.vmmio_registry.update_owner(page_idx, 2)

    sb_b = mm.claim(receiver_task_id=2, shm_id=shm_id).unwrap()
    assert sb_b.get_owner() == 2
    assert sb_b._is_active
    assert mm.vmmio_registry.get_owner(page_idx) == 2


def test_mem_10c_route_message_rollback_restores_owner_id():
    """MEM-10c: Rollback on queue full restores PTE owner_id to sender."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    shm_id = sb.release()
    assert mm.vmmio_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT

    # Rollback on queue full
    mm.rollback_transfer(original_sender_id=1, shm_id=shm_id)
    assert mm.vmmio_registry.get_owner(sb.page_idx) == 1


def test_mem_11_shared_block_raII_auto_deallocate():
    """MEM-11: SharedBlock RAII automatically deallocates buffer on drop."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)

    initial_alloc = mm.total_allocated_bytes
    with mm.allocate_shared(caller_task_id=2, size=1024).unwrap() as sb:
        assert mm.total_allocated_bytes > initial_alloc
        assert sb.shm_id in mm.shm_slots

    assert mm.total_allocated_bytes == initial_alloc
    assert sb.shm_id not in mm.shm_slots


def test_mem_20_mpu_8_regions_static_allocation():
    """MEM-20: 8 MPU regions match the PMSAv8 static allocation table."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    assert len(mpu.regions) == 8
    assert mpu.regions[0].ap == AccessPermission.RO and not mpu.regions[0].xn
    assert mpu.regions[3].ap == AccessPermission.RW and mpu.regions[3].xn
    assert mpu.regions[4].ap == AccessPermission.RO and not mpu.regions[4].xn
    assert mpu.regions[7].ap == AccessPermission.NO_ACCESS


def test_mem_21_jit_code_cache_wx_switch_and_restore():
    """MEM-21 & MEM-22: JIT code cache W^X transaction switching and permanent non-RWX."""
    mpu = PMSAv8MPU(pool_base=0x20020000)
    mpu.assert_no_rwx()

    mpu.begin_jit_patch()
    assert mpu.regions[4].is_writable and not mpu.regions[4].is_executable
    mpu.assert_no_rwx()

    mpu.commit_jit_patch()
    assert mpu.regions[4].is_executable and not mpu.regions[4].is_writable
    mpu.assert_no_rwx()


# ===========================================================================
# 4. Tier 3 Platform HAL & UART / Timer (platform_hal_test_spec.md)
# ===========================================================================

def test_hal_01_uart_transport_is_real_pipe():
    t = UartTransport()
    try:
        assert t.write(b"fireball\n") == 9
        assert t.drain() == b"fireball\n"
        assert t.drain() == b""
    finally:
        t.close()


def test_hal_02_timer_monotonic_ns():
    timer = Timer()
    t1 = timer.get_now_ns()
    time.sleep(0.001)
    t2 = timer.get_now_ns()
    assert t2 > t1


def test_hal_03_shm_pool_rejects_oversized():
    pool = ShmBufferPool()
    try:
        try:
            pool.acquire_buffer(1, size=FB_CONF_HAL_BUFFER_SIZE + 1)
            raise AssertionError("expected ValueError for oversized acquire_buffer")
        except ValueError:
            pass
        handles = [pool.acquire_buffer(1, size=32) for _ in range(FB_CONF_HAL_MAX_BUFFERS)]
        assert len(handles) == FB_CONF_HAL_MAX_BUFFERS
    finally:
        pool.close_all()


def test_hal_04_shm_slice_bounds_and_ownership():
    pool = ShmBufferPool()
    try:
        h = pool.acquire_buffer(task_id=1, size=16)
        view = pool.view(1, h, 0, 16)
        assert len(view) == 16
        try:
            pool.view(2, h, 0, 16)
            raise AssertionError("expected ShmTrap: task 2 does not own handle")
        except ShmTrap:
            pass
    finally:
        pool.close_all()


# ===========================================================================
# 5. Tier 1 Logging & Recovery (system_logging_test_spec.md)
# ===========================================================================

def test_log_01_dictionary_rejects_pointer_specifiers():
    d = LogDictionary()
    d.register(0x01, "ok: %d %d")
    for bad in ("bad: %s", "bad: %p", "bad: %c"):
        try:
            d.register(0x02, bad)
            raise AssertionError("expected ValueError for pointer-shaped specifier")
        except ValueError:
            pass


def test_log_02_logger_ring_buffer_overwrites():
    t = UartTransport()
    try:
        d = LogDictionary()
        d.register(0x01, "event #%d")
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=4)
        for i in range(6):
            logger.log_event(LogLevel.INFO, 0x01, i)
        assert logger.ring.overwrite_count == 2
        flushed = logger.flush()
        assert flushed == 4
        wire = t.drain().decode()
        assert "event #2" in wire and "event #5" in wire
    finally:
        t.close()


def test_recovery_retry_and_escalation():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        return calls["n"] >= 2

    assert call_with_retry(flaky, sleep=lambda _s: None) == 2
    assert classify_ipc_enqueue_failure(queue_was_full=True) == RecoveryStrategy.RETRY


# ===========================================================================
# 6. Tier 3 JIT Hotspot Profiling & 3-Bank Cache (jit_compiler_test_spec.md, jit_runtime_test_spec.md)
# ===========================================================================

def test_hotspot_01_2bit_card_marking_state_transitions():
    """HOTSPOT-01: 2-bit state machine: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)."""
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
    assert bitmap.touch(pc) == CardState.COMPILED


def test_hotspot_02_history_ring_buffered_yield_drain():
    """HOTSPOT-02: Interpreter records basic-block heads to HistoryRing, drained on yield."""
    ring = HistoryRing(capacity=8)
    for i in range(10):
        ring.record(0x1000 + i * 4)
    assert ring.dropped == 2
    drained = ring.drain()
    assert len(drained) == 8
    assert len(ring.drain()) == 0


def test_hotspot_03_lifo_compile_queue_batch_drain():
    """HOTSPOT-03: HOT traces are queued to LIFO compile queue and batch-compiled into Active bank."""
    compiled_traces = []
    def dummy_compiler(pc: int) -> JITTrace:
        t = JITTrace(head_pc=pc, native_fn=lambda: pc * 2, size_bytes=64)
        compiled_traces.append(pc)
        return t

    engine = RuntimeEngine(jit_compiler=dummy_compiler, yield_threshold=4)
    # Touch PC 0x200 multiple times to promote to HOT
    engine.record_block_head(0x200)
    engine.record_block_head(0x200)
    engine.record_block_head(0x300)
    engine.record_block_head(0x300)
    # Trigger yield threshold
    engine.on_yield()

    assert 0x200 in engine.compile_queue
    assert 0x300 in engine.compile_queue

    count = engine.drain_compile_queue()
    assert count == 2
    assert engine.bitmap.get_state(0x200) == CardState.COMPILED
    assert engine.bitmap.get_state(0x300) == CardState.COMPILED
    assert engine.cache.lookup(0x200) is not None


def test_hotspot_04_3bank_cache_oldest_only_promotion():
    """HOTSPOT-04: Multi-bank cache promotes traces from Oldest bank to Active bank upon hit."""
    cache = JITMultiBufferCache(bank_capacity=256)
    t1 = JITTrace(0x10, lambda: 1, size_bytes=64)
    assert cache.insert(t1)
    assert cache.active.has_trace(0x10)

    # Rotate twice: Active -> Warm -> Oldest
    cache.rotate()
    assert cache.warm.has_trace(0x10)
    # Warm bank hit does NOT promote
    assert cache.lookup(0x10) is t1
    assert cache.promotions == 0

    cache.rotate()
    assert cache.oldest.has_trace(0x10)
    # Oldest bank hit MUST promote immediately to Active
    promoted = cache.lookup(0x10)
    assert promoted is t1
    assert cache.promotions == 1
    assert cache.active.has_trace(0x10)
    assert not cache.oldest.has_trace(0x10)


def test_hotspot_05_3bank_cache_rotation_and_eviction_resets_card():
    """HOTSPOT-05: Oldest bank eviction unlinks inbound sources and resets card state to EXECUTED."""
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
    assert bitmap.get_state(0x50) == CardState.EXECUTED, "Evicted trace must revert card state to EXECUTED (01)"


# ===========================================================================
# 7. Tier 2 vMMIO: 3-Tier Gate & FC=14 SHM Ownership (runtime_vmmio_test_spec.md)
# ===========================================================================

def test_vmmio_01_three_tier_gate_dispatch():
    """VMMIO-01: 3-tier address gate resolves Linear RAM, Static Devices, and SHM/Passthrough."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    ctrl.map_static_device(0xC0000)
    ctrl.map_passthrough_page(vpn=0xF0000, phys_page=1)

    # Linear RAM (Tier 1)
    stat, detail = ctrl.access(raw_addr=0x1000, is_write=False, current_task_id=0)
    assert stat == "OK_GUEST_RAM"

    # Static Device (Tier 2, FC=12)
    stat, detail = ctrl.access(raw_addr=0xC000_0000, is_write=True, current_task_id=0)
    assert stat == "OK_SYSCALL"

    # Passthrough (Tier 3, FC=15)
    stat, detail = ctrl.access(raw_addr=0xF000_0000, is_write=False, current_task_id=0)
    assert stat == "OK_PHYSICAL"

    # Out of Bounds Linear RAM
    stat, _ = ctrl.access(raw_addr=0x10000, is_write=False, current_task_id=0)
    assert stat == TrapCode.OUT_OF_BOUNDS


def test_vmmio_02_fc14_shm_owner_isolation_and_flight():
    """VMMIO-02: FC=14 shared memory enforces owner_id match and traps FLIGHT state."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    ctrl.map_shm_page(vpn=0xE0000, phys_page=2, owner_id=1)

    # Owner 1 access OK
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    assert stat == "OK_PHYSICAL"

    # Rogue task 2 access TRAPS
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=2)
    assert stat == TrapCode.OWNER_MISMATCH

    # In-flight access TRAPS for all tasks
    ctrl.revoke_shm_owner(vpn=0xE0000)
    stat, _ = ctrl.access(raw_addr=0xE000_0000, is_write=True, current_task_id=1)
    assert stat == TrapCode.OWNER_MISMATCH


def test_vmmio_03_undefined_function_code_traps():
    """VMMIO-03: Undefined FC (0x0..0xB, 0xD) immediately traps."""
    ctrl = VMMIOController(guest_ram_size=64 * 1024)
    stat, _ = ctrl.access(raw_addr=0xD000_0000, is_write=False, current_task_id=1)
    assert stat == TrapCode.UNDEFINED_FC


# ===========================================================================
# 8. Tier 1 IPC Router & Zero-Copy SharedBlock Transfer (ipc_router_test_spec.md)
# ===========================================================================

def test_ipc_01_uri_lookup_and_permission_matrix():
    """IPC-01: Service URI lookup and role-based access control."""
    router = IPCRouter()
    entry = router.registry.find("fireball://hal/gpio/0")
    assert entry is not None
    assert entry["channel_id"] == "ch_gpio"
    assert entry["role"] == "PLATFORM_HAL"

    msg1 = IPCMessage(resource_id="msg1", payload={"cmd": "PIN_HIGH"})
    # CLIENT_APP has permission
    status, _ = router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg1)
    assert status == "OK_ENQUEUED"

    # UNKNOWN role has NO permission
    msg2 = IPCMessage(resource_id="msg2", payload={"cmd": "PIN_HIGH"})
    status_bad, _ = router.route_message("UNKNOWN_ROLE", "fireball://hal/gpio/0", msg2)
    assert status_bad == "ERR_PERMISSION_DENIED"


def test_ipc_02_e2e_shared_block_transfer():
    """IPC-02: End-to-end zero-copy SharedBlock transfer via IPC router."""
    sysv = System()
    try:
        # Sender allocates SharedBlock
        sb = sysv.memory_manager.allocate_shared(caller_task_id=1, size=256).unwrap()
        assert sb.get_owner() == 1
        addr = sb.get_address()
        assert addr >= 0x20020000

        # Sender releases to FLIGHT
        shm_id = sb.release()
        assert sysv.memory_manager.vmmio_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT

        # Route via IPC
        msg = IPCMessage(resource_id="shm_msg", payload={"shm_id": shm_id})
        status, _ = sysv.ipc.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg)
        assert status == "OK_ENQUEUED"

        # Receiver retrieves message from ch_gpio and claims block
        recv_msg = sysv.ipc.receive_message("ch_gpio")
        assert recv_msg is not None
        recv_shm_id = recv_msg.payload["shm_id"]

        # Grant to task 2 and claim
        sysv.memory_manager.vmmio_registry.update_owner(sb.page_idx, 2)
        recv_sb = sysv.memory_manager.claim(receiver_task_id=2, shm_id=recv_shm_id).unwrap()
        assert recv_sb.get_owner() == 2
        assert recv_sb.get_address() == addr
    finally:
        sysv.shutdown()


def test_ipc_03_queue_full_rollback_restores_owner():
    """IPC-03: Enqueue failure on full queue rolls back SharedBlock ownership to sender."""
    sysv = System()
    try:
        uri = "fireball://hal/gpio/0"   # Max queue = 2
        sysv.ipc.route_message("CLIENT_APP", uri, IPCMessage("m1", {}))
        sysv.ipc.route_message("CLIENT_APP", uri, IPCMessage("m2", {}))

        # Third message with SharedBlock
        sb = sysv.memory_manager.allocate_shared(caller_task_id=1, size=256).unwrap()
        shm_id = sb.release()

        msg3 = IPCMessage("m3", {"shm_id": shm_id})
        status, _ = sysv.ipc.route_message("CLIENT_APP", uri, msg3)
        assert status == "ERR_QUEUE_FULL"

        # Rollback
        sysv.memory_manager.rollback_transfer(original_sender_id=1, shm_id=shm_id)
        assert sysv.memory_manager.vmmio_registry.get_owner(sb.page_idx) == 1
    finally:
        sysv.shutdown()


# ===========================================================================
# 9. fireball_call Full Syscall Surface (system_syscall_test_spec.md)
# ===========================================================================

def test_syscall_01_unknown_id_returns_nosys():
    sysv = System()
    try:
        assert sysv.fireball_call(0xDEAD, 0, 0, 0, 0, 0, 0) == WasiErrno.NOSYS
    finally:
        sysv.shutdown()


def test_syscall_02_sys_control_registers():
    sysv = System()
    try:
        assert sysv.fireball_call(FbSyscallId.SYS_YIELD, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.SYS_RESET, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.reset_requested
        assert sysv.fireball_call(FbSyscallId.SYS_HALT, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.halted
    finally:
        sysv.shutdown()


def test_syscall_03_mmio_read_write():
    sysv = System()
    try:
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        assert sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xCAFEBABE, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, 0, 0, 0, 0, 0) == 0xCAFEBABE
    finally:
        sysv.shutdown()


def test_syscall_04_vdma_transfer():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        guest_mem[0:4] = struct.pack("<I", 0x11223344)
        sysv.bind_guest(guest_mem, task_id=1)

        dst = FB_CONF_VSOC_PASSTHROUGH_BASE + 0x1000
        assert sysv.fireball_call(FbSyscallId.VDMA_START, 0, dst, 4, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, dst, 0, 0, 0, 0, 0) == 0x11223344
    finally:
        sysv.shutdown()


def test_syscall_05_irq_flags():
    sysv = System()
    try:
        sysv.raise_irq(0x4)
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0x4
        assert sysv.fireball_call(FbSyscallId.IRQ_CLEAR, 0x4, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0
    finally:
        sysv.shutdown()


def test_syscall_06_ipc_lookup_send_recv():
    sysv = System()
    try:
        guest_mem = bytearray(128)
        uri = b"fireball://hal/gpio/0"
        guest_mem[0:len(uri)] = uri
        payload = b"SET_GPIO"
        guest_mem[64:64 + len(payload)] = payload
        sysv.bind_guest(guest_mem, task_id=1)

        handle = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri), 0, 0, 0, 0)
        assert handle > 0
        assert sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 64, len(payload), 0, 0, 0) == WasiErrno.SUCCESS
        recv_len = sysv.fireball_call(FbSyscallId.IPC_RECV, handle, 96, 32, 0, 0, 0)
        assert recv_len == len(payload)
        assert bytes(guest_mem[96:96 + recv_len]) == payload
    finally:
        sysv.shutdown()


def test_syscall_07_wasi_fd_write():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        message = b"hello from wasm\n"
        guest_mem[32:32 + len(message)] = message
        struct.pack_into("<II", guest_mem, 0, 32, len(message))
        sysv.bind_guest(guest_mem, task_id=1)

        assert sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        assert sysv.transport.drain() == message
    finally:
        sysv.shutdown()


# ===========================================================================
# 10. WASM Instruction Set MVP (wasm_instruction_set_test_spec.md)
# ===========================================================================

def test_wasm_01_to_06_unsupported_features_rejected():
    """WASM-01..06: Unsupported features (SIMD, threads, tail-call) are rejected with error code."""
    builder = ModuleBuilder()
    fb = builder.add_function(params=(), results=("i32",), export_name="test_simd")
    # Emit unsupported SIMD prefix opcode 0xFD
    fb.code.append(0xFD)
    fb.code.append(0x00)
    fb.end()

    wasm_bytes = builder.build()
    mod = parse(wasm_bytes)
    try:
        interp = Interpreter(mod)
        interp.call(0, [])
        raise AssertionError("Expected WasmUnsupportedFeatureError for SIMD opcode")
    except WasmUnsupportedFeatureError as e:
        assert "ERR_WASM_UNSUPPORTED_FEATURE" in str(e)


def test_wasm_10_to_15_control_flow_and_calls():
    """WASM-10..15: Unreachable trap, block/loop/if/br_table, call, and call_indirect."""
    builder = ModuleBuilder()
    builder.add_table(min_size=2, max_size=2)

    # f0: unreachable trap
    f0 = builder.add_function(params=(), results=(), export_name="unreachable_fn")
    f0.unreachable().end()

    # f1: loop + br_table
    f1 = builder.add_function(params=("i32",), results=("i32",), export_name="calc_fn")
    f1.block()
    f1.block()
    f1.local_get(0)
    f1.br_table([0, 1], 0)
    f1.end()
    f1.i32_const(100).return_()
    f1.end()
    f1.i32_const(200).return_()
    f1.end()

    # f2: indirect caller
    f2 = builder.add_function(params=("i32", "i32"), results=("i32",), export_name="call_ind")
    f2.local_get(0)   # arg to target
    f2.local_get(1)   # table index
    f2.call_indirect(type_index=1, table_index=0)
    f2.end()

    builder.add_element(table_index=0, offset=0, func_indices=[1, 1])

    wasm_bytes = builder.build()
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)

    # WASM-10: unreachable traps
    try:
        interp.call(mod.export_func_index("unreachable_fn"), [])
        raise AssertionError("Expected Trap for unreachable")
    except Trap:
        pass

    # WASM-13: br_table branch resolution
    assert interp.call(mod.export_func_index("calc_fn"), [0]) == [100]
    assert interp.call(mod.export_func_index("calc_fn"), [1]) == [200]

    # WASM-15: call_indirect
    assert interp.call(mod.export_func_index("call_ind"), [0, 0]) == [100]
    assert interp.call(mod.export_func_index("call_ind"), [1, 1]) == [200]


def test_wasm_20_21_drop_and_select():
    """WASM-20..21: drop and select parametric instructions."""
    builder = ModuleBuilder()
    fb = builder.add_function(params=("i32", "i32", "i32"), results=("i32",), export_name="sel")
    fb.local_get(0)
    fb.drop()         # drops param 0
    fb.local_get(1)   # val1 (if cond != 0)
    fb.local_get(2)   # val2 (if cond == 0)
    fb.local_get(0)   # cond
    fb.select()
    fb.end()

    mod = parse(builder.build())
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("sel"), [1, 10, 20]) == [10]
    assert interp.call(mod.export_func_index("sel"), [0, 10, 20]) == [20]


def test_wasm_30_31_locals_and_globals():
    """WASM-30..31: local.get/set/tee and global.get/set."""
    builder = ModuleBuilder()
    g_idx = builder.add_global(vtype="i32", mutable=True, init_value=42)

    fb = builder.add_function(params=("i32",), results=("i32",), locals_extra=["i32"], export_name="loc_glob")
    # local.tee: set local 1 and keep on stack
    fb.local_get(0)
    fb.local_tee(1)
    # global.set
    fb.global_set(g_idx)
    # global.get + local 1
    fb.global_get(g_idx)
    fb.local_get(1)
    fb.i32_add()
    fb.end()

    mod = parse(builder.build())
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("loc_glob"), [5]) == [10]
    assert interp.globals[0] == 5


def test_wasm_40_to_46_memory_load_store_grow_and_data():
    """WASM-40..46 & WASM-60: Linear memory load, store, size, grow, bounds traps, and Data segments."""
    builder = ModuleBuilder()
    builder.add_memory(min_pages=1, max_pages=2)
    # Add initial data segment: string "WASM_INIT" at offset 0
    builder.add_data_segment(offset=0, data=b"WASM_INIT")

    fb = builder.add_function(params=(), results=("i32",), export_name="mem_ops")
    # Read first 4 bytes as i32
    fb.i32_const(0)
    fb.i32_load(align=2, offset=0)
    # Write 0x12345678 to offset 16
    fb.i32_const(16)
    fb.i32_const(0x12345678)
    fb.i32_store(align=2, offset=0)
    # Grow memory by 1 page
    fb.i32_const(1)
    fb.memory_grow()
    fb.drop()
    # Return memory.size
    fb.memory_size()
    fb.end()

    # Function that attempts out-of-bounds access
    fb_oob = builder.add_function(params=(), results=(), export_name="trap_oob")
    fb_oob.i32_const(0x1000000)   # Out of bounds offset
    fb_oob.i32_load(align=2, offset=0)
    fb_oob.drop()
    fb_oob.end()

    mod = parse(builder.build())
    mem = bytearray(65536)
    interp = Interpreter(mod, memory=mem)

    # Initial data check
    assert bytes(mem[0:9]) == b"WASM_INIT"

    # Execution
    pages = interp.call(mod.export_func_index("mem_ops"), [])
    assert pages == [2]
    assert struct.unpack_from("<I", mem, 16)[0] == 0x12345678

    # OOB trap check
    try:
        interp.call(mod.export_func_index("trap_oob"), [])
        raise AssertionError("Expected Trap on out of bounds memory access")
    except Trap:
        pass


def test_wasm_50_to_56_integer_arithmetic_and_bitwise():
    """WASM-50..56: 32-bit integer arithmetic, div-by-zero trap, popcnt, clz, rotl, rotr."""
    builder = ModuleBuilder()

    # Div by zero
    fb_div = builder.add_function(params=("i32", "i32"), results=("i32",), export_name="div_s")
    fb_div.local_get(0).local_get(1).i32_div_s().end()

    # Bit counts and rotation
    fb_bit = builder.add_function(params=("i32",), results=("i32",), export_name="bit_ops")
    fb_bit.local_get(0).i32_popcnt()   # popcnt(x)
    fb_bit.local_get(0).i32_clz()      # clz(x)
    fb_bit.i32_add()
    fb_bit.local_get(0).i32_const(4).i32_rotl()  # rotl(x, 4)
    fb_bit.i32_xor()
    fb_bit.end()

    mod = parse(builder.build())
    interp = Interpreter(mod)

    # WASM-54: Div by zero traps
    try:
        interp.call(mod.export_func_index("div_s"), [10, 0])
        raise AssertionError("Expected Trap on division by zero")
    except Trap:
        pass

    # Normal div
    assert interp.call(mod.export_func_index("div_s"), [10, 2]) == [5]

    # WASM-52, 55, 56: Bit ops
    # x = 0x80000001 -> popcnt=2, clz=0 -> sum=2. rotl(x, 4) = 0x00000018. 2 ^ 0x18 = 0x1A (26)
    assert interp.call(mod.export_func_index("bit_ops"), [0x80000001]) == [26]


# ===========================================================================
# Test Runner
# ===========================================================================

ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"\n[PASS] All {len(ALL_TESTS)} comprehensive pysim invariant tests passed.")
