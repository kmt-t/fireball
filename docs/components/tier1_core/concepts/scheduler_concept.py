"""
docs/components/tier1_core/concepts/scheduler_concept.py
Reference Concept Implementation: COOS Round-Robin Scheduler
Implementation Invariants & Gotchas:
- Pure FIFO round-robin dispatch without priority (ADR_CoosPureRoundRobin).
- Fixed capacity bounds (FB_CONF_MAX_TASKS = 16) with zero dynamic allocation.
- SCHED-GOTCHA-01: Round-robin fairness ensures all ready tasks receive deterministic CPU time.
"""

from collections.abc import Generator
from enum import IntEnum


class TaskState(IntEnum):
    READY = 1
    RUNNING = 2
    BLOCKED = 3
    TERMINATED = 4


class TaskControlBlock:
    __slots__ = ("block_reason", "coro", "dispatches", "id", "state")

    def __init__(self, task_id: str, coro: Generator):
        self.id = task_id
        self.coro = coro
        self.state = TaskState.READY
        self.dispatches = 0
        self.block_reason: str | None = None


class RoundRobinScheduler:
    def __init__(self, max_tasks: int = 16):
        self.max_tasks = max_tasks
        self.tasks: dict[str, TaskControlBlock] = {}
        self.ready_ring: list[str] = []
        self.current_task: str | None = None
        self.total_dispatches = 0

    def spawn(self, task_id: str, coroutine: Generator) -> bool:
        """
        Register a new task into the fixed task table and ready ring.
        No priority parameter: scheduling is pure FIFO round-robin (D1).
        """
        assert len(self.tasks) < self.max_tasks, "Max task capacity exceeded"
        assert task_id not in self.tasks, f"Task {task_id} already exists"
        self.tasks[task_id] = TaskControlBlock(task_id, coroutine)
        self.ready_ring.append(task_id)
        return True

    def schedule_next(self) -> str | None:
        """Selects the next task in O(1) from the ready ring."""
        if not self.ready_ring:
            return None
        task_id = self.ready_ring.pop(0)
        self.current_task = task_id
        tcb = self.tasks[task_id]
        tcb.state = TaskState.RUNNING
        tcb.dispatches += 1
        self.total_dispatches += 1
        return task_id

    def yield_current(self) -> None:
        """Cooperative yield: move current task to the tail of the ready ring."""
        assert self.current_task is not None, "No active task to yield"
        task_id = self.current_task
        tcb = self.tasks[task_id]
        tcb.state = TaskState.READY
        self.ready_ring.append(task_id)
        self.current_task = None

    def block_current(self, reason: str = "WAIT") -> None:
        """Block current task on event/IPC: removed from ready ring."""
        assert self.current_task is not None, "No active task to block"
        task_id = self.current_task
        tcb = self.tasks[task_id]
        tcb.state = TaskState.BLOCKED
        tcb.block_reason = reason
        self.current_task = None

    def unblock_task(self, task_id: str) -> None:
        """Unblock task on event arrival: append to ready ring."""
        assert task_id in self.tasks, f"Unknown task {task_id}"
        tcb = self.tasks[task_id]
        if tcb.state == TaskState.BLOCKED:
            tcb.state = TaskState.READY
            tcb.block_reason = None
            self.ready_ring.append(task_id)

    def terminate_current(self) -> None:
        """Terminate active task."""
        assert self.current_task is not None
        task_id = self.current_task
        self.tasks[task_id].state = TaskState.TERMINATED
        self.current_task = None

    def run_cycle(self) -> bool:
        """Dispatches and advances one active task."""
        task_id = self.schedule_next()
        if task_id is None:
            return False  # All tasks blocked or completed
        tcb = self.tasks[task_id]
        try:
            action = tcb.coro.send(None)
            if action == "YIELD":
                self.yield_current()
            elif action == "BLOCK":
                self.block_current()
        except StopIteration:
            self.terminate_current()
        return True


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_round_robin_fairness() -> None:
    sched = RoundRobinScheduler(max_tasks=4)
    exec_order = []

    def task_a() -> Generator[str, None, None]:
        exec_order.append("A1")
        yield "YIELD"
        exec_order.append("A2")
        yield "YIELD"

    def task_b() -> Generator[str, None, None]:
        exec_order.append("B1")
        yield "YIELD"
        exec_order.append("B2")
        yield "YIELD"

    sched.spawn("A", task_a())
    sched.spawn("B", task_b())
    while sched.run_cycle():
        pass
    assert exec_order == ["A1", "B1", "A2", "B2"], f"Unexpected execution order: {exec_order}"
    assert sched.tasks["A"].state == TaskState.TERMINATED
    assert sched.tasks["B"].state == TaskState.TERMINATED
    assert sched.total_dispatches == 6


def test_block_and_unblock_cycle() -> None:
    sched = RoundRobinScheduler(max_tasks=4)
    trace = []

    def worker() -> Generator[str, None, None]:
        trace.append("W_START")
        yield "BLOCK"
        trace.append("W_RESUMED")

    sched.spawn("W", worker())
    # Step 1: Worker runs and blocks
    sched.run_cycle()
    assert trace == ["W_START"]
    assert sched.tasks["W"].state == TaskState.BLOCKED
    assert len(sched.ready_ring) == 0
    # Step 2: Unblock worker
    sched.unblock_task("W")
    assert sched.tasks["W"].state == TaskState.READY
    assert len(sched.ready_ring) == 1
    # Step 3: Run worker to completion
    sched.run_cycle()
    assert trace == ["W_START", "W_RESUMED"]
    assert sched.tasks["W"].state == TaskState.TERMINATED


if __name__ == "__main__":
    test_round_robin_fairness()
    test_block_and_unblock_cycle()
    print("[PASS] All Scheduler concept tests passed successfully.")
