from __future__ import annotations

"""
Unit tests for Tier 1 Core: Round-Robin Scheduler
Traceability: os_scheduler_test_spec.md
"""

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
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from helpers import expect_assertion
from scheduler import (
    BoundedReadyQueue,
    ChannelAction,
    ChannelTransferMode,
    Scheduler,
    Task,
    TaskState,
)


def _activate_task(scheduler: Scheduler, task: Task) -> None:
    scheduler.current_task = None
    scheduler.detach(task)
    scheduler.activate_task(task)


def test_sched_01_pure_round_robin_fifo():
    """TEST-SCHED-01: Pure round-robin execution without priority bias."""
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
    """TEST-SCHED-02: Scheduler enforces FB_CONF_MAX_TASKS (16) limit."""
    sched = Scheduler(max_tasks=4)
    for i in range(4):
        sched.spawn(f"t{i}")

    with expect_assertion("capacity exceeded"):
        sched.spawn("t_overflow")


def test_sched_03_duplicate_task_id_rejected():
    """TEST-SCHED-03: Attempting to spawn with an existing task_id is rejected."""
    sched = Scheduler()
    sched.spawn("t1", task_id=10)
    with expect_assertion("already exists"):
        sched.spawn("t2", task_id=10)


# ===========================================================================
# 3. Memory Manager: Partitions & SharedBlock RAII (system_memory_test_spec.md / runtime_memory_test_spec.md)
# ===========================================================================


def test_sched_04_shared_block_move_semantics_csp_rendezvous():
    """TEST-MEM-10 / IPC_ZeroCopy: Move-only SharedBlock transfer across CSP channel.
    Upon rendezvous, ownership moves directly from sender to receiver.
    Sender instance is invalidated (use-after-move triggers assertion),
    while receiver acquires full ownership of the backing buffer."""
    from memory import FB_CONF_MEMORY_POOL_SIZE, MemoryManager, SharedBlock

    sched = Scheduler()
    mm = MemoryManager(sched)
    mm.init_manager(pool_base=0x20020000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
    ch = sched.create_channel(transfer_mode=ChannelTransferMode.MOVABLE)

    t1 = sched.get_task(sched.spawn("sender", task_id=1))
    t2 = sched.get_task(sched.spawn("receiver", task_id=2))

    # Task 1 allocates a SharedBlock
    _activate_task(sched, t1)
    sb = mm.allocate_shared(size=64).unwrap()
    sb.write_bytes(0, b"Hello Fireball CSP Move Semantics!")

    # Step 1: Task 1 sends the SharedBlock directly via channel
    _activate_task(sched, t1)
    action_send, _ = ch.send(sb)
    assert action_send == ChannelAction.BLOCK
    assert t1.state == TaskState.SUSPENDED_CSP
    assert t1.pending_val is sb
    # While waiting, sender can still access
    assert sb.read_bytes(0, 5) == b"Hello"

    # Step 2: Task 2 arrives and receives
    _activate_task(sched, t2)
    action_recv, _ = ch.recv()
    assert action_recv in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)

    # Receiver acquired the moved SharedBlock
    recv_sb = t2.received_val
    assert isinstance(recv_sb, SharedBlock)
    assert recv_sb.get_owner() == 2
    assert recv_sb.read_bytes(0, 34) == b"Hello Fireball CSP Move Semantics!"

    # Sender instance was invalidated via move semantics (C++23 std::move / &&)
    with expect_assertion():
        sb.read_bytes(0, 5)

    with expect_assertion():
        sb.write_bytes(0, b"Fail")

    # Sub-case 2: Receiver waits first, Sender arrives second
    ch2 = sched.create_channel(transfer_mode=ChannelTransferMode.MOVABLE)
    _activate_task(sched, t1)
    sb2 = mm.allocate_shared(size=64).unwrap()
    sb2.write_bytes(0, b"Subcase 2 Move!")

    _activate_task(sched, t2)
    action_recv2, _ = ch2.recv()
    assert action_recv2 == ChannelAction.BLOCK
    assert t2.state == TaskState.SUSPENDED_CSP

    _activate_task(sched, t1)
    action_send2, _ = ch2.send(sb2)
    assert action_send2 in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)

    recv_sb2 = t2.received_val
    assert isinstance(recv_sb2, SharedBlock)
    assert recv_sb2.get_owner() == 2
    assert recv_sb2.read_bytes(0, 15) == b"Subcase 2 Move!"

    with expect_assertion():
        sb2.read_bytes(0, 5)


def test_sched_05_queue_and_detached_task_lifecycle():
    queue = BoundedReadyQueue(capacity=2)
    first = Task(1, "first")
    second = Task(2, "second")
    assert queue.enqueue(first)
    assert queue.enqueue_front(second)
    assert len(queue) == 2
    assert first in queue
    assert list(queue) == [second, first]
    assert queue.remove(second)
    assert not queue.contains(second)
    assert list(queue) == [first]
    queue.clear()
    assert first.ready_prev is None and first.ready_next is None
    assert not queue
    assert queue.enqueue(first)
    assert queue.remove(first)
    assert not queue

    scheduler = Scheduler(max_tasks=3)
    task_id = scheduler.spawn("detached")
    task = scheduler.get_task(task_id)
    assert task is not None
    scheduler.detach(task)
    assert scheduler.pending_task_count() == 0
    scheduler.attach(task)
    assert scheduler.pending_task_count() == 1
    scheduler.attach(task)
    assert scheduler.pending_task_count() == 1


def test_sched_06_ready_queue_intrusive_ring_two_ended_fifo():
    queue = BoundedReadyQueue(capacity=4)
    tasks = tuple(Task(task_id, f"t{task_id}") for task_id in range(1, 6))
    first, second, third, fourth, fifth = tasks

    assert queue.enqueue(first)
    assert queue.enqueue(second)
    assert queue.enqueue(third)
    assert queue.dequeue() is first
    assert queue.enqueue(fourth)
    assert queue.enqueue_front(first)
    expected = [first, second, third, fourth]
    assert list(queue) == expected
    for index, task in enumerate(expected):
        assert task.ready_next is expected[(index + 1) % len(expected)]
        assert task.ready_prev is expected[(index - 1) % len(expected)]
    assert not queue.enqueue(fifth)
    assert not queue.enqueue_front(fifth)
    assert queue.dequeue() is first
    assert queue.enqueue_front(fifth)
    assert list(queue) == [fifth, second, third, fourth]
    assert queue.remove(third)
    expected_after_remove = [fifth, second, fourth]
    assert list(queue) == expected_after_remove
    for index, task in enumerate(expected_after_remove):
        assert task.ready_next is expected_after_remove[(index + 1) % len(expected_after_remove)]
        assert task.ready_prev is expected_after_remove[(index - 1) % len(expected_after_remove)]


if __name__ == "__main__":
    test_sched_01_pure_round_robin_fifo()
    test_sched_02_task_capacity_limit()
    test_sched_03_duplicate_task_id_rejected()
    test_sched_04_shared_block_move_semantics_csp_rendezvous()
    test_sched_05_queue_and_detached_task_lifecycle()
    test_sched_06_ready_queue_intrusive_ring_two_ended_fifo()
    print("[PASS] All 6 Round-Robin Scheduler tests passed.")
