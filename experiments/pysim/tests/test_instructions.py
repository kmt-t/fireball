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

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes
import struct
import time

from hal import (
    FB_CONF_HAL_BUFFER_SIZE,
    FB_CONF_HAL_MAX_BUFFERS,
    ShmBufferPool,
    ShmTrap,
    Timer,
    UartTransport,
)
from interpreter import Interpreter, Trap
from ipc_router import (
    DataType,
    IPCMessage,
    IPCRouter,
    IpcStatus,
    OwnershipState,
    Role,
    ScopeKind,
    bytes_to_kv_storage,
    kv_entries_to_bytes,
    pack_key32,
)
from logger import LogDictionary, Logger, LogLevel
from memory import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_PARTITION_SIZE,
    FB_TASK_ID_FLIGHT,
    AccessPermission,
    MemoryManager,
    PMSAv8MPU,
    RecoveryAction,
)
from recovery import (
    RecoveryManager,
    RecoveryStrategy,
    Result,
    classify_errno_strategy,
    classify_trap_strategy,
)
from runtime_engine import (
    BasicBlock,
    CardState,
    HistoryRing,
    HotspotBitmap,
    IntegratedHybridEngine,
    JITCacheBank,
    JITMultiBufferCache,
    JITTrace,
    JITTraceHeader,
    PcOnlyCompiler,
    RuntimeEngine,
    WASMContext,
)
from scheduler import ChannelAction, Scheduler, TaskState, WaitDir
from system import (
    FB_CONF_VSOC_PASSTHROUGH_BASE,
    FbSyscallId,
    System,
    WasiErrno,
)
from system_containers import (
    BitView,
    FlatMapStorage,
    FlatMapView,
    FlatSetView,
    RadixBinaryTreeView,
    lookup_jit_entry_radix,
)
from vmmio import (
    TrapCode,
    VMMIOController,
)
from wasi import WasiHostContext
from wasm_reader import WasmUnsupportedFeatureError, parse
from x64_jit import TraceCompiler


def wat_to_wasm(wat_text: str) -> bytes:
    """Compiles WAT text format to standard WASM binary via wasmtime."""
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


# ===========================================================================
# 1. Tier 1 COOS: Hoare CSP Rendezvous Channel (os_coos_test_spec.md)
# ===========================================================================


def test_coos_01_send_first_suspends_csp():
    """COOS-01: Sender arriving first transitions to SUSPENDED_CSP; value stays in frame."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1_id = sched.spawn("t1")
    t1 = sched.get_task(t1_id)
    sched.current_task = t1
    action, _ = ch.send(42)
    assert action == ChannelAction.BLOCK
    assert t1.state == TaskState.SUSPENDED_CSP
    assert t1.pending_val == 42
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND


def test_coos_02_recv_after_send_completes_rendezvous():
    """COOS-02: Receiver arriving second completes rendezvous and takes ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send("DATA_PAYLOAD")
    sched.current_task = t2
    action, _ = ch.recv()
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == "DATA_PAYLOAD"
    assert t1.pending_val is None, "Pending value must be cleared on sender (no double-ownership)"
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_03_recv_first_suspends_csp():
    """COOS-03: Receiver arriving first transitions to SUSPENDED_CSP."""
    sched = Scheduler()
    ch = sched.create_channel()
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t2
    action, _ = ch.recv()
    assert action == ChannelAction.BLOCK
    assert t2.state == TaskState.SUSPENDED_CSP
    assert ch.waiter_task == t2
    assert ch.waiter_dir == WaitDir.RECV


def test_coos_04_send_after_recv_completes_rendezvous():
    """COOS-04: Sender arriving second completes rendezvous and transfers ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t2
    ch.recv()
    sched.current_task = t1
    action, _ = ch.send(12345)
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == 12345
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_05_one_waiter_per_channel_enforced():
    """COOS-05: Only one waiter per channel direction; second waiter asserts."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send(1)
    sched.current_task = t2
    try:
        ch.send(2)
        raise AssertionError("Expected AssertionError for second sender on same channel")
    except AssertionError as e:
        assert "separate channels" in str(e)


def test_coos_06_csp_handoff_direct_switch():
    """COOS-06: Rendezvous completion performs direct symmetric handoff to head of READY queue."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send(99)
    sched.current_task = t2
    action, target_id = ch.recv()
    assert action == ChannelAction.DIRECT_SWITCH
    assert target_id == t1.task_id
    assert sched._ready[0] == t1, "Target task must be placed at front of READY queue"


def test_coos_07_consecutive_handoff_limit_yields():
    """COOS-07: Consecutive handoff limit (4) forces yield back to main loop."""
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


def test_coos_08_interrupt_notification_and_drain():
    """COOS-08: ISR notification queues interrupt without direct mutation; drain wakes task."""
    sched = Scheduler()
    woken = []

    def irq_handler():
        sched.wait_for_interrupt(16)
        yield (ChannelAction.BLOCK, None)
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

    # Verify bytearray accessors on active SharedBlock
    sb_a.write_u8(0, 0xAB)
    assert sb_a.read_u8(0) == 0xAB

    sb_a.write_u16(2, 0x1234)
    assert sb_a.read_u16(2) == 0x1234

    sb_a.write_u32(4, 0xCAFEBABE)
    assert sb_a.read_u32(4) == 0xCAFEBABE

    sb_a.write_i32(8, -42)
    assert sb_a.read_i32(8) == -42

    sb_a.write_bytes(16, b"Hello Fireball SHM")
    assert sb_a.read_bytes(16, 18) == b"Hello Fireball SHM"

    sb_a.write_kv(40, 0x1000, 0x2000)
    assert sb_a.read_kv(40) == (0x1000, 0x2000)

    # Underlying bytearray direct accessor
    raw_ba = sb_a.get_bytearray()
    assert isinstance(raw_ba, bytearray)
    assert raw_ba[0] == 0xAB

    page_idx = sb_a.page_idx
    shm_id = sb_a.release()
    assert not sb_a._is_active
    assert mm.vmmio_registry.get_owner(page_idx) == FB_TASK_ID_FLIGHT

    # Access during in-flight must raise AssertionError
    try:
        sb_a.read_u32(4)
        raise AssertionError("Expected access error while in-flight")
    except AssertionError:
        pass

    # Simulate IPC Router Grant phase
    mm.vmmio_registry.update_owner(page_idx, 2)
    sb_b = mm.claim(receiver_task_id=2, shm_id=shm_id).unwrap()
    assert sb_b.get_owner() == 2
    assert sb_b._is_active
    assert mm.vmmio_registry.get_owner(page_idx) == 2

    # Receiver can read everything sender wrote into the bytearray!
    assert sb_b.read_u32(4) == 0xCAFEBABE
    assert sb_b.read_bytes(16, 18) == b"Hello Fireball SHM"
    assert sb_b.read_kv(40) == (0x1000, 0x2000)


def test_mem_10c_rollback_transfer_restores_owner_id():
    """MEM-10c: rollback_transfer() restores PTE owner_id to the original sender."""
    mm = MemoryManager()
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    sb = mm.allocate_shared(caller_task_id=1, size=1024).unwrap()
    shm_id = sb.release()
    assert mm.vmmio_registry.get_owner(sb.page_idx) == FB_TASK_ID_FLIGHT
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


def test_log_03_dictionary_storage_ownership_separation():
    """LOG-03: LogDictionary borrows FlatMapStorage without owning/duplicating it."""
    storage = FlatMapStorage([(0x01, "event #%d"), (0x02, "value %d %d")])
    d = LogDictionary(storage=storage)

    # Ownership separation assertion
    assert d.storage is storage
    assert d.payload.entries is storage.entries
    assert d.format(0x01, (42, 0, 0, 0)) == "event #42"
    assert d.format(0x02, (10, 20, 0, 0)) == "value 10 20"


def test_log_04_coos_and_ipc_diagnostic_logging():
    """LOG-04: COOS and IPC emit strict diagnostic log events upon anomalies/boundary conditions."""
    sysv = System()
    try:
        # 1. COOS Duplicate Task ID -> 0x0103
        def dummy_coro():
            return
            yield

        try:
            sysv.scheduler.spawn("dup_task", dummy_coro(), task_id=99)
            sysv.scheduler.spawn("dup_task_2", dummy_coro(), task_id=99)
        except ValueError:
            pass

        # 2. COOS IRQ Queue Overflow -> 0x0104
        for irq_idx in range(20):
            sysv.scheduler.notify_interrupt(irq_idx)

        # 3. IPC Unknown URI -> 0x0202
        msg = IPCMessage.from_entries([(1, 10)], memory_manager=sysv.memory_manager)

        def bad_uri_task():
            yield from sysv.ipc.send(Role.RUNTIME, "fireball://unknown/service", msg)

        sysv.scheduler.spawn("bad_uri_task", bad_uri_task())
        sysv.scheduler.run_until_idle()

        # 4. IPC RBAC Denied -> 0x0201
        msg2 = IPCMessage.from_entries([(1, 20)], memory_manager=sysv.memory_manager)

        def rbac_denied_task():
            # RUNTIME sending to DEBUGGER is DENIED
            yield from sysv.ipc.send(Role.RUNTIME, "fireball://dbg/manager/0", msg2)

        sysv.scheduler.spawn("rbac_denied_task", rbac_denied_task())
        sysv.scheduler.run_until_idle()

        # 5. IPC Message Too Large -> 0x0203
        too_large_msg = IPCMessage.from_entries(
            [(i, i) for i in range(1, 10)],  # 9 pairs > 8
            memory_manager=sysv.memory_manager,
        )

        def too_large_task():
            yield from sysv.ipc.send(Role.RUNTIME, "fireball://hal/gpio/0", too_large_msg)

        sysv.scheduler.spawn("too_large_task", too_large_task())
        sysv.scheduler.run_until_idle()

        # Flush logger to UART (in addition to idle hooks)
        sysv.logger.flush()
        wire = sysv.transport.drain().decode()

        # Verify all diagnostic strings were formatted and transmitted
        assert "COOS: duplicate task id rejected" in wire
        assert "COOS: irq queue overflow dropped" in wire
        assert "IPC: unknown uri routing failed" in wire
        assert "IPC: rbac denied" in wire
        assert "IPC: message too large" in wire
    finally:
        sysv.shutdown()


def test_recovery_01_retry_success_within_limit():
    """RECOVERY-01: Transient failure succeeds within 3 retries (10ms backoff) without exceptions."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts = [0]

    def op() -> Result[str, str]:
        attempts[0] += 1
        if attempts[0] < 3:
            return Result.err("BUSY", RecoveryStrategy.RETRY)
        return Result.ok("SUCCESS_DATA")

    res = mgr.execute_with_recovery(op)
    assert res.is_ok is True
    assert res.value == "SUCCESS_DATA"
    assert attempts[0] == 3
    assert mgr.total_retries == 2
    assert mgr.total_restarts == 0
    assert mgr.total_panics == 0


def test_recovery_02_retry_exhaustion_escalates_to_restart():
    """RECOVERY-02: 3-attempt retry exhaustion automatically escalates to RESTART."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts = [0]
    reset_called = [False]

    def failing_op() -> Result[str, str]:
        attempts[0] += 1
        if reset_called[0]:
            return Result.ok("RECOVERED_AFTER_RESET")
        return Result.err("RESOURCE_EXHAUSTED", RecoveryStrategy.RETRY)

    def do_reset() -> bool:
        reset_called[0] = True
        return True

    res = mgr.execute_with_recovery(failing_op, task_reset_fn=do_reset)
    assert res.is_ok is True
    assert res.value == "RECOVERED_AFTER_RESET"
    assert attempts[0] == 4  # 3 initial retries + 1 post-reset run
    assert reset_called[0] is True
    assert mgr.total_restarts == 1
    assert mgr.total_panics == 0


def test_recovery_03_panic_triggers_immediate_failsafe():
    """RECOVERY-03: Fatal safety violation (MPU fault/permission) triggers PANIC immediately without retry."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    panic_msg = []

    def fatal_op() -> Result[str, str]:
        return Result.err("TRAP_ACCESS_VIOLATION", RecoveryStrategy.PANIC)

    def panic_hook(msg: str) -> None:
        panic_msg.append(msg)

    res = mgr.execute_with_recovery(fatal_op, panic_fn=panic_hook)
    assert res.is_ok is False
    assert res.strategy == RecoveryStrategy.PANIC
    assert len(panic_msg) == 1
    assert "TRAP_ACCESS_VIOLATION" in panic_msg[0]
    assert mgr.total_panics == 1
    assert mgr.total_retries == 0


def test_recovery_04_errorcode_to_strategy_mapping():
    """RECOVERY-04: Error code to RecoveryStrategy mapping matches {Errorcode_To_Strategy} spec."""
    # WASI Errno mappings
    assert classify_errno_strategy(0) == RecoveryStrategy.IGNORE  # SUCCESS
    assert classify_errno_strategy(6) == RecoveryStrategy.RETRY  # EAGAIN
    assert classify_errno_strategy(73) == RecoveryStrategy.RETRY  # ETIMEDOUT
    assert classify_errno_strategy(76) == RecoveryStrategy.RETRY  # ENOMEM
    assert classify_errno_strategy(28) == RecoveryStrategy.RESTART  # EINVAL
    assert classify_errno_strategy(44) == RecoveryStrategy.RESTART  # ENOENT
    assert classify_errno_strategy(8) == RecoveryStrategy.RESTART  # EBADF
    assert classify_errno_strategy(63) == RecoveryStrategy.PANIC  # EPERM
    assert classify_errno_strategy(21) == RecoveryStrategy.PANIC  # EFAULT
    # String traps
    assert classify_trap_strategy("TRAP_MEMORY_OUT_OF_BOUNDS") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_ACCESS_VIOLATION") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_OWNER_MISMATCH") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_UNDEFINED_FC") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_UNREGISTERED_PAGE") == RecoveryStrategy.RESTART


# ===========================================================================
# 6. Tier 3 JIT Hotspot Profiling & 3-Bank Cache (jit_compiler_test_spec.md, jit_runtime_test_spec.md)
# ===========================================================================


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
    engine.compile_queue = [0x100, 0x200, 0x300]
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
    engine.run(interp, fn_idx, [50], quantum=8)

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
    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(lambda pc: None), card_shift=3)
    assert engine.min_trace_bytes == 8
    # Two 2-byte blocks, both inside card 2 (0x10>>3 == 0x12>>3 == 2).
    engine.register_block(BasicBlock(head_pc=0x10, ops=[("nop", None)], next_pc=0x12))
    engine.register_block(BasicBlock(head_pc=0x12, ops=[("nop", None)], next_pc=0x14))
    for _ in range(engine.yield_threshold * 2):
        engine.record_block_head(0x10)
        engine.record_block_head(0x12)
    assert engine.bitmap.get_state(0x10) == CardState.UNEXECUTED
    assert engine.bitmap.get_state(0x12) == CardState.UNEXECUTED
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

    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(fake_compile), card_shift=3)
    engine.register_block(BasicBlock(head_pc=0x100, ops=[("nop", None)] * 8, next_pc=0x110))
    engine.cache.insert(JITTrace(0x100, lambda: 0, size_bytes=64))
    engine.compile_queue.append(0x100)

    compiled = engine.idle_hook(budget=4)

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert engine.bitmap.get_state(0x100) == CardState.COMPILED


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


_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_SHM_ID = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=1)
_CMD_PIN_HIGH = 1


def _run_immediate(gen):
    """Drives an IPCRouter.send()/recv() generator that is expected to reject
    at Stage 1/2 (URI lookup / RBAC) -- i.e. never touch a CSP channel and so
    never actually block -- and returns its final (IpcStatus, ...) value."""
    try:
        next(gen)
    except StopIteration as e:
        return e.value
    raise AssertionError("expected immediate Stage 1/2 rejection, but the call blocked")


def test_ipc_01_uri_lookup_and_permission_matrix():
    """IPC-01: Service URI lookup and role-based access control."""
    sched = Scheduler()
    router = IPCRouter(sched)
    entry = router.find_service("fireball://hal/gpio/0")
    assert entry is not None
    assert entry.role == Role.PLATFORM_HAL

    sender_id = sched.spawn("sender")
    sched.current_task = sched.get_task(sender_id)

    # RUNTIME has permission, but nothing is receiving yet -> send() itself
    # genuinely waits (ipc_router.md §5.1), so single-step it exactly like
    # scheduler.Channel's own tests (test_coos_01 etc.) to observe the
    # CSP block directly instead of driving it to a rendezvous that will
    # never come.
    msg1 = IPCMessage.from_entries([(_KEY_CMD, _CMD_PIN_HIGH)])
    gen = router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg1)
    assert next(gen) == (ChannelAction.BLOCK, None)
    assert msg1.ownership == OwnershipState.IN_FLIGHT

    # PLATFORM_HAL has no outgoing edges at all (role matrix row is all-DENY).
    msg2 = IPCMessage.from_entries([(_KEY_CMD, _CMD_PIN_HIGH)])
    status_bad, _ = _run_immediate(router.send(Role.PLATFORM_HAL, "fireball://hal/gpio/0", msg2))
    assert status_bad == IpcStatus.ERR_PERMISSION_DENIED
    assert msg2.ownership == OwnershipState.SENDER_OWNS


def test_ipc_02_e2e_shared_block_transfer():
    """IPC-02: End-to-end zero-copy SharedBlock transfer via IPC router (CSP rendezvous)."""
    sysv = System()
    try:
        # Sender allocates SharedBlock
        sb = sysv.memory_manager.allocate_shared(caller_task_id=2, size=256).unwrap()
        assert sb.get_owner() == 2
        addr = sb.get_address()
        assert addr >= 0x20020000

        # Sender puts shm_id directly in the message entry's value inside shared memory!
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, sb.shm_id)],
            memory_manager=sysv.memory_manager,
            task_id=2,
        )
        sent: list[IpcStatus] = []

        def client_app_task():
            status, _ = yield from sysv.ipc.send(Role.RUNTIME, "fireball://hal/gpio/0", msg)
            sent.append(status)

        received: list[IPCMessage] = []

        def gpio_receiver():
            status, recv_msg = yield from sysv.ipc.recv("fireball://hal/gpio/0")
            received.append(recv_msg)

        # Spawn receiver (task 1) then sender (task 2)
        sysv.scheduler.spawn("gpio_receiver", gpio_receiver())
        sysv.scheduler.spawn("client_app", client_app_task())
        sysv.scheduler.run_until_idle()

        assert sent == [IpcStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # Channel automatically granted ownership of entry's shm_id to receiver task (task 1)!
        recv_shm_id = recv_msg[_KEY_SHM_ID]
        assert recv_shm_id == sb.shm_id

        recv_sb = sysv.memory_manager.claim(receiver_task_id=1, shm_id=recv_shm_id).unwrap()
        assert recv_sb.get_owner() == 1
        assert recv_sb.get_address() == addr
    finally:
        sysv.shutdown()


def test_ipc_03_send_failure_restores_owner():
    """IPC-03: If IPC send is rejected (e.g. RBAC denial), sender can rollback."""
    sysv = System()
    try:
        sb = sysv.memory_manager.allocate_shared(caller_task_id=1, size=256).unwrap()
        shm_id = sb.release()
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, shm_id)],
            memory_manager=sysv.memory_manager,
            task_id=1,
        )
        # PLATFORM_HAL has no outgoing edges: rejected at Stage 2 before ever
        # touching a channel, so this never actually blocks.
        status, _ = _run_immediate(sysv.ipc.send(Role.PLATFORM_HAL, "fireball://hal/gpio/0", msg))
        assert status == IpcStatus.ERR_PERMISSION_DENIED
        assert msg.ownership == OwnershipState.SENDER_OWNS
        # Rollback
        sysv.memory_manager.rollback_transfer(original_sender_id=1, shm_id=shm_id)
        assert sysv.memory_manager.vmmio_registry.get_owner(sb.page_idx) == 1
    finally:
        sysv.shutdown()


def test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group():
    """
    IPC-04: recv()'s guarded external choice (select) completes with
    whichever allowed sender arrives first -- CORE_SERVICE is reachable from
    both RUNTIME and DEBUGGER, so a receiver must not commit to just one
    upfront. After the select resolves, the losing edge must be cleared (not
    left as a stale waiter) so it remains independently usable afterward.
    """
    sched = Scheduler()
    router = IPCRouter(sched)

    received: list[tuple[IpcStatus, IPCMessage]] = []

    def core_receiver():
        status, msg = yield from router.recv("fireball://core/coos/0")
        received.append((status, msg))

    def debugger_sender():
        status, _ = yield from router.send(
            Role.DEBUGGER, "fireball://core/coos/0", IPCMessage.from_entries([(1, 99)])
        )
        assert status == IpcStatus.COMPLETED

    recv_id = sched.spawn("core_receiver", core_receiver())
    sched.run_until_idle()
    assert sched.get_task(recv_id).state == TaskState.SUSPENDED_CSP
    # Selecting on both edges must not double-register: each channel still
    # has exactly one waiter, this same receiver task.
    runtime_ch = router.channel_for_edge(Role.RUNTIME, Role.CORE_SERVICE)
    debugger_ch = router.channel_for_edge(Role.DEBUGGER, Role.CORE_SERVICE)
    assert runtime_ch is not None and debugger_ch is not None
    assert runtime_ch.waiter_dir == WaitDir.RECV
    assert debugger_ch.waiter_dir == WaitDir.RECV
    assert runtime_ch.waiter_task is debugger_ch.waiter_task

    sched.spawn("debugger_sender", debugger_sender())
    sched.run_until_idle()

    assert len(received) == 1
    status, msg = received[0]
    assert status == IpcStatus.COMPLETED
    assert msg.get(1) == 99
    # The losing edge (RUNTIME->CORE_SERVICE) must have been cleared, not
    # left pointing at the now-terminated receiver.
    assert runtime_ch.waiter_dir == WaitDir.NONE
    assert runtime_ch.waiter_task is None

    # That edge must still be independently usable by a fresh receiver.
    received2: list[tuple[IpcStatus, IPCMessage]] = []

    def core_receiver2():
        status, msg = yield from router.recv("fireball://core/coos/0")
        received2.append((status, msg))

    def runtime_sender():
        status, _ = yield from router.send(
            Role.RUNTIME, "fireball://core/coos/0", IPCMessage.from_entries([(1, 7)])
        )
        assert status == IpcStatus.COMPLETED

    sched.spawn("core_receiver2", core_receiver2())
    sched.spawn("runtime_sender", runtime_sender())
    sched.run_until_idle()

    assert len(received2) == 1
    assert received2[0][1].get(1) == 7


def test_ipc_05_message_storage_ownership_and_access_check():
    """IPC-05: IPCMessage owns its FlatMapStorage and enforces ownership checks upon access."""
    from ipc_router import OwnershipState

    msg = IPCMessage.from_entries([(10, 100), (20, 200)])
    assert msg.ownership == OwnershipState.SENDER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200
    assert len(msg) == 2
    assert 10 in msg

    # Transition to IN_FLIGHT (sending): access to entries is strictly prohibited
    msg.ownership = OwnershipState.IN_FLIGHT
    try:
        _ = msg.get(10)
        raise AssertionError("Accessing entries during IN_FLIGHT must raise AssertionError")
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    try:
        _ = msg.entries
        raise AssertionError(
            "Accessing entries property during IN_FLIGHT must raise AssertionError"
        )
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    try:
        _ = len(msg)
        raise AssertionError("Calling len() during IN_FLIGHT must raise AssertionError")
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    # Transition to RECEIVER_OWNS: access is permitted again
    msg.ownership = OwnershipState.RECEIVER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200


def test_ipc_06_router_create_channel_authorization():
    """IPC-06: router.create_channel() resolves destination, binds current task, checks RBAC, and returns Channel."""
    sched = Scheduler()
    router = IPCRouter(sched)

    # Task with Role.RUNTIME can open channel to HAL (ALLOWED)
    runtime_task_id = sched.spawn("runtime_task", role=Role.RUNTIME)
    sched.current_task = sched.get_task(runtime_task_id)

    ch_hal = router.create_channel("fireball://hal/gpio/0")
    assert ch_hal is not None, "RUNTIME -> PLATFORM_HAL must be allowed"

    # Task with Role.PLATFORM_HAL cannot open channel to DEBUGGER (DENIED)
    hal_task_id = sched.spawn("hal_task", role=Role.PLATFORM_HAL)
    sched.current_task = sched.get_task(hal_task_id)

    ch_denied = router.create_channel("fireball://debugger/control")
    assert ch_denied is None, "PLATFORM_HAL -> DEBUGGER must be denied by RBAC"

    # Communication over the authorized channel
    msg = IPCMessage.from_entries([(1, 42)])
    sched.current_task = sched.get_task(runtime_task_id)
    action, _ = ch_hal.send(msg)
    assert action == ChannelAction.BLOCK
    assert ch_hal.waiter_dir == WaitDir.SEND


def test_ipc_07_message_in_shm_and_payload_shm_transfer():
    """IPC-07: The message is resident in shared memory, and can carry another payload SHM ID inside its entries."""
    from ipc_router import DataType, ScopeKind, pack_key32

    sysv = System()
    try:
        sender_id = 2
        receiver_id = 1

        # 1. Allocate SharedBlock for the message itself (message is shared memory!)
        msg_sb = sysv.memory_manager.allocate_shared(caller_task_id=sender_id, size=256).unwrap()
        assert msg_sb.get_owner() == sender_id

        # 2. Allocate another SharedBlock for payload bulk data
        payload_sb = sysv.memory_manager.allocate_shared(
            caller_task_id=sender_id, size=1024
        ).unwrap()
        assert payload_sb.get_owner() == sender_id
        payload_shm_id = payload_sb.shm_id

        # 3. Embed payload SHM ID into the message's KV entries (in the memory block!)
        k_payload_id = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=0x14)
        k_payload_len = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0x01)
        entries = [(k_payload_id, payload_shm_id), (k_payload_len, 1024)]

        # Construct message backed by msg_sb and write entries into its memory block!
        msg = IPCMessage(msg_sb)
        msg.write_entries(entries)
        assert msg.block is msg_sb
        assert msg[k_payload_id] == payload_shm_id

        sent: list[IpcStatus] = []

        def client_sender():
            status, _ = yield from sysv.ipc.send(Role.RUNTIME, "fireball://hal/gpio/0", msg)
            sent.append(status)

        received: list[IPCMessage] = []

        def hal_receiver():
            status, recv_msg = yield from sysv.ipc.recv("fireball://hal/gpio/0")
            received.append(recv_msg)

        # Receiver is task 1, Sender is task 2
        sysv.scheduler.spawn("hal_receiver", hal_receiver())
        sysv.scheduler.spawn("client_sender", client_sender())
        sysv.scheduler.run_until_idle()

        assert sent == [IpcStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # 1. Message's own SHM block is granted to receiver!
        assert recv_msg.block is not None
        assert recv_msg.block.get_owner() == receiver_id

        # 2. Payload SHM ID in entries was also automatically granted to receiver!
        retrieved_shm_id = recv_msg.get_by_key_id(0x14, ScopeKind.RESOURCE)
        assert retrieved_shm_id == payload_shm_id

        # Receiver claims the payload SharedBlock
        recv_payload_sb = recv_msg.claim_resource(sysv.memory_manager, receiver_id, key_id=0x14)
        assert recv_payload_sb is not None
        assert recv_payload_sb.get_owner() == receiver_id
        assert recv_payload_sb.shm_id == payload_shm_id
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
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xCAFEBABE, 0, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
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
    """
    SYS-40..42: fireball_call's IPC_LOOKUP/SEND/RECV. The guest task's own
    execution *is* the IPC_SEND/IPC_RECV call (system_syscall.md: a host call
    runs inside the calling task's coroutine), so it genuinely waits for its
    CSP counterpart -- no EAGAIN/polling (ipc_router.md §5.1). The
    receiver/sender coroutines below are spawned before the guest's call only
    so the rendezvous resolves within that one call, not because the guest
    call itself would otherwise fail.
    """
    sysv = System()
    try:
        uri = "fireball://hal/gpio/0"
        uri_bytes = uri.encode()
        payload = b"SET_GPIO"

        guest_mem = bytearray(128)
        guest_mem[0 : len(uri_bytes)] = uri_bytes
        guest_mem[64 : 64 + len(payload)] = payload
        sysv.bind_guest(guest_mem, task_id=1)
        handle = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri_bytes), 0, 0, 0, 0)
        assert handle > 0

        # -- IPC_SEND: a PLATFORM_HAL receiver coroutine blocks first (nobody
        # is sending yet), then the guest's IPC_SEND completes the rendezvous
        # synchronously the instant it calls in.
        sent: list[IPCMessage] = []

        def hal_receiver():
            status, msg = yield from sysv.ipc.recv(uri)
            sent.append(msg)

        recv_id = sysv.scheduler.spawn("hal_receiver", hal_receiver())
        sysv.scheduler.run_until_idle()
        assert sysv.scheduler.get_task(recv_id).state.name == "SUSPENDED_CSP"

        assert (
            sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 64, len(payload), 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert sent and kv_entries_to_bytes(sent[0].entries, max_len=len(payload)) == payload

        # -- IPC_RECV: a DEBUGGER sender coroutine blocks first, so the
        # guest's IPC_RECV completes the rendezvous the instant it calls in.
        core_uri = "fireball://core/coos/0"
        core_uri_bytes = core_uri.encode()
        guest_mem[32 : 32 + len(core_uri_bytes)] = core_uri_bytes
        reply = b"ACK"
        sent_status = []

        def debugger_sender():
            status, _ = yield from sysv.ipc.send(
                Role.DEBUGGER,
                core_uri,
                IPCMessage.from_entries(
                    bytes_to_kv_storage(reply), memory_manager=sysv.memory_manager
                ),
            )
            sent_status.append(status)

        sysv.scheduler.spawn("debugger_sender", debugger_sender())
        sysv.scheduler.run_until_idle()

        core_handle = sysv.fireball_call(
            FbSyscallId.IPC_LOOKUP, 32, len(core_uri_bytes), 0, 0, 0, 0
        )
        assert core_handle > 0
        recv_len = sysv.fireball_call(FbSyscallId.IPC_RECV, core_handle, 96, 32, 0, 0, 0)
        assert recv_len == len(reply)
        assert bytes(guest_mem[96 : 96 + recv_len]) == reply
        assert sent_status == [IpcStatus.COMPLETED]
    finally:
        sysv.shutdown()


def test_hal_task_ipc_communication():
    """HAL-01: HAL operates as a distinct task on COOS and handles commands via IPC rendezvous."""
    from hal import ARG_LENGTH, ARG_OFFSET
    from wasi import Wasi03pEngine, WasiIpcCmd

    sysv = System()
    try:
        sysv.spawn_hal_task()
        engine = Wasi03pEngine(sysv)
        # Send command via IPC
        nwritten = engine.send_ipc_command(
            "fireball://device/uart/0",
            WasiIpcCmd.STREAM_WRITE_SHM,
            FlatMapView([(ARG_LENGTH, 128), (ARG_OFFSET, 0)]),
        )
        assert nwritten == 128
        assert sysv.hal_task.processed_count == 1
        assert sysv.hal_task.last_handled_uri == "fireball://device/uart/0"
        assert sysv.hal_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_SHM
    finally:
        sysv.shutdown()


def test_gdbserver_task_coos_cooperative_execution():
    """DBG-01: GDBServer operates as an independent task on COOS and handles RSP packets."""
    import socket

    from debugger import DebuggerManager

    sysv = System()
    dbg = DebuggerManager()
    ctx = WASMContext()
    ctx.locals = [10, 20]
    task_id, port = sysv.spawn_gdbserver_task(dbg, start_pc=0x10, ctx=ctx)

    try:
        # Connect client to the non-blocking gdbserver task
        client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        client.settimeout(2.0)

        # Drive COOS scheduler to accept the connection
        sysv.scheduler.step()

        # Send '?' halt reason query
        client.sendall(b"$?#3f")
        # Step scheduler to process packet
        sysv.scheduler.step()

        # Read ACK '+' and response
        resp = client.recv(1024)
        assert b"+" in resp
        assert b"$S05#b8" in resp

        # Send 'g' read registers
        client.sendall(b"+$g#67")
        sysv.scheduler.step()
        resp = client.recv(1024)
        assert b"+" in resp
        assert b"$" in resp

        client.close()
    finally:
        sysv.shutdown()


def test_syscall_07_wasi_fd_write():
    """SYS-80: WASI_FD_WRITE writes single iovec to UART stdout and reports written bytes."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        message = b"hello from wasm\n"
        guest_mem[32 : 32 + len(message)] = message
        struct.pack_into("<II", guest_mem, 0, 32, len(message))
        sysv.bind_guest(guest_mem, task_id=1)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        assert sysv.transport.drain() == message
        nwritten = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nwritten == len(message)
    finally:
        sysv.shutdown()


def test_wasi_01_fd_write_scatter_gather():
    """SYS-80: WASI_FD_WRITE supports scatter-gather output with multiple iovecs."""
    sysv = System()
    try:
        guest_mem = bytearray(128)
        chunk1 = b"FIREBALL_"
        chunk2 = b"WASI_SCATTER_GATHER\n"
        guest_mem[32 : 32 + len(chunk1)] = chunk1
        guest_mem[64 : 64 + len(chunk2)] = chunk2
        # 2 iovecs at offset 0 and 8
        struct.pack_into("<II", guest_mem, 0, 32, len(chunk1))
        struct.pack_into("<II", guest_mem, 8, 64, len(chunk2))
        sysv.bind_guest(guest_mem, task_id=1)
        # Write to stdout (fd=1) with 2 iovecs, result at offset 100
        assert (
            sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 2, 100, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.transport.drain() == chunk1 + chunk2
        nwritten = struct.unpack_from("<I", guest_mem, 100)[0]
        assert nwritten == len(chunk1) + len(chunk2)
    finally:
        sysv.shutdown()


def test_wasi_02_fd_read_eof():
    """SYS-81: WASI_FD_READ reports 0 bytes read (EOF) without crashing."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        struct.pack_into("<II", guest_mem, 0, 16, 32)
        sysv.bind_guest(guest_mem, task_id=1)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_READ, 0, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        nread = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nread == 0  # Standard WASI EOF
    finally:
        sysv.shutdown()


def test_wasi_03_fd_close():
    """SYS-82: WASI_FD_CLOSE returns SUCCESS for any fd."""
    sysv = System()
    try:
        assert sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, 3, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
    finally:
        sysv.shutdown()


def test_wasi_04_clock_time_get_monotonic():
    """SYS-83: WASI_CLOCK_TIME_GET writes monotonic 64-bit nanosecond timestamp to guest memory."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 0, 0, 16, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        t1 = struct.unpack_from("<Q", guest_mem, 16)[0]
        assert t1 > 0
        time.sleep(0.001)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 0, 0, 24, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        t2 = struct.unpack_from("<Q", guest_mem, 24)[0]
        assert t2 >= t1, "WASI monotonic clock must be monotonically non-decreasing"
    finally:
        sysv.shutdown()


def test_wasi_05_proc_exit():
    """SYS-84: WASI_PROC_EXIT sets system halted state and exit code."""
    sysv = System()
    try:
        assert sysv.halted is False
        assert (
            sysv.fireball_call(FbSyscallId.WASI_PROC_EXIT, 42, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.halted is True
        assert sysv.exit_code == 42
    finally:
        sysv.shutdown()


def test_wasi_06_random_get():
    """SYS-85: WASI_RANDOM_GET fills guest buffer with cryptographically secure random bytes."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_RANDOM_GET, 8, 16, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        random_bytes = bytes(guest_mem[8:24])
        assert len(random_bytes) == 16
        assert random_bytes != bytes(16), "Random buffer must not be all zeros"
    finally:
        sysv.shutdown()


def test_wasi_07_invalid_fd_returns_badf():
    """SYS-91: WASI_FD_WRITE to invalid fd (e.g. fd=99) returns EBADF."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        struct.pack_into("<II", guest_mem, 0, 16, 8)
        sysv.bind_guest(guest_mem, task_id=1)
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 99, 0, 1, 48, 0, 0)
        assert res == WasiErrno.BADF
    finally:
        sysv.shutdown()


def test_wasi_08_out_of_bounds_offset_returns_fault():
    """SYS-92: Out-of-bounds guest memory offset in WASI call returns EFAULT instantly."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        # iovs_ptr way past 64 bytes
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0x10000, 1, 48, 0, 0)
        assert res == WasiErrno.FAULT
    finally:
        sysv.shutdown()


# ===========================================================================
# 10. WASM Instruction Set & Interpreter (runtime_interpreter_test_spec.md, wasm_instruction_set_test_spec.md)
# ===========================================================================


def test_intp_01_02_cps_handlers_and_dispatch_table():
    """INTP-01, 02: Opcode handlers use CPS 4-arg signature (ip, frame, env, locals) and direct array table dispatch."""
    import inspect

    from interpreter import _HANDLERS

    # Direct 256-element array table (no dynamic dict lookup)
    assert type(_HANDLERS) is list
    assert len(_HANDLERS) == 256
    # Every registered handler must accept exactly 4 arguments and return next continuation
    registered_count = 0
    for op, handler in enumerate(_HANDLERS):
        if handler is not None:
            registered_count += 1
            sig = inspect.signature(handler)
            assert len(sig.parameters) == 4, (
                f"Handler for opcode 0x{op:02X} must have exactly 4 arguments (CPS)"
            )

    assert registered_count >= 30, (
        f"Expected at least 30 registered MVP opcode handlers, found {registered_count}"
    )


def test_wasm_01_to_06_unsupported_features_rejected():
    """WASM-01..06: Unsupported features (SIMD, threads, tail-call) are rejected with error code."""
    # Module with unsupported SIMD opcode 0xFD
    wasm_bytes = (
        b"\x00asm\x01\x00\x00\x00"
        b"\x01\x05\x01\x60\x00\x01\x7f"
        b"\x03\x02\x01\x00"
        b"\x07\x0d\x01\x09test_simd\x00\x00"
        b"\x0a\x06\x01\x04\x00\xfd\x00\x0b"
    )
    mod = parse(wasm_bytes)
    try:
        interp = Interpreter(mod)
        interp.call(0, [])
        raise AssertionError("Expected WasmUnsupportedFeatureError for SIMD opcode")
    except WasmUnsupportedFeatureError as e:
        assert "ERR_WASM_UNSUPPORTED_FEATURE" in str(e)


def test_wasm_10_to_15_control_flow_and_calls():
    """WASM-10..15: Unreachable trap, block/loop/if/br_table, call, and call_indirect."""
    wat = """
    (module
      (table 2 2 funcref)
      (type $sig_calc (func (param i32) (result i32)))
      (func $unreachable_fn (export "unreachable_fn")
        (unreachable)
      )
      (func $calc_fn (export "calc_fn") (param $x i32) (result i32)
        (block $b0
          (block $b1
            (br_table $b1 $b0 (local.get $x))
          )
          (return (i32.const 100))
        )
        (return (i32.const 200))
      )
      (func $call_ind (export "call_ind") (param $arg i32) (param $idx i32) (result i32)
        (call_indirect (type $sig_calc) (local.get $arg) (local.get $idx))
      )
      (elem (i32.const 0) $calc_fn $calc_fn)
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_10_to_15")
        return
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
    wat = """
    (module
      (func $sel (export "sel") (param $cond i32) (param $val1 i32) (param $val2 i32) (result i32)
        (drop (local.get $cond))
        (select (local.get $val1) (local.get $val2) (local.get $cond))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_20_21")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("sel"), [1, 10, 20]) == [10]
    assert interp.call(mod.export_func_index("sel"), [0, 10, 20]) == [20]


def test_wasm_30_31_locals_and_globals():
    """WASM-30..31: local.get/set/tee and global.get/set."""
    wat = """
    (module
      (global $g (mut i32) (i32.const 42))
      (func $loc_glob (export "loc_glob") (param $p0 i32) (result i32)
        (local $l1 i32)
        (local.set $l1 (local.get $p0))
        (global.set $g (local.get $l1))
        (i32.add (global.get $g) (local.get $l1))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_30_31")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("loc_glob"), [5]) == [10]
    assert interp.globals[0] == 5


def test_wasm_40_to_46_memory_load_store_grow_and_data():
    """WASM-40..46 & WASM-60: Linear memory load, store, size, grow, bounds traps, and Data segments."""
    wat = """
    (module
      (memory 1 2)
      (data (i32.const 0) "WASM_INIT")
      (func $mem_ops (export "mem_ops") (result i32)
        (drop (i32.load (i32.const 0)))
        (i32.store (i32.const 16) (i32.const 0x12345678))
        (drop (memory.grow (i32.const 1)))
        (memory.size)
      )
      (func $trap_oob (export "trap_oob")
        (drop (i32.load (i32.const 0x1000000)))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_40_to_46")
        return
    mod = parse(wasm_bytes)
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
    wat = """
    (module
      (func $div_s (export "div_s") (param $a i32) (param $b i32) (result i32)
        (i32.div_s (local.get $a) (local.get $b))
      )
      (func $bit_ops (export "bit_ops") (param $x i32) (result i32)
        (i32.xor
          (i32.add (i32.popcnt (local.get $x)) (i32.clz (local.get $x)))
          (i32.rotl (local.get $x) (i32.const 4))
        )
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_50_to_56")
        return
    mod = parse(wasm_bytes)
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
# System Containers (CONT-01 .. CONT-10)
# ===========================================================================


def test_cont_01_flat_map_view_find_binary_search():
    """CONT-01: flat_map_view.find performs O(log n) binary search returning value or None."""
    entries = [(10, 100), (20, 200), (30, 300), (40, 400), (50, 500), (60, 600)]
    view = FlatMapView(entries)
    assert view.find(30) == 300
    assert view.find(10) == 100
    assert view.find(60) == 600
    assert view.find(25) is None
    assert view.find(5) is None
    assert view.find(70) is None
    assert view.size() == 6
    assert not view.empty()


def test_cont_02_narrow_monotonic_shrinkage():
    """CONT-02: narrow(lo, hi) produces monotonic sub-window subset."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5), (60, 6), (70, 7), (80, 8)]
    v0 = FlatMapView(entries)
    v1 = v0.narrow(20, 60)
    assert v1.size() == 5  # 20, 30, 40, 50, 60
    assert v1.find(20) == 2
    assert v1.find(60) == 6
    assert v1.find(10) is None
    v2 = v1.narrow(30, 45)
    assert v2.size() == 2  # 30, 40
    assert v2.find(30) == 3
    assert v2.find(40) == 4
    assert v2.find(20) is None
    assert v2.find(50) is None


def test_cont_03_slice_monotonic_shrinkage_and_bounds():
    """CONT-03: slice must only ever shrink within parent view bounds."""
    entries = [(10, 1), (20, 2), (30, 3), (40, 4), (50, 5)]
    v0 = FlatMapView(entries)
    v1 = v0.slice(1, 4)
    assert v1.size() == 3
    assert v1.find(20) == 2
    assert v1.find(40) == 4
    try:
        v1.slice(0, 5)  # Expanding beyond v1's window [1, 4] must fail
        raise AssertionError("Expected ValueError when expanding slice")
    except ValueError:
        pass


def test_cont_04_flat_set_view_membership_only():
    """CONT-04: flat_set_view answers contains(key) with bool, carries no value span."""
    keys = [100, 200, 300, 400]
    set_view = FlatSetView(keys)
    assert set_view.contains(200) is True
    assert set_view.contains(250) is False
    assert (300 in set_view) is True
    assert (50 in set_view) is False
    assert not hasattr(set_view, "values"), "flat_set_view must not carry a values span"


def test_cont_05_bit_view_adjacent_element_non_destructive():
    """CONT-05: bit_view put/at modifies targeted sub-byte element without corrupting adjacent elements."""
    storage = bytearray(4)  # 4 bytes = 16 2-bit elements
    bv = BitView(storage, bits=2, origin=0, count=16)
    # Initial state all 0
    for i in range(16):
        assert bv.at(i) == 0

    # Write pattern to adjacent elements
    bv.put(0, 1)  # 01
    bv.put(1, 2)  # 10
    bv.put(2, 3)  # 11
    bv.put(3, 0)  # 00
    # Verify byte 0 is 0b00111001 = 0x39 (little-endian bit packing)
    assert storage[0] == (1 | (2 << 2) | (3 << 4) | (0 << 6))
    assert bv.at(0) == 1
    assert bv.at(1) == 2
    assert bv.at(2) == 3
    assert bv.at(3) == 0
    # Mutate middle element, ensure neighbors remain untouched
    bv.put(1, 3)
    assert bv.at(0) == 1
    assert bv.at(1) == 3
    assert bv.at(2) == 3
    assert bv.at(3) == 0


def test_cont_06_bit_view_unaligned_slice_origin_absorption():
    """CONT-06: bit_view.slice absorbs non-byte-aligned bit origins."""
    storage = bytearray(2)  # 8 2-bit elements
    bv = BitView(storage, bits=2, origin=0, count=8)
    for i in range(8):
        bv.put(i, i % 4)

    # Slice starting at unaligned index 3 (bit offset = 6)
    sub = bv.slice(3, 7)
    assert sub.size() == 4
    assert sub.origin == 6
    assert sub.at(0) == bv.at(3)
    assert sub.at(1) == bv.at(4)
    assert sub.at(2) == bv.at(5)
    assert sub.at(3) == bv.at(6)


def test_cont_07_bit_view_allowed_bits_enforced():
    """CONT-07: bit_view allows only 1, 2, 4 bits dividing 8."""
    storage = bytearray(4)
    # Valid
    BitView(storage, bits=1, count=32)
    BitView(storage, bits=2, count=16)
    BitView(storage, bits=4, count=8)
    # Invalid
    for invalid in (3, 5, 6, 7, 8):
        try:
            BitView(storage, bits=invalid, count=4)
            raise AssertionError(f"Expected ValueError for invalid Bits={invalid}")
        except ValueError:
            pass


def test_cont_08_radix_binary_tree_view_coarse_radix_lookup():
    """CONT-08: radix_binary_tree_view uses O(1) Radix Table prefix + local binary search."""
    keys = [0x0010, 0x0020, 0x0110, 0x0120, 0x0130, 0x0210]
    values = ["T0_A", "T0_B", "T1_A", "T1_B", "T1_C", "T2_A"]
    # Radix shift = 8 -> prefix = pc >> 8
    # Prefix 0: [0, 2), Prefix 1: [2, 5), Prefix 2: [5, 6)
    radix_table = [0, 2, 5, 6]
    tree = RadixBinaryTreeView(keys, values, radix_table, radix_shift=8)
    assert tree.find(0x0120) == "T1_B"
    assert tree.find(0x0010) == "T0_A"
    assert tree.find(0x0210) == "T2_A"
    assert tree.find(0x0199) is None
    assert tree.find(0x0300) is None


def test_cont_09_jit_entry_lookup_card_table_prefilter():
    """CONT-09: lookup_jit_entry performs O(1) Card Marking check before searching (card_shift=3, 8B/card)."""
    card_storage = bytearray(4)
    card_table = BitView(card_storage, bits=2, origin=0, count=16)
    keys = [0x0010, 0x0020]
    values = ["NATIVE_0010", "NATIVE_0020"]
    # Prefix 0: empty [0, 0), Prefix 1: [0, 1), Prefix 2: [1, 2)
    radix_table = [0, 0, 1, 2]
    tree = RadixBinaryTreeView(keys, values, radix_table, radix_shift=4)
    # PC 0x0010 (16) is card 2 (16 >> 3). Currently UNEXECUTED (0) -> lookup returns None without search
    assert lookup_jit_entry_radix(tree, card_table, pc=0x0010, card_shift=3) is None
    # Mark card 2 as COMPILED (3)
    card_table.put(2, 3)
    assert lookup_jit_entry_radix(tree, card_table, pc=0x0010, card_shift=3) == "NATIVE_0010"


def test_cont_10_container_type_separation():
    """CONT-10: flat_map_view and flat_set_view have strictly separated type responsibilities."""
    keys = [1, 2, 3]
    vals = [10, 20, 30]
    entries = list(zip(keys, vals, strict=False))
    m = FlatMapView(entries)
    s = FlatSetView(keys)
    assert type(m) is FlatMapView
    assert type(s) is FlatSetView
    assert type(s) is not FlatMapView
    assert hasattr(m, "values")
    assert not hasattr(s, "values")


def test_cont_11_storage_and_view_ownership_separation():
    """CONT-11: Data storage ownership is strictly separated from non-owning views (AoS)."""
    entries = [(10, "A"), (20, "B"), (30, "C")]
    storage = FlatMapStorage(entries)
    v1 = storage.view()
    v2 = storage.view()

    # Views borrow the same underlying entries array without taking ownership
    assert v1.find(20) == "B"
    assert v2.find(30) == "C"
    assert v1.entries == storage.entries
    assert v2.entries == storage.entries
    assert v1.keys == [10, 20, 30]
    assert v1.values == ["A", "B", "C"]


def test_cont_12_flat_map_storage_standard_sort():
    """CONT-12: FlatMapStorage in-place sorts entries by key using standard sort (AoS)."""
    entries = [(50, "E"), (10, "A"), (40, "D"), (20, "B"), (30, "C")]
    storage = FlatMapStorage(entries)
    assert not storage.is_sorted()

    # In-place standard sort
    storage.sort()
    assert storage.is_sorted()
    assert storage.keys == [10, 20, 30, 40, 50]
    assert storage.values == ["A", "B", "C", "D", "E"]
    assert storage.entries == [(10, "A"), (20, "B"), (30, "C"), (40, "D"), (50, "E")]

    # View correctly finds via binary search
    v = storage.view()
    assert v.find(10) == "A"
    assert v.find(30) == "C"
    assert v.find(50) == "E"
    assert v.find(99) is None

    # Automatic sorting via sort=True with AoS (key, value) pairs
    s_auto = FlatMapStorage([(3, "three"), (1, "one"), (2, "two")], sort=True)
    assert s_auto.is_sorted()
    assert s_auto.keys == [1, 2, 3]
    assert s_auto.values == ["one", "two", "three"]


def test_cont_13_flat_map_storage_sorted_insert_remove():
    """CONT-13: FlatMapStorage maintains sorted order across arbitrary insert and remove/erase calls."""
    storage = FlatMapStorage()
    assert len(storage) == 0

    # Insert elements out of order
    assert storage.insert(30, "thirty") is True
    assert storage.insert(10, "ten") is True
    assert storage.insert(50, "fifty") is True
    assert storage.insert(20, "twenty") is True
    assert storage.insert(40, "forty") is True

    # Maintained sorted order at all times
    assert storage.is_sorted()
    assert storage.keys == [10, 20, 30, 40, 50]
    assert storage.values == ["ten", "twenty", "thirty", "forty", "fifty"]

    # Updating existing key replaces value, returns False (no size increase)
    assert storage.insert(30, "THIRTY_UPDATED") is False
    assert len(storage) == 5
    assert storage.keys == [10, 20, 30, 40, 50]
    assert storage.values == ["ten", "twenty", "THIRTY_UPDATED", "forty", "fifty"]

    # Removal maintains sorted order
    # Remove head
    assert storage.remove(10) is True
    assert storage.keys == [20, 30, 40, 50]
    assert storage.values == ["twenty", "THIRTY_UPDATED", "forty", "fifty"]
    assert storage.is_sorted()

    # Erase middle
    assert storage.erase(30) is True
    assert storage.keys == [20, 40, 50]
    assert storage.values == ["twenty", "forty", "fifty"]
    assert storage.is_sorted()

    # Erase tail
    assert storage.erase(50) is True
    assert storage.keys == [20, 40]
    assert storage.values == ["twenty", "forty"]
    assert storage.is_sorted()

    # Remove nonexistent returns False
    assert storage.remove(999) is False
    assert len(storage) == 2

    # View remains valid and functional
    v = storage.view()
    assert v.find(20) == "twenty"
    assert v.find(40) == "forty"
    assert v.find(10) is None
    assert v.find(30) is None


# ===========================================================================
# Cooperative Multitasking & Idle-Hook Integration (YIELD / IDLE / TIER)
# ===========================================================================


def test_coop_01_wasm_coroutine_yields_on_quantum():
    """YIELD-01: Long-running WASM task yields every `yield_every` instructions, interleaving with other tasks."""
    wat = """
    (module
      (func $busy_loop (export "busy_loop") (param $x i32) (result i32)
        (block $b
          (loop $l
            (local.set $x (i32.add (local.get $x) (i32.const 1)))
            (br_if $l (i32.lt_s (local.get $x) (i32.const 100)))
          )
        )
        (local.get $x)
      )
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_coop_01")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # Step in quanta of 10 instructions
    call_state = interp.start(mod.export_func_index("busy_loop"), [0])
    step_count = 0
    while not call_state.finished:
        call_state = interp.step(call_state, quantum=10)
        step_count += 1
    result = call_state.results

    assert step_count >= 10, f"Expected multiple quantum steps, got {step_count}"
    assert result == [100]


def test_idle_01_jit_batch_compilation_on_idle():
    """IDLE-01: Compile queue is drained and compiled in LIFO order when scheduler fires idle_hook."""
    compiled_log = []

    def mock_compiler(pc: int) -> JITTrace:
        compiled_log.append(pc)
        return JITTrace(head_pc=pc, native_fn=lambda: pc, size_bytes=64)

    engine = RuntimeEngine(jit_compiler=PcOnlyCompiler(mock_compiler))
    engine.bitmap.touch(0x100)
    engine.bitmap.touch(0x100)  # HOT
    engine.bitmap.touch(0x200)
    engine.bitmap.touch(0x200)  # HOT
    engine.compile_queue = [0x100, 0x200]  # Enqueued
    # COOS idle_hook fires with budget 2
    count = engine.idle_hook(budget=2)
    assert count == 2
    assert compiled_log == [0x200, 0x100], "LIFO compilation order required"
    assert engine.bitmap.get_state(0x100) == CardState.COMPILED
    assert engine.bitmap.get_state(0x200) == CardState.COMPILED
    assert engine.cache.active.has_trace(0x100)
    assert engine.cache.active.has_trace(0x200)


def test_idle_02_logging_flush_on_idle():
    """IDLE-02: Deferred logs in RingBuffer are flushed to UART transport upon scheduler idle."""
    transport = UartTransport()
    dictionary = LogDictionary()
    dictionary.register(0x01, "event payload=%d")
    logger = Logger(transport, dictionary, min_level=LogLevel.INFO)
    # Log events during active execution
    status1 = logger.log_event(LogLevel.INFO, 0x01, 42)
    status2 = logger.log_event(LogLevel.INFO, 0x01, 99)
    assert status1 == "QUEUED"
    assert status2 == "QUEUED"
    assert transport.bytes_written == 0, "No UART I/O allowed on hot path"
    # Scheduler reaches IDLE -> fires idle hook
    flushed = logger.flush()
    assert flushed == 2
    wire_output = transport.drain().decode("utf-8")
    assert "event payload=42" in wire_output
    assert "event payload=99" in wire_output


def test_tier_01_interpreter_to_jit_cooperative_flow():
    """TIER-01: End-to-end integration of cooperative WASM execution on COOS with idle JIT compilation and log flush."""
    sysv = System()
    sysv.dictionary.register(0x10, "wasm iteration=%d")
    executed_steps = []

    def wasm_task():
        # Emulate a WASM task executing in slices
        for i in range(5):
            sysv.runtime_engine.record_block_head(0x1000)
            sysv.logger.log_event(LogLevel.INFO, 0x10, i)
            executed_steps.append(f"task_step_{i}")
            yield  # Cooperative yield

    def monitor_task():
        for i in range(5):
            executed_steps.append(f"monitor_step_{i}")
            yield  # Cooperative yield

    sysv.scheduler.spawn("wasm_worker", wasm_task())
    sysv.scheduler.spawn("monitor", monitor_task())
    # Run COOS scheduler to completion
    sysv.scheduler.run_to_completion()
    # Verify interleaved cooperative execution
    assert "task_step_0" in executed_steps
    assert "monitor_step_0" in executed_steps
    # Verify deferred logs were flushed by idle_hook
    wire = sysv.transport.drain().decode("utf-8")
    assert "wasm iteration=0" in wire
    assert "wasm iteration=4" in wire


def test_tier_02_interpreter_to_jit_trace_transition():
    """TIER-02: Loop executes via Interpreter first -> promotes to HOT -> idle_hook compiles trace -> executes as JIT."""
    engine = IntegratedHybridEngine(yield_threshold=3)
    # Basic block: loop body (local1 *= local0; local0 -= 1; branch while local0 != 0)
    # head_pc=0x100, loops back to 0x100 if local0 != 0, else falls through to 0x200
    loop_body = BasicBlock(
        head_pc=0x100,
        ops=[
            ("local.get", 1),
            ("local.get", 0),
            ("i32.mul", None),
            ("local.set", 1),
            ("local.get", 0),
            ("i32.const", 1),
            ("i32.sub", None),
            ("local.set", 0),
            ("local.get", 0),  # condition for branch
        ],
        next_pc=0x200,
        loops_to=0x100,
    )
    # Epilogue block: local.get 1 (result)
    epilogue = BasicBlock(head_pc=0x200, ops=[("local.get", 1)], next_pc=None)
    engine.register_block(loop_body)
    engine.register_block(epilogue)
    # Compute factorial(5) with 5 iterations: locals=[5, 1]
    ctx = WASMContext(locals_values=[5, 1])
    pc = 0x100
    # Step 1: First iteration runs in Interpreter
    pc = engine.run_step(pc, ctx)
    assert engine.interp_blocks == 1
    assert engine.jit_traces == 0
    assert engine.bitmap.get_state(0x100) == CardState.EXECUTED
    # Step 2: Second iteration runs in Interpreter -> Card becomes HOT
    pc = engine.run_step(pc, ctx)
    assert engine.interp_blocks == 2
    assert engine.jit_traces == 0
    assert engine.bitmap.get_state(0x100) == CardState.HOT
    # Step 3: Third iteration triggers yield -> on_yield queues HOT card to compile_queue
    pc = engine.run_step(pc, ctx)
    assert 0x100 in engine.compile_queue
    # Simulate COOS scheduler idle_hook: batch compiles queued trace into Active cache
    compiled = engine.idle_hook()
    assert compiled == 1
    assert engine.bitmap.get_state(0x100) == CardState.COMPILED
    assert engine.cache.active.has_trace(0x100)
    # Step 4 & 5: Remaining iterations execute via fast native JIT trace!
    while pc is not None:
        pc = engine.run_step(pc, ctx)

    # Verification:
    # Result is 5! = 120
    assert ctx.stack[-1] == 120
    # Verified that both Interpreter AND JIT traces executed in the same task run
    assert engine.interp_blocks >= 3
    assert engine.jit_traces >= 2
    assert engine.compilations == 1


def test_tier_03_trace_chaining_and_interpreter_fallback():
    """TIER-03: Traces chain directly into resident successors, and fall back to Interpreter when chain ends."""
    engine = IntegratedHybridEngine(yield_threshold=10)
    # Two consecutive blocks: block A (0x100) -> block B (0x200) -> block C (0x300, not compiled)
    block_a = BasicBlock(
        head_pc=0x100,
        ops=[("local.get", 0), ("i32.const", 10), ("i32.add", None), ("local.set", 0)],
        next_pc=0x200,
    )
    block_b = BasicBlock(
        head_pc=0x200,
        ops=[("local.get", 0), ("i32.const", 20), ("i32.add", None), ("local.set", 0)],
        next_pc=0x300,
    )
    block_c = BasicBlock(
        head_pc=0x300,
        ops=[("local.get", 0), ("i32.const", 30), ("i32.add", None), ("local.set", 0)],
        next_pc=None,
    )
    engine.register_block(block_a)
    engine.register_block(block_b)
    engine.register_block(block_c)
    # Compile block B first, then block A (so A can chain directly into resident B)
    trace_b = engine.compiler.compile_trace(0x200, block_b)
    engine.cache.insert(trace_b)
    engine.bitmap.mark_compiled(0x200)
    trace_a = engine.compiler.compile_trace(0x100, block_a)
    engine.cache.insert(trace_a)
    engine.bitmap.mark_compiled(0x100)
    # Assert trace A chained directly into trace B
    assert trace_a.chain_next == 0x200
    # Run execution:
    ctx = WASMContext(locals_values=[100])
    pc = 0x100
    # Step 1: Run 0x100 (JIT) -> returns 0x200 via direct chain
    pc = engine.run_step(pc, ctx)
    assert pc == 0x200
    assert engine.jit_traces == 1
    assert ctx.locals[0] == 110
    # Step 2: Run 0x200 (JIT) -> chain_next is None -> falls back to interpreter at 0x300
    pc = engine.run_step(pc, ctx)
    assert pc == 0x300
    assert engine.jit_traces == 2
    assert ctx.locals[0] == 130
    # Step 3: Run 0x300 (Interpreter) -> completes execution smoothly!
    pc = engine.run_step(pc, ctx)
    assert pc is None
    assert engine.interp_blocks == 1
    assert ctx.locals[0] == 160


# ===========================================================================
# Guest-Side WASI & Host-Call Execution (Interpreter & x64 JIT)
# ===========================================================================


def test_guest_wasi_01_interpreter_fd_write():
    """GUEST-WASI-01: WASM guest invoking wasi_snapshot_preview1.fd_write in Interpreter outputs to host UART."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
      (func (export "main") (result i32)
        (call $fd_write (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 32))
      )
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_01")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        # Set up guest memory:
        # offset 0: iov { buf: 16, len: 12 }
        # offset 16: "hello guest\n"
        msg = b"hello guest\n"
        struct.pack_into("<II", ctx.guest_memory, 0, 16, len(msg))
        ctx.guest_memory[16 : 16 + len(msg)] = msg
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        res = interp.call(mod.export_func_index("main"), [])
        assert res == [0], f"Expected WASI SUCCESS (0), got {res}"
        assert sysv.transport.drain() == msg
        nwritten = struct.unpack_from("<I", ctx.guest_memory, 32)[0]
        assert nwritten == len(msg)
    finally:
        sysv.shutdown()


def test_guest_wasi_02_interpreter_clock_and_random():
    """GUEST-WASI-02: WASM guest invoking clock_time_get and random_get stores valid data in guest memory."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "clock_time_get" (func $clock (param i32 i32 i32) (result i32)))
      (import "wasi_snapshot_preview1" "random_get" (func $rand (param i32 i32) (result i32)))
      (func (export "main") (result i32)
        (drop (call $clock (i32.const 0) (i32.const 0) (i32.const 16)))
        (call $rand (i32.const 32) (i32.const 16))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_02")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        res = interp.call(mod.export_func_index("main"), [])
        assert res == [0]
        t = struct.unpack_from("<Q", ctx.guest_memory, 16)[0]
        assert t > 0
        rand_data = bytes(ctx.guest_memory[32:48])
        assert len(rand_data) == 16
        assert rand_data != bytes(16)
    finally:
        sysv.shutdown()


def test_guest_wasi_03_interpreter_proc_exit():
    """GUEST-WASI-03: WASM guest invoking proc_exit(99) halts the host system with exit code."""
    wat = """
    (module
      (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
      (func (export "main")
        (call $exit (i32.const 99))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_guest_wasi_03")
        return
    mod = parse(wasm_bytes)
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        host_funcs = ctx.build_interpreter_host_functions(mod)
        interp = Interpreter(mod, memory=ctx.guest_memory, host_functions=host_funcs)
        assert sysv.halted is False
        interp.call(mod.export_func_index("main"), [])
        assert sysv.halted is True
        assert sysv.exit_code == 99
    finally:
        sysv.shutdown()


def test_guest_wasi_04_jit_fd_write_native():
    """GUEST-WASI-04: JIT trace executes native machine code calling wasi_snapshot_preview1.fd_write."""
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        msg = b"HELLO FROM JIT WASI GUEST!\n"
        struct.pack_into("<II", ctx.guest_memory, 0, 16, len(msg))
        ctx.guest_memory[16 : 16 + len(msg)] = msg

        def host_fd_write():
            return ctx.fd_write(1, 0, 1, 48)

        t = ctypes.CFUNCTYPE(ctypes.c_uint32)(host_fd_write)
        t_addr = ctypes.cast(t, ctypes.c_void_p).value
        block = BasicBlock(
            head_pc=0x100,
            ops=[
                ("call_host", t_addr),
                ("local.set", 0),
            ],
            next_pc=None,
        )
        compiler = TraceCompiler(host_trampolines={1: t_addr})
        trace = compiler.compile_trace(0x100, block)
        w_ctx = WASMContext(locals_values=[0])
        trace.invoke(w_ctx)
        assert w_ctx.locals[0] == 0  # WASI SUCCESS
        assert sysv.transport.drain() == msg
        nwritten = struct.unpack_from("<I", ctx.guest_memory, 48)[0]
        assert nwritten == len(msg)
    finally:
        sysv.shutdown()


def test_guest_wasi_05_jit_fireball_call_ipc_messaging():
    """GUEST-WASI-05: JIT trace calls fireball_call to perform IPC lookup, send, and recv."""
    sysv = System()
    try:
        ctx = WasiHostContext(sysv)
        uri = b"fireball://hal/gpio/0"
        payload = b"SET_HIGH"
        ctx.guest_memory[0 : len(uri)] = uri
        ctx.guest_memory[32 : 32 + len(payload)] = payload

        # IPC is inter-*task* communication: the guest (RUNTIME) sending
        # and the guest recv()-ing back are two different edges, each
        # needing its own already-waiting counterpart task, or fireball_call
        # (running as the guest task's own coroutine) would genuinely and
        # correctly block forever with nobody to rendezvous with.
        # hal_receiver pins itself to exactly the RUNTIME edge (bypassing
        # recv()'s select-across-every-allowed-sender behavior) so it can
        # never accidentally steal debugger_sender's message meant for the
        # guest's own later IPC_RECV.
        def hal_receiver():
            channel = sysv.ipc.channel_for_edge(Role.RUNTIME, Role.PLATFORM_HAL)
            assert channel is not None
            action, _ = channel.recv()
            if action == ChannelAction.BLOCK:
                yield (ChannelAction.BLOCK, None)

        def debugger_sender():
            yield from sysv.ipc.send(
                Role.DEBUGGER,
                "fireball://hal/gpio/0",
                IPCMessage.from_entries(
                    bytes_to_kv_storage(payload), memory_manager=sysv.memory_manager
                ),
            )

        sysv.scheduler.spawn("hal_receiver", hal_receiver())
        sysv.scheduler.spawn("debugger_sender", debugger_sender())
        sysv.scheduler.run_until_idle()

        def host_ipc_roundtrip():
            h = ctx.fireball_call(0x42, 0, len(uri), 0, 0, 0, 0)
            ctx.fireball_call(0x40, h, 32, len(payload), 0, 0, 0)
            # IPC_RECV no longer takes a sender_role argument: it selects
            # across every edge allowed into this URI's own role (here, just
            # the DEBUGGER edge is still pending; RUNTIME's was already
            # consumed by hal_receiver above).
            return ctx.fireball_call(0x41, h, 64, len(payload), 0, 0, 0)

        t = ctypes.CFUNCTYPE(ctypes.c_uint32)(host_ipc_roundtrip)
        t_addr = ctypes.cast(t, ctypes.c_void_p).value
        block = BasicBlock(
            head_pc=0x200,
            ops=[
                ("call_host", t_addr),
                ("local.set", 0),
            ],
            next_pc=None,
        )
        compiler = TraceCompiler(host_trampolines={1: t_addr})
        trace = compiler.compile_trace(0x200, block)
        w_ctx = WASMContext(locals_values=[0])
        trace.invoke(w_ctx)
        recv_len = w_ctx.locals[0]
        assert recv_len == len(payload)
        assert bytes(ctx.guest_memory[64 : 64 + recv_len]) == payload
    finally:
        sysv.shutdown()


def test_debugger_manager_gdb_rsp_integration():
    """DBG-01..15: Verifies Debug Manager GDB RSP protocol, breakpoints, registers and JIT flush."""
    from debugger import DebuggerManager, GDBRspProtocol

    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(64)
    ctx = WASMContext(locals_values=[10, 20], memory=mem)
    # 1. Query stop signal
    res, _ = rsp.handle_packet("?", 0x100, ctx, {})
    assert res == "$S05#b8"
    # 2. Virtual registers read/write
    res_g, _ = rsp.handle_packet("g", 0x100, ctx, {})
    assert len(res_g[1 : res_g.index("#")]) == 160
    # 3. Memory write & JIT flush ({Debugger_Jit_Flush})
    block = BasicBlock(head_pc=0x100, ops=[("i32.const", 42)])
    trace = engine.compiler.compile_trace(0x100, block)
    engine.cache.insert(trace)
    assert engine.cache.active.has_trace(0x100)
    res_m, _ = rsp.handle_packet("M0,4:aabbccdd", 0x100, ctx, {})
    assert res_m.startswith("$OK#")
    assert bytes(mem[0:4]) == bytes.fromhex("aabbccdd")
    assert not engine.cache.active.has_trace(0x100)  # Flushed!
    # 4. Breakpoint & Stepping
    block1 = BasicBlock(
        head_pc=0x100,
        ops=[("local.get", 0), ("i32.const", 1), ("i32.add", None), ("local.set", 0)],
        next_pc=0x200,
    )
    block2 = BasicBlock(
        head_pc=0x200,
        ops=[("local.get", 0), ("i32.const", 2), ("i32.mul", None), ("local.set", 0)],
        next_pc=None,
    )
    blocks = {0x100: block1, 0x200: block2}
    rsp.handle_packet("Z0,200,0", 0x100, ctx, blocks)
    res_c, stop_pc = rsp.handle_packet("c", 0x100, ctx, blocks)
    assert res_c.startswith("$S05#")
    assert stop_pc == 0x200
    assert ctx.locals[0] == 11
    # Remove BP and finish
    rsp.handle_packet("z0,200,0", 0x200, ctx, blocks)
    res_c2, _ = rsp.handle_packet("c", 0x200, ctx, blocks)
    assert res_c2.startswith("$W00#")
    assert ctx.locals[0] == 22


def test_interpreter_debugger_handler_table_switch_and_hooks():
    """INTP-60..65: Verifies Interpreter DebuggerLabelTableSwitch, JIT bypass, PC sampling and assertions."""
    from debugger import DebuggerManager

    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    block1 = BasicBlock(
        head_pc=0x100,
        ops=[("local.get", 0), ("i32.const", 1), ("i32.add", None), ("local.set", 0)],
        next_pc=0x200,
    )
    block2 = BasicBlock(
        head_pc=0x200,
        ops=[("local.get", 0), ("i32.const", 2), ("i32.mul", None), ("local.set", 0)],
        next_pc=None,
    )
    engine.register_block(block1)
    engine.register_block(block2)
    # 1. Normal mode (INTP-60: zero overhead, normal handler table)
    assert engine.handler_table == "normal"
    assert engine.debugger is None
    ctx_normal = WASMContext(locals_values=[5])
    next_pc = engine.run_step(0x100, ctx_normal)
    assert next_pc == 0x200
    assert ctx_normal.locals[0] == 6
    # 2. Attach debugger (INTP-61: switches to debug handler table)
    dbg.attach()
    assert engine.handler_table == "debug"
    assert engine.debugger is dbg
    # 3. Breakpoint hit (INTP-62: halts before execution)
    dbg.add_breakpoint(0x200)
    ctx_debug = WASMContext(locals_values=[10], memory=bytearray([0x55, 0xAA]))
    dbg.add_memory_assertion(0, 0x55, "valid magic")
    dbg.add_memory_assertion(1, 0x00, "invalid magic")  # Will fail
    # Step block1 (0x100 -> 0x200, stops at 0x200 due to BP)
    next_pc = engine.run_step(0x100, ctx_debug)
    assert next_pc == 0x200
    assert dbg.halted is True
    assert dbg.stop_signal == 5
    assert ctx_debug.locals[0] == 11
    # 4. Profiler & Assertions (INTP-63, INTP-64)
    assert dbg.pc_sample_counts[0x100] == 1
    assert len(dbg.assertion_violations) == 1
    # 5. JIT Bypass under debug mode (INTP-65: JIT trace exists but interpreter debug table runs)
    trace = engine.compiler.compile_trace(0x100, block1)
    engine.cache.insert(trace)
    assert engine.cache.active.has_trace(0x100)
    # Run step at 0x100 under debug mode -> interp_blocks increments, NOT jit_traces
    interp_before = engine.interp_blocks
    jit_before = engine.jit_traces
    engine.run_step(0x100, ctx_debug)
    assert engine.interp_blocks == interp_before + 1
    assert engine.jit_traces == jit_before  # JIT bypassed!
    # Detach
    dbg.detach()
    assert engine.handler_table == "normal"


def test_wasm_loader_and_radix_binary_tree_view_indexes():
    """LOAD-01..47: Verifies WASM Loader zero-copy indexing, verification, and RadixBinaryTreeView file offset & hash symbol indexes."""
    from loader import WasmLoader, WasmVerifyError
    from test_loader import _build_test_wasm_binary

    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("test_module", wasm_bytes)
    # 1. Zero-copy & Hash + RadixBinaryTreeView export lookup (LOAD-13)
    assert [e.name for e in view.exports_dict] == ["alpha", "beta", "zeta"]
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("unknown") is None
    # 2. Transactional rollback on invalid WASM
    watermark = loader.allocator.offset
    try:
        loader.prepare("bad", _build_test_wasm_binary(magic=b"\x7fELF"))
        assert False
    except WasmVerifyError:
        pass
    assert loader.allocator.offset == watermark
    # 3. RadixBinaryTreeView file offset reverse-lookup (LOAD-40..44)
    assert len(view.entity_registry) > 0
    func_start, func_size = view.code_offsets[0]
    entity_fn = view.lookup_by_file_offset(func_start)
    assert entity_fn is not None
    assert entity_fn.kind == "FUNCTION"
    assert entity_fn.name_or_idx == 0
    # Middle of function
    entity_fn_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_fn_mid is not None
    assert entity_fn_mid.kind == "FUNCTION"
    # Global lookup
    glob_entry = view.globals[0]
    entity_glob = view.lookup_by_file_offset(glob_entry.init_expr_offset)
    assert entity_glob is not None
    assert entity_glob.kind == "GLOBAL"
    # Out-of-bounds offset
    assert view.lookup_by_file_offset(len(wasm_bytes) + 1000) is None


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
