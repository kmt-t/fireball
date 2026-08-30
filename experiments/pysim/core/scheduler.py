"""

experiments/pysim/scheduler.py



A cooperative round-robin scheduler and Hoare CSP rendezvous engine, mirroring

docs/components/tier1_core/os_scheduler.md ({ADR_CoosPureRoundRobin}) and

docs/components/tier1_core/os_coos.md ({ADR_RendezvousChannel}, {CSP_Handoff}).

- Pure round-robin FIFO dispatch (no priority).

- Fixed capacity check: FB_CONF_MAX_TASKS = 16.

- Bufferless synchronous Hoare CSP channels with single-waiter enforcement.

- Direct symmetric context switch with consecutive handoff bound (FB_CONF_MAX_CONSECUTIVE_HANDOFFS = 4).

- Asynchronous ISR interrupt notification queue and drain wake-up.

"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1] if any(d in str(Path(__file__)) for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")) else Path(__file__).resolve().parent

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys

from pathlib import Path



from collections import deque

from enum import Enum, auto

from typing import Any, Callable, Generator



FB_CONF_MAX_TASKS = 16

FB_CONF_MAX_CONSECUTIVE_HANDOFFS = 4

FB_CONF_INTERRUPT_QUEUE_SIZE = 16





class TaskState(Enum):

    READY = auto()

    RUNNING = auto()

    BLOCKED = auto()

    SUSPENDED_CSP = auto()

    TERMINATED = auto()





class WaitDir(Enum):

    NONE = auto()

    SEND = auto()

    RECV = auto()





class Channel:

    """Bufferless synchronous CSP rendezvous channel (ADR_RendezvousChannel)."""

    def __init__(self, channel_id: str):

        self.channel_id = channel_id

        self.waiter_task: Task | None = None

        self.waiter_dir: WaitDir = WaitDir.NONE





class Task:

    def __init__(self, task_id: int | str, name: str, coro: Generator[Any, None, None] | None):

        self.task_id = task_id

        self.name = name

        self.coro = coro

        self.state = TaskState.READY

        self.pending_val: Any = None

        self.received_val: Any = None





class Scheduler:

    def __init__(self, max_tasks: int = FB_CONF_MAX_TASKS, max_handoffs: int = FB_CONF_MAX_CONSECUTIVE_HANDOFFS):

        self.max_tasks = max_tasks

        self.max_handoffs = max_handoffs

        self.consecutive_handoffs = 0



        self._ready: deque[Task] = deque()

        self._blocked_by_event: list[tuple[Any, list[Task]]] = []

        self._all: list[Task] = []

        self._channels: list[Channel] = []



        self.current_task: Task | None = None

        self._next_id = 1

        self.idle_hooks: list[Callable[[], None]] = []



        self.interrupt_event_queue: deque[int] = deque(maxlen=FB_CONF_INTERRUPT_QUEUE_SIZE)

        self.irq_waiters: list[tuple[int, list[Task]]] = []

        self.dropped_irqs = 0



    def get_task(self, task_id: int | str) -> Task | None:

        for t in self._all:

            if t.task_id == task_id:

                return t

        return None



    def spawn(self, name: str, coro: Generator[Any, None, None] | None = None, task_id: int | str | None = None) -> int | str:

        """Spawn a new task within FB_CONF_MAX_TASKS bounds."""

        if len(self._all) >= self.max_tasks:

            raise RuntimeError(f"Task capacity exceeded (max {self.max_tasks})")



        assigned_id = task_id if task_id is not None else self._next_id

        if self.get_task(assigned_id) is not None:

            raise ValueError(f"Task with ID {assigned_id} already exists")



        if task_id is None:

            self._next_id += 1



        task = Task(assigned_id, name, coro)

        self._all.append(task)

        self._ready.append(task)

        return task.task_id



    def get_channel(self, channel_id: str) -> Channel | None:

        for ch in self._channels:

            if ch.channel_id == channel_id:

                return ch

        return None



    def create_channel(self, channel_id: str) -> Channel:

        existing = self.get_channel(channel_id)

        if existing is not None:

            return existing

        ch = Channel(channel_id)

        self._channels.append(ch)

        return ch



    def channel_send(self, channel_id: str, data: Any) -> tuple[str, Any]:

        """Synchronous CSP send with atomic ownership handoff."""

        ch = self.get_channel(channel_id)

        if ch is None:

            ch = self.create_channel(channel_id)

        sender = self.current_task

        assert sender is not None, "channel_send requires active running task"



        if ch.waiter_dir == WaitDir.RECV:

            receiver = ch.waiter_task

            assert receiver is not None

            ch.waiter_task, ch.waiter_dir = None, WaitDir.NONE



            receiver.received_val = data

            receiver.state = TaskState.READY

            sender.state = TaskState.READY

            return self._handoff_or_yield(receiver)



        assert ch.waiter_dir != WaitDir.SEND, \
            "one waiter per channel: concurrent senders must use separate channels"

        ch.waiter_task, ch.waiter_dir = sender, WaitDir.SEND

        sender.pending_val = data

        sender.state = TaskState.SUSPENDED_CSP

        return ("BLOCK", None)



    def channel_recv(self, channel_id: str) -> tuple[str, Any]:

        """Synchronous CSP recv with atomic ownership handoff."""

        ch = self.get_channel(channel_id)

        if ch is None:

            ch = self.create_channel(channel_id)

        receiver = self.current_task

        assert receiver is not None, "channel_recv requires active running task"



        if ch.waiter_dir == WaitDir.SEND:

            sender = ch.waiter_task

            assert sender is not None

            val = sender.pending_val

            sender.pending_val = None  # Prevent double ownership

            ch.waiter_task, ch.waiter_dir = None, WaitDir.NONE



            receiver.received_val = val

            sender.state = TaskState.READY

            receiver.state = TaskState.READY

            return self._handoff_or_yield(sender)



        assert ch.waiter_dir != WaitDir.RECV, \
            "one waiter per channel: concurrent receivers must use separate channels"

        ch.waiter_task, ch.waiter_dir = receiver, WaitDir.RECV

        receiver.state = TaskState.SUSPENDED_CSP

        return ("BLOCK", None)



    def _handoff_or_yield(self, target_task: Task) -> tuple[str, Any]:

        """CSP direct handoff or scheduler yield upon consecutive threshold."""

        if self.consecutive_handoffs < self.max_handoffs:

            self.consecutive_handoffs += 1

            if target_task in self._ready:

                self._ready.remove(target_task)

            self._ready.appendleft(target_task)

            return ("DIRECT_SWITCH", target_task.task_id)



        self.consecutive_handoffs = 0

        return ("YIELD", None)



    def notify_interrupt(self, irq_id: int) -> bool:

        """Non-blocking ISR notification to event queue."""

        if len(self.interrupt_event_queue) >= FB_CONF_INTERRUPT_QUEUE_SIZE:

            self.dropped_irqs += 1

            return False

        self.interrupt_event_queue.append(irq_id)

        return True



    def drain_interrupts(self) -> int:

        """Drain IRQ queue and wake registered tasks."""

        count = 0

        while self.interrupt_event_queue:

            irq_id = self.interrupt_event_queue.popleft()

            count += 1

            waiters = []

            for i, (qid, wlist) in enumerate(self.irq_waiters):

                if qid == irq_id:

                    waiters = wlist

                    self.irq_waiters.pop(i)

                    break

            for task in waiters:

                task.state = TaskState.READY

                self._ready.append(task)

        return count



    def wait_for_interrupt(self, irq_id: int) -> None:

        task = self.current_task

        assert task is not None

        task.state = TaskState.BLOCKED

        for qid, wlist in self.irq_waiters:

            if qid == irq_id:

                wlist.append(task)

                return

        self.irq_waiters.append((irq_id, [task]))



    def set_idle_hook(self, fn: Callable[[], None]) -> None:

        self.idle_hooks.append(fn)



    def notify_event(self, event_key: Any) -> None:

        woken = []

        for i, (k, tlist) in enumerate(self._blocked_by_event):

            if k == event_key:

                woken = tlist

                self._blocked_by_event.pop(i)

                break

        for task in woken:

            task.state = TaskState.READY

            self._ready.append(task)



    def pending_task_count(self) -> int:

        return len(self._ready) + sum(len(w) for _, w in self._blocked_by_event) + sum(len(w) for _, w in self.irq_waiters)



    def run_until_idle(self) -> None:

        """Runs one full round-robin sweep, then fires idle hooks once READY queue drains."""

        self.drain_interrupts()



        while self._ready:

            task = self._ready.popleft()

            self.current_task = task

            task.state = TaskState.RUNNING



            if task.coro is None:

                task.state = TaskState.READY

                self._ready.append(task)

                continue



            try:

                wait_on = next(task.coro)

            except StopIteration:

                task.state = TaskState.TERMINATED

                self.current_task = None

                continue



            if wait_on is None:

                task.state = TaskState.READY

                self._ready.append(task)

            elif isinstance(wait_on, tuple) and wait_on[0] == "BLOCK":

                pass

            else:

                task.state = TaskState.BLOCKED

                found = False

                for k, tlist in self._blocked_by_event:

                    if k == wait_on:

                        tlist.append(task)

                        found = True

                        break

                if not found:

                    self._blocked_by_event.append((wait_on, [task]))

            self.current_task = None



        for hook in self.idle_hooks:

            hook()



    def run_to_completion(self, max_sweeps: int = 1000) -> None:

        for _ in range(max_sweeps):

            self.run_until_idle()

            if not self._ready and not self._blocked_by_event and not self.irq_waiters:

                return

        raise RuntimeError(

            f"scheduler did not reach idle within {max_sweeps} sweeps "

            "(a task is stuck BLOCKED on an event nobody notifies)"

        )
