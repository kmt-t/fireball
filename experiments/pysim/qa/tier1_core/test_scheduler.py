from __future__ import annotations

"""
Unit tests for Tier 1 Core: Round-Robin Scheduler
Traceability: os_scheduler_test_spec.md
"""

from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from interrupt_event import InterruptEvent
from qa.shared.helpers import expect_assertion
from scheduler import (
    BoundedReadyQueue,
    ChannelAction,
    ChannelTransferMode,
    LockFreeInterruptEventQueue,
    Scheduler,
    Task,
    TaskState,
)


class CountedReadyTask(Task):
    """QA-only task that counts READY-link access without changing queue operations."""

    __slots__ = ("_following", "_previous", "link_reads", "link_writes")

    def __init__(self, task_id: int) -> None:
        self.link_reads = 0
        self.link_writes = 0
        self._previous: Task | None = None
        self._following: Task | None = None
        super().__init__(task_id)

    @property
    def ready_prev(self) -> Task | None:
        self.link_reads += 1
        return self._previous

    @ready_prev.setter
    def ready_prev(self, task: Task | None) -> None:
        self.link_writes += 1
        self._previous = task

    @property
    def ready_next(self) -> Task | None:
        self.link_reads += 1
        return self._following

    @ready_next.setter
    def ready_next(self, task: Task | None) -> None:
        self.link_writes += 1
        self._following = task


def _reset_ready_link_counts(tasks: tuple[CountedReadyTask, ...]) -> None:
    for task in tasks:
        task.link_reads = 0
        task.link_writes = 0


def _assert_bounded_ready_link_counts(tasks: tuple[CountedReadyTask, ...]) -> None:
    # Each operation touches a fixed neighborhood, never every READY task.
    assert sum(task.link_reads for task in tasks) <= 8
    assert sum(task.link_writes for task in tasks) <= 8


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


def test_sched_09_task_capacity_limit():
    """TEST-SCHED-09: Rejected overflow preserves the live tasks and READY order."""
    sched = Scheduler(max_tasks=4)
    for i in range(4):
        sched.spawn(f"t{i}")

    with expect_assertion("capacity exceeded"):
        sched.spawn("t_overflow")
    assert [task.task_id for task in sched._ready] == [1, 2, 3, 4]
    assert all(sched.get_task(task_id).state == TaskState.READY for task_id in range(1, 5))


def test_sched_10_duplicate_task_id_rejected():
    """TEST-SCHED-10: A duplicate ID cannot replace its task or alter READY order."""
    sched = Scheduler()
    sched.spawn("t1", task_id=10)
    original = sched.get_task(10)
    with expect_assertion("already exists"):
        sched.spawn("t2", task_id=10)
    assert sched.get_task(10) is original
    assert list(sched._ready) == [original]


# ===========================================================================
# 3. Memory Manager: Partitions & SharedBlock RAII (system_memory_test_spec.md / runtime_memory_test_spec.md)
# ===========================================================================


def test_mem_10_shared_block_move_semantics_csp_rendezvous():
    """TEST-MEM-10 / IPC_ZeroCopy: Move-only SharedBlock transfer across CSP channel.
    Upon rendezvous, ownership moves directly from sender to receiver.
    Sender instance is invalidated (use-after-move triggers assertion),
    while receiver acquires full ownership of the backing buffer."""
    from tier2_runtime.memory.manager import FB_CONF_MEMORY_POOL_SIZE, MemoryManager, SharedBlock

    sched = Scheduler()
    mm = MemoryManager(sched)
    mm.init_manager(pool_base=0x00010000, pool_size=FB_CONF_MEMORY_POOL_SIZE)
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
    _activate_task(sched, t2)
    assert recv_sb2.read_bytes(0, 15) == b"Subcase 2 Move!"

    with expect_assertion():
        sb2.read_bytes(0, 5)


def test_sched_13_detached_task_reattaches_once():
    """TEST-SCHED-13: Removing/reattaching a task preserves queue membership and links."""
    queue = BoundedReadyQueue(capacity=2)
    first = Task(1)
    second = Task(2)
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


def test_sched_13_ready_queue_intrusive_ring_two_ended_fifo():
    """TEST-SCHED-13: FIFO/ring links survive overflow rejection and arbitrary removal."""
    queue = BoundedReadyQueue(capacity=4)
    tasks = tuple(Task(task_id) for task_id in range(1, 6))
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
    assert list(queue) == expected
    assert fifth.ready_prev is None and fifth.ready_next is None
    assert queue.dequeue() is first
    assert queue.enqueue_front(fifth)
    assert list(queue) == [fifth, second, third, fourth]
    assert queue.remove(third)
    expected_after_remove = [fifth, second, fourth]
    assert list(queue) == expected_after_remove
    for index, task in enumerate(expected_after_remove):
        assert task.ready_next is expected_after_remove[(index + 1) % len(expected_after_remove)]
        assert task.ready_prev is expected_after_remove[(index - 1) % len(expected_after_remove)]


def test_sched_08_ready_queue_link_operations_are_bounded():
    """TEST-SCHED-08/13: Link accesses stay bounded as the READY ring grows."""
    for count in (0, 1, 4, 16, 64):
        queue = BoundedReadyQueue(capacity=count + 2)
        tasks = tuple(CountedReadyTask(task_id) for task_id in range(count + 3))
        initial = tasks[:count]
        tail, front, overflow = tasks[count:]
        for task in initial:
            assert queue.enqueue(task)

        _reset_ready_link_counts(tasks)
        assert queue.enqueue(tail)
        _assert_bounded_ready_link_counts(tasks)
        assert tuple(queue) == (*initial, tail)

        _reset_ready_link_counts(tasks)
        assert queue.enqueue_front(front)
        _assert_bounded_ready_link_counts(tasks)
        expected = (front, *initial, tail)
        assert tuple(queue) == expected

        _reset_ready_link_counts(tasks)
        assert not queue.enqueue(overflow)
        assert not queue.enqueue_front(overflow)
        assert sum(task.link_reads + task.link_writes for task in tasks) == 0
        assert tuple(queue) == expected

        _reset_ready_link_counts(tasks)
        assert queue.dequeue() is front
        _assert_bounded_ready_link_counts(tasks)
        assert tuple(queue) == (*initial, tail)

        _reset_ready_link_counts(tasks)
        assert queue.remove(tail)
        _assert_bounded_ready_link_counts(tasks)
        assert tuple(queue) == initial
        if count > 2:
            middle = initial[count // 2]
            _reset_ready_link_counts(tasks)
            assert queue.remove(middle)
            _assert_bounded_ready_link_counts(tasks)
            assert tuple(queue) == tuple(task for task in initial if task is not middle)


def test_sched_16_terminated_task_returns_its_tcb_slot_on_spawn():
    """TEST-SCHED-16: a full table reclaims terminated tasks; live tasks are never reclaimed."""

    def quick():
        return
        yield

    sched = Scheduler(max_tasks=4)
    first = [sched.spawn(f"t{i}", quick()) for i in range(4)]
    sched.run_to_completion()
    assert all(sched.get_task(i).state == TaskState.TERMINATED for i in first)

    # The table is full of terminated tasks, so a new task takes the oldest slot.
    fifth = sched.spawn("t4", quick())
    assert sched.get_task(first[0]) is None
    assert all(sched.get_task(i) is not None for i in first[1:])
    assert sched.get_task(fifth) is not None

    # Lifetime spawns exceed the table size when tasks finish in between.
    sched.run_to_completion()
    later = [sched.spawn(f"u{i}", quick()) for i in range(3)]
    sched.run_to_completion()
    assert len(later) == 3
    assert all(sched.get_task(i) is not None for i in later)

    # Four live tasks still fill the table: nothing is reclaimed from them.
    busy = Scheduler(max_tasks=4)

    def waiting():
        yield None
        yield None

    for i in range(4):
        busy.spawn(f"w{i}", waiting())
    with expect_assertion("capacity exceeded"):
        busy.spawn("w_overflow")


def test_sched_16_task_ids_stay_unique_after_a_slot_is_reclaimed():
    """TEST-SCHED-16: a reclaimed slot never hands its old task ID to a new task."""

    def quick():
        return
        yield

    sched = Scheduler(max_tasks=2)
    ids = [sched.spawn("a", quick()), sched.spawn("b", quick())]
    sched.run_to_completion()
    ids.append(sched.spawn("c", quick()))
    sched.run_to_completion()
    ids.append(sched.spawn("d", quick()))
    assert len(set(ids)) == 4


def test_sched_18_timed_wait_runs_ready_peers_before_idle_sleep():
    """TEST-SCHED-18: READY peers run before timer sleep; only the deadline resumes the waiter."""
    now_ns = 100
    sleep_intervals: list[int] = []
    idle_hook_times: list[int] = []
    events: list[tuple[str, int]] = []

    def clock_ns() -> int:
        return now_ns

    def sleep_ns(duration_ns: int) -> None:
        nonlocal now_ns
        sleep_intervals.append(duration_ns)
        now_ns += duration_ns

    sched = Scheduler(clock_ns=clock_ns, sleep_ns=sleep_ns)
    sched.set_idle_hook(lambda: idle_hook_times.append(now_ns))

    def timed_waiter():
        events.append(("wait", now_ns))
        sched.wait_until(105)
        yield (ChannelAction.BLOCK, None)
        events.append(("resume", now_ns))

    def ready_peer():
        events.append(("peer", now_ns))
        yield None

    waiter_id = sched.spawn("timed_waiter", timed_waiter())
    peer_id = sched.spawn("ready_peer", ready_peer())
    sched.run_until_idle()

    assert events == [("wait", 100), ("peer", 100), ("resume", 105)]
    assert sleep_intervals == [5]
    assert idle_hook_times == [100, 105]
    assert sched.get_task(waiter_id).state == TaskState.TERMINATED
    assert sched.get_task(peer_id).state == TaskState.TERMINATED
    assert sched.pending_task_count() == 0


def test_sched_19_killing_timed_waiter_clears_only_its_deadline():
    """TEST-SCHED-19 / GOTCHA-SCHED-03: Cancelling the earliest deadline preserves the survivor."""
    now_ns = 100
    sleep_intervals: list[int] = []
    resumed: list[str] = []

    def clock_ns() -> int:
        return now_ns

    def sleep_ns(duration_ns: int) -> None:
        nonlocal now_ns
        sleep_intervals.append(duration_ns)
        now_ns += duration_ns

    sched = Scheduler(clock_ns=clock_ns, sleep_ns=sleep_ns)

    def waiter(name: str, deadline_ns: int):
        sched.wait_until(deadline_ns)
        yield (ChannelAction.BLOCK, None)
        resumed.append(name)

    earliest_id = sched.spawn("earliest", waiter("earliest", 110))
    survivor_id = sched.spawn("survivor", waiter("survivor", 120))
    assert sched.step() is not None
    assert sched.step() is not None
    assert sched.get_task(earliest_id).state == TaskState.BLOCKED_TIMER
    assert sched.get_task(survivor_id).state == TaskState.BLOCKED_TIMER

    assert sched.task_killed(earliest_id)
    assert sched.get_task(earliest_id).state == TaskState.TERMINATED
    sched.run_until_idle()
    assert sleep_intervals == [20], "cancelled earliest deadline must not cause a stale wake cycle"
    assert resumed == ["survivor"]
    assert sched.get_task(survivor_id).state == TaskState.TERMINATED


def test_sched_02_spawn_appends_behind_existing_ready_peer() -> None:
    """TEST-SCHED-02: A task spawned by RUNNING A is dispatched after already READY B."""
    scheduler = Scheduler()
    events: list[str] = []

    def child():
        events.append("C")
        yield None

    def parent():
        events.append("A")
        scheduler.spawn("C", child())
        yield None
        events.append("A resumed")

    def peer():
        events.append("B")
        yield None

    a_id = scheduler.spawn("A", parent())
    b_id = scheduler.spawn("B", peer())
    assert scheduler.step().task_id == a_id
    assert [task.task_id for task in scheduler._ready] == [b_id, 3, a_id]
    assert scheduler.step().task_id == b_id
    assert scheduler.step().task_id == 3
    assert scheduler.step().task_id == a_id
    assert events == ["A", "B", "C", "A resumed"]


def test_sched_03_yield_returns_to_ready_and_runs_again() -> None:
    """TEST-SCHED-03: Each yield exposes READY; each subsequent dispatch exposes RUNNING."""
    scheduler = Scheduler()
    observed: list[TaskState] = []

    def worker():
        for _ in range(2):
            observed.append(scheduler.current_task.state)
            yield None
        observed.append(scheduler.current_task.state)

    task = scheduler.get_task(scheduler.spawn("worker", worker()))
    for _ in range(2):
        assert scheduler.step() is task
        assert task.state == TaskState.READY
        assert list(scheduler._ready) == [task]
        assert scheduler.current_task is None
    assert scheduler.step() is task
    assert observed == [TaskState.RUNNING] * 3
    assert task.state == TaskState.TERMINATED


def test_sched_05_terminated_task_is_never_redispatched() -> None:
    """TEST-SCHED-05: StopIteration removes the task from READY and future dispatches."""
    scheduler = Scheduler()
    events: list[str] = []

    def worker():
        events.append("finished")
        return
        yield

    task = scheduler.get_task(scheduler.spawn("worker", worker()))
    assert scheduler.step() is task
    assert task.state == TaskState.TERMINATED
    assert task.waiting_irq is None
    assert task not in scheduler._ready
    assert scheduler.step() is None
    assert scheduler.step() is None
    assert events == ["finished"]


def test_sched_04_06_07_interrupt_defers_targeted_wakeup_until_drain() -> None:
    """TEST-SCHED-04, TEST-SCHED-06, TEST-SCHED-07: BLOCKED idle, deferred wakeup, and exact cause handoff."""
    scheduler = Scheduler()
    received: list[tuple[int, int, int, int, int]] = []
    idle_states: list[tuple[TaskState, TaskState]] = []

    def waiter(vector_id: int):
        scheduler.wait_for_interrupt(vector_id)
        yield (ChannelAction.BLOCK, None)
        event = scheduler.consume_interrupt_event()
        assert event is not None
        received.append(event.words())

    a = scheduler.get_task(scheduler.spawn("A", waiter(7)))
    b = scheduler.get_task(scheduler.spawn("B", waiter(8)))
    scheduler.set_idle_hook(lambda: idle_states.append((a.state, b.state)))
    assert scheduler.step() is a
    assert scheduler.step() is b
    scheduler.run_until_idle()
    assert idle_states == [(TaskState.BLOCKED, TaskState.BLOCKED)]
    assert scheduler.step() is None

    assert scheduler.notify_interrupt(InterruptEvent(7, 91, 3, 0x1234, 0x5678))
    assert a.state == b.state == TaskState.BLOCKED
    assert list(scheduler._ready) == []
    assert a.pending_interrupt_event is None
    assert scheduler.drain_interrupts() == 1
    assert a.state == TaskState.READY
    assert a.waiting_irq is None
    assert list(scheduler._ready) == [a]
    assert b.state == TaskState.BLOCKED and b.waiting_irq == 8
    assert b.pending_interrupt_event is None
    scheduler.run_until_idle()
    assert received == [(7, 91, 3, 0x1234, 0x5678)]
    assert a.state == TaskState.TERMINATED
    assert b.state == TaskState.BLOCKED


def test_sched_11_unnotified_waiter_reaches_explicit_sweep_limit() -> None:
    """TEST-SCHED-11: A permanently BLOCKED IRQ waiter reports noncompletion within the budget."""
    scheduler = Scheduler()

    def waiter():
        scheduler.wait_for_interrupt(7)
        yield (ChannelAction.BLOCK, None)

    task = scheduler.get_task(scheduler.spawn("waiter", waiter()))
    with expect_assertion("within 2 sweeps"):
        scheduler.run_to_completion(max_sweeps=2)
    assert task.state == TaskState.BLOCKED
    assert task.waiting_irq == 7
    assert list(scheduler._ready) == []


def test_sched_12_interrupt_fifo_orders_registered_targets_and_drops_unknown() -> None:
    """TEST-SCHED-12: Event order governs wake order; an unknown vector changes no task."""
    scheduler = Scheduler()

    def waiter(vector_id: int):
        scheduler.wait_for_interrupt(vector_id)
        yield (ChannelAction.BLOCK, None)

    a = scheduler.get_task(scheduler.spawn("A", waiter(7)))
    b = scheduler.get_task(scheduler.spawn("B", waiter(8)))
    scheduler.step()
    scheduler.step()
    for event in (
        InterruptEvent(8, 2, 3, 4, 5),
        InterruptEvent(99, 0, 0, 0, 0),
        InterruptEvent(7, 6, 7, 8, 9),
    ):
        assert scheduler.notify_interrupt(event)
    assert a.state == b.state == TaskState.BLOCKED
    assert scheduler.drain_interrupts() == 3
    assert list(scheduler._ready) == [b, a]
    assert a.pending_interrupt_event.words() == (7, 6, 7, 8, 9)
    assert b.pending_interrupt_event.words() == (8, 2, 3, 4, 5)
    assert scheduler.dropped_irqs == 1
    assert len(scheduler.interrupt_event_queue) == 0


def test_sched_14_15_interrupt_generation_is_observed_once_by_existing_targets() -> None:
    """TEST-SCHED-14, TEST-SCHED-15: One burst creates one round; a newly spawned task is excluded."""
    scheduler = Scheduler()
    a = scheduler.get_task(scheduler.spawn("A"))
    b = scheduler.get_task(scheduler.spawn("B"))
    for vector in (98, 99):
        assert scheduler.notify_interrupt(InterruptEvent(vector, 0, 0, 0, 0))
    assert scheduler.reschedule_generation == 1
    assert scheduler.reschedule_pending
    assert scheduler.step() is a
    assert a.last_seen_generation == 1
    assert b.last_seen_generation == 0
    assert scheduler.round_target_mask == 1 << b.task_id
    assert scheduler.reschedule_pending
    c = scheduler.get_task(scheduler.spawn("C"))
    assert c.last_seen_generation == 1
    assert scheduler.round_target_mask & (1 << c.task_id) == 0
    assert not scheduler.observe_reschedule_generation(c)
    assert scheduler.reschedule_pending
    assert scheduler.step() is b
    assert b.last_seen_generation == 1
    assert not scheduler.reschedule_pending
    assert scheduler.round_target_mask == 0
    assert not scheduler.observe_reschedule_generation(a)
    assert not scheduler.observe_reschedule_generation(b)
    assert scheduler.reschedule_generation == 1


def test_sched_17_interrupt_fifo_rejects_overflow_then_reuses_consumed_slot() -> None:
    """TEST-SCHED-17: Overflow preserves cause records; wraparound reuses only a consumed slot."""
    queue = LockFreeInterruptEventQueue(capacity=2)
    a = InterruptEvent(1, 2, 3, 4, 5)
    b = InterruptEvent(6, 7, 8, 9, 10)
    c = InterruptEvent(11, 12, 13, 14, 15)
    assert queue.push(a)
    assert queue.push(b)
    assert not queue.push(c)
    assert len(queue) == 2
    assert queue.pop() == a
    assert queue.push(c)
    assert queue.pop() == b
    assert queue.pop() == c
    assert queue.pop() is None
    assert len(queue) == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([str(_TEST_FILE)]))
