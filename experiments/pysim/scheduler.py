"""
experiments/pysim/scheduler.py

A pure cooperative round-robin scheduler over Python generators, mirroring
docs/components/tier1_core/os_scheduler.md's {ADR_CoosPureRoundRobin}: no
priority, FIFO dispatch, a task keeps running until it `yield`s.

A task is a Python generator. `yield None` means "plain cooperative yield,
put me back at the tail of READY". `yield <event_key>` means "block me
until someone calls notify_event(event_key)" -- matching
{ADR_EventDrivenWakeQueue}: wake-ups are a direct dict lookup on the event
key, never a scan over every BLOCKED task.
"""

from __future__ import annotations

from collections import deque
from enum import Enum, auto
from typing import Any, Callable, Generator


class TaskState(Enum):
    READY = auto()
    RUNNING = auto()
    BLOCKED = auto()
    TERMINATED = auto()


class Task:
    def __init__(self, task_id: int, name: str, coro: Generator[Any, None, None]):
        self.task_id = task_id
        self.name = name
        self.coro = coro
        self.state = TaskState.READY


class Scheduler:
    def __init__(self):
        self._ready: deque[Task] = deque()
        self._blocked_by_event: dict[Any, list[Task]] = {}
        self._all: dict[int, Task] = {}
        self._next_id = 1
        self.idle_hooks: list[Callable[[], None]] = []

    def spawn(self, name: str, coro: Generator[Any, None, None]) -> int:
        task = Task(self._next_id, name, coro)
        self._next_id += 1
        self._all[task.task_id] = task
        self._ready.append(task)
        return task.task_id

    def set_idle_hook(self, fn: Callable[[], None]) -> None:
        self.idle_hooks.append(fn)

    def notify_event(self, event_key: Any) -> None:
        """{ADR_EventDrivenWakeQueue}: O(1) wake via a direct dict lookup on
        the event key, never a linear scan over all BLOCKED tasks."""
        woken = self._blocked_by_event.pop(event_key, [])
        for task in woken:
            task.state = TaskState.READY
            self._ready.append(task)

    def pending_task_count(self) -> int:
        return len(self._ready) + sum(len(v) for v in self._blocked_by_event.values())

    def run_until_idle(self) -> None:
        """Runs one full round-robin sweep, then fires idle hooks once the
        READY queue drains -- matching os_coos.md 4.2's Idle transition."""
        while self._ready:
            task = self._ready.popleft()
            task.state = TaskState.RUNNING
            try:
                wait_on = next(task.coro)
            except StopIteration:
                task.state = TaskState.TERMINATED
                continue

            if wait_on is None:
                task.state = TaskState.READY
                self._ready.append(task)
            else:
                task.state = TaskState.BLOCKED
                self._blocked_by_event.setdefault(wait_on, []).append(task)

        for hook in self.idle_hooks:
            hook()

    def run_to_completion(self, max_sweeps: int = 1000) -> None:
        for _ in range(max_sweeps):
            self.run_until_idle()
            if not self._ready and not self._blocked_by_event:
                return
        raise RuntimeError(
            f"scheduler did not reach idle within {max_sweeps} sweeps "
            "(a task is stuck BLOCKED on an event nobody notifies)"
        )
