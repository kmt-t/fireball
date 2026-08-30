"""
experiments/pysim/tests.py

Comprehensive assert-based invariant test suite for the pysim experiment,
covering all Fireball component test specifications (*_test_spec.md):
- Tier 1 COOS & Scheduler (os_coos_test_spec.md, os_scheduler_test_spec.md)
- Tier 1 Logging & IPC Router (system_logging_test_spec.md, ipc_router_test_spec.md)
- Tier 2 vMMIO & Recovery (runtime_vmmio_test_spec.md)
- Tier 3 Platform Memory & HAL & MPU W^X (platform_memory_test_spec.md, platform_hal_test_spec.md)
- Tier 1/2 Syscalls (system_syscall_test_spec.md)

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


# ===========================================================================
# 1. Tier 1 COOS: Hoare CSP Rendezvous Channel (os_coos_test_spec.md)
# ===========================================================================

def test_coos_01_send_first_suspends_csp():
    """COOS-01: Sender arriving first transitions to SUSPENDED_CSP; value stays in frame."""
    sched = Scheduler()
    ch = sched.create_channel("ch_test")
    t1_id = sched.spawn("t1")
    t1 = sched._all[t1_id]
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
    t1 = sched._all[sched.spawn("t1")]
    t2 = sched._all[sched.spawn("t2")]

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
    t2 = sched._all[sched.spawn("t2")]
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
    t1 = sched._all[sched.spawn("t1")]
    t2 = sched._all[sched.spawn("t2")]

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
    t1 = sched._all[sched.spawn("t1")]
    t2 = sched._all[sched.spawn("t2")]

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
    t1 = sched._all[sched.spawn("t1")]
    t2 = sched._all[sched.spawn("t2")]

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

    t1 = sched._all[sched.spawn("t1")]
    t2 = sched._all[sched.spawn("t2")]

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


def test_mem_11_shared_block_raii_auto_deallocate():
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
# 6. fireball_call Full Syscall Surface (system_syscall_test_spec.md)
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
