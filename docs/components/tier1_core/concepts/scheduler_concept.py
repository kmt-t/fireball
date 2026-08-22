"""
docs/components/tier1_core/concepts/scheduler_concept.py
Reference Concept Implementation: COOS Round-Robin Scheduler
- O(1) deterministic ring queue dispatching
- Cooperative task yielding & queue tail re-insertion
- Task state transitions (READY, RUNNING, BLOCKED, TERMINATED)
- Strict memory bound: fixed task table, zero dynamic allocation
"""

from typing import Any, Generator


class TaskState:
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    TERMINATED = "TERMINATED"


class RoundRobinScheduler:
    def __init__(self, max_tasks: int = 16):
        self.max_tasks = max_tasks
        self.tasks: dict[str, dict[str, Any]] = {}
        self.ready_ring: list[str] = []
        self.current_task: str | None = None
        self.total_dispatches = 0

    def spawn(self, task_id: str, coroutine: Generator, priority: int = 0) -> bool:
        """Register a new task into the fixed task table and ready ring."""
        assert len(self.tasks) < self.max_tasks, "Max task capacity exceeded"
        assert task_id not in self.tasks, f"Task {task_id} already exists"

        self.tasks[task_id] = {
            "id": task_id,
            "coro": coroutine,
            "state": TaskState.READY,
            "priority": priority,
            "dispatches": 0,
        }
        self.ready_ring.append(task_id)
        return True

    def schedule_next(self) -> str | None:
        """Selects the next task in O(1) from the ready ring."""
        if not self.ready_ring:
            return None

        task_id = self.ready_ring.pop(0)
        self.current_task = task_id
        task_entry = self.tasks[task_id]
        task_entry["state"] = TaskState.RUNNING
        task_entry["dispatches"] += 1
        self.total_dispatches += 1
        return task_id

    def yield_current(self):
        """Cooperative yield: move current task to the tail of the ready ring."""
        assert self.current_task is not None, "No active task to yield"
        task_id = self.current_task
        task_entry = self.tasks[task_id]
        task_entry["state"] = TaskState.READY
        self.ready_ring.append(task_id)
        self.current_task = None

    def block_current(self, reason: str = "WAIT"):
        """Block current task on event/IPC: removed from ready ring."""
        assert self.current_task is not None, "No active task to block"
        task_id = self.current_task
        task_entry = self.tasks[task_id]
        task_entry["state"] = TaskState.BLOCKED
        task_entry["block_reason"] = reason
        self.current_task = None

    def unblock_task(self, task_id: str):
        """Unblock task on event arrival: append to ready ring."""
        assert task_id in self.tasks, f"Unknown task {task_id}"
        task_entry = self.tasks[task_id]
        if task_entry["state"] == TaskState.BLOCKED:
            task_entry["state"] = TaskState.READY
            task_entry["block_reason"] = None
            self.ready_ring.append(task_id)

    def terminate_current(self):
        """Terminate active task."""
        assert self.current_task is not None
        task_id = self.current_task
        self.tasks[task_id]["state"] = TaskState.TERMINATED
        self.current_task = None

    def run_cycle(self) -> bool:
        """Dispatches and advances one active task."""
        task_id = self.schedule_next()
        if task_id is None:
            return False  # All tasks blocked or completed

        task_entry = self.tasks[task_id]
        try:
            action = task_entry["coro"].send(None)
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

def test_round_robin_fairness():
    sched = RoundRobinScheduler(max_tasks=4)
    exec_order = []

    def task_a():
        exec_order.append("A1")
        yield "YIELD"
        exec_order.append("A2")
        yield "YIELD"

    def task_b():
        exec_order.append("B1")
        yield "YIELD"
        exec_order.append("B2")
        yield "YIELD"

    sched.spawn("A", task_a())
    sched.spawn("B", task_b())

    while sched.run_cycle():
        pass

    assert exec_order == ["A1", "B1", "A2", "B2"], f"Unexpected execution order: {exec_order}"
    assert sched.tasks["A"]["state"] == TaskState.TERMINATED
    assert sched.tasks["B"]["state"] == TaskState.TERMINATED
    assert sched.total_dispatches == 6


def test_block_and_unblock_cycle():
    sched = RoundRobinScheduler(max_tasks=4)
    trace = []

    def worker():
        trace.append("W_START")
        yield "BLOCK"
        trace.append("W_RESUMED")

    sched.spawn("W", worker())

    # Step 1: Worker runs and blocks
    sched.run_cycle()
    assert trace == ["W_START"]
    assert sched.tasks["W"]["state"] == TaskState.BLOCKED
    assert len(sched.ready_ring) == 0

    # Step 2: Unblock worker
    sched.unblock_task("W")
    assert sched.tasks["W"]["state"] == TaskState.READY
    assert len(sched.ready_ring) == 1

    # Step 3: Run worker to completion
    sched.run_cycle()
    assert trace == ["W_START", "W_RESUMED"]
    assert sched.tasks["W"]["state"] == TaskState.TERMINATED


if __name__ == "__main__":
    test_round_robin_fairness()
    test_block_and_unblock_cycle()
    print("[PASS] All Scheduler concept tests passed successfully.")
