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

from collections import deque
from collections.abc import Callable, Generator
from enum import IntEnum
from typing import Any

from logger import (
    LOG_EVT_COOS_DUPLICATE_TASK,
    LOG_EVT_COOS_HANDOFF_LIMIT,
    LOG_EVT_COOS_IRQ_OVERFLOW,
    LOG_EVT_COOS_TASK_CAPACITY,
    LogLevel,
)

FB_CONF_MAX_TASKS = 16
FB_CONF_MAX_CONSECUTIVE_HANDOFFS = 4
FB_CONF_INTERRUPT_QUEUE_SIZE = 16


class TaskState(IntEnum):
    READY = 1
    RUNNING = 2
    BLOCKED = 3
    SUSPENDED_CSP = 4
    TERMINATED = 5


class WaitDir(IntEnum):
    NONE = 0
    SEND = 1
    RECV = 2


class ChannelAction(IntEnum):
    """Action returned by channel_send, channel_recv, and channel_select_recv."""

    BLOCK = 1
    DIRECT_SWITCH = 2
    YIELD = 3


class SelectGroup:
    """
    Tracks one receiver's pending guarded external choice (select) across
    several channels at once (ADR_RendezvousChannel): at most one of
    `channels` can ever resolve the wait, after which the receiver's
    registration is cleared from every other member.
    """

    __slots__ = ("channels",)

    def __init__(self, channels: "list[Channel]"):
        self.channels = channels


class Channel:
    """Bufferless synchronous CSP rendezvous channel (ADR_RendezvousChannel).

    Fields defined in os_coos.md 3.3:
    - waiter_task: Task | None (single waiting task or None)
    - waiter_dir: WaitDir (NONE, SEND, RECV)
    """

    def __init__(self, scheduler: "Scheduler | None" = None):
        self.scheduler = scheduler
        self.waiter_task: Task | None = None
        self.waiter_dir: WaitDir = WaitDir.NONE
        # Set only while waiter_task is a receiver waiting via
        # channel_select_recv(). When this channel completes the wait, it
        # walks group.channels to clear waiter_task from the non-winning
        # channels, preserving the one-waiter-per-channel invariant.
        self.waiter_group: SelectGroup | None = None

    def send(self, data: Any) -> tuple[ChannelAction, Any]:
        """Synchronous CSP send on this channel."""
        assert self.scheduler is not None, "Channel not attached to a scheduler"
        return self.scheduler.channel_send(self, data)

    def recv(self) -> tuple[ChannelAction, Any]:
        """Synchronous CSP recv on this channel."""
        assert self.scheduler is not None, "Channel not attached to a scheduler"
        return self.scheduler.channel_recv(self)


class Task:
    """A single coroutine-based task with explicit cooperative lifecycle state."""

    def __init__(
        self,
        task_id: int,
        name: str,
        coro: Generator[Any, None, None] | None = None,
    ):
        self.task_id = task_id
        self.name = name
        self.coro = coro
        self.state = TaskState.READY
        self.pending_val: Any = None
        self.received_val: Any = None
        self.result: Any = None


class Scheduler:
    def __init__(
        self,
        max_tasks: int = FB_CONF_MAX_TASKS,
        max_handoffs: int = FB_CONF_MAX_CONSECUTIVE_HANDOFFS,
        logger: Any = None,
    ):
        self.max_tasks = max_tasks
        self.max_handoffs = max_handoffs
        self.logger = logger
        self.consecutive_handoffs = 0
        self._ready: deque[Task] = deque()
        self._all: list[Task] = []
        self.current_task: Task | None = None
        self._next_id = 1
        self.idle_hooks: list[Callable[[], None]] = []
        self.interrupt_event_queue: deque[int] = deque(maxlen=FB_CONF_INTERRUPT_QUEUE_SIZE)
        self.irq_waiters: list[tuple[int, list[Task]]] = []
        self.dropped_irqs = 0

    def get_task(self, task_id: int) -> Task | None:
        for t in self._all:
            if t.task_id == task_id:
                return t
        return None

    def spawn(
        self,
        name: str,
        coro: Generator[Any, None, None] | None = None,
        task_id: int | None = None,
    ) -> int:
        """Spawn a new task within FB_CONF_MAX_TASKS bounds."""
        if len(self._all) >= self.max_tasks:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.ERROR,
                    LOG_EVT_COOS_TASK_CAPACITY,
                    self.max_tasks,
                    len(self._all) + 1,
                    0,
                    0,
                )
            raise RuntimeError(f"Task capacity exceeded (max {self.max_tasks})")
        if task_id is not None:
            assigned_id = task_id
            if self.get_task(assigned_id) is not None:
                if self.logger is not None:
                    self.logger.log_event(
                        LogLevel.ERROR,
                        LOG_EVT_COOS_DUPLICATE_TASK,
                        assigned_id,
                        0,
                        0,
                        0,
                    )
                raise ValueError(f"Task with ID {assigned_id} already exists")
        else:
            while self.get_task(self._next_id) is not None:
                self._next_id += 1
            assigned_id = self._next_id
            self._next_id += 1

        task = Task(assigned_id, name, coro)
        self._all.append(task)
        self._ready.append(task)
        return task.task_id

    def detach(self, task: Task) -> None:
        """
        Removes a task from the READY queue so it will never be picked up by
                run_until_idle(). For a task with no coroutine (or one driven
                directly by a caller rather than the scheduler loop, e.g. a
                fireball_call bridge), leaving it in READY is a bug: a coro=None
                task re-appends itself every sweep (see run_until_idle), spinning
                forever instead of ever going idle.
        """
        try:
            self._ready.remove(task)
        except ValueError:
            pass

    def attach(self, task: Task) -> None:
        """
        Puts a task in the READY queue (READY state, not already present) so
                run_until_idle() will pick it up and drive `task.coro` -- the
                counterpart of detach(). Used when a caller (e.g. a fireball_call
                bridge) hands a task a fresh coroutine to run as its own action.
        """
        task.state = TaskState.READY
        if task not in self._ready:
            self._ready.append(task)

    def create_channel(self) -> Channel:
        """
        Creates an unbuffered synchronous CSP rendezvous channel (ADR_RendezvousChannel).
        Call channel.send(data) or channel.recv() directly on the returned Channel.
        """
        return Channel(scheduler=self)

    def channel_send(self, channel: Channel, data: Any) -> tuple[ChannelAction, Any]:
        """Synchronous CSP send with atomic ownership handoff directly on Channel."""
        ch = channel
        sender = self.current_task
        assert sender is not None, "channel_send requires active running task"
        if ch.waiter_dir == WaitDir.RECV:
            receiver = ch.waiter_task
            assert receiver is not None
            group = ch.waiter_group
            ch.waiter_task, ch.waiter_dir, ch.waiter_group = None, WaitDir.NONE, None
            if group is not None:
                # This receiver was select()-waiting on several channels;
                # this one won, so clear its registration from the rest.
                for other in group.channels:
                    if other is not ch and other.waiter_task is receiver:
                        other.waiter_task, other.waiter_dir, other.waiter_group = (
                            None,
                            WaitDir.NONE,
                            None,
                        )
            receiver.received_val = data
            receiver.state = TaskState.READY
            sender.state = TaskState.READY
            return self._handoff_or_yield(receiver)
        assert ch.waiter_dir != WaitDir.SEND, (
            "one waiter per channel: concurrent senders must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir = sender, WaitDir.SEND
        sender.pending_val = data
        sender.state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def channel_recv(self, channel: Channel) -> tuple[ChannelAction, Any]:
        """Synchronous CSP recv with atomic ownership handoff directly on Channel."""
        ch = channel
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
        assert ch.waiter_dir != WaitDir.RECV, (
            "one waiter per channel: concurrent receivers must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir, ch.waiter_group = receiver, WaitDir.RECV, None
        receiver.state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def channel_select_recv(self, channels: list[Channel]) -> tuple[ChannelAction, Any]:
        """
        Guarded external choice (receive-only select, {ADR_RendezvousChannel}):
        waits on whichever of `channels` gets a matching sender first.
        """
        receiver = self.current_task
        assert receiver is not None, "channel_select_recv requires active running task"

        for ch in channels:
            if ch.waiter_dir == WaitDir.SEND:
                sender = ch.waiter_task
                assert sender is not None
                val = sender.pending_val
                sender.pending_val = None
                ch.waiter_task, ch.waiter_dir = None, WaitDir.NONE
                receiver.received_val = val
                sender.state = TaskState.READY
                receiver.state = TaskState.READY
                return self._handoff_or_yield(sender)

        group = SelectGroup(channels)
        for ch in channels:
            assert ch.waiter_dir != WaitDir.RECV, (
                "one waiter per channel: concurrent receivers must use separate channels"
            )
            ch.waiter_task, ch.waiter_dir, ch.waiter_group = receiver, WaitDir.RECV, group
        receiver.state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def _handoff_or_yield(self, target_task: Task) -> tuple[ChannelAction, Any]:
        """CSP direct handoff or scheduler yield upon consecutive threshold."""
        if self.consecutive_handoffs < self.max_handoffs:
            self.consecutive_handoffs += 1
            if target_task in self._ready:
                self._ready.remove(target_task)

            self._ready.appendleft(target_task)
            return (ChannelAction.DIRECT_SWITCH, target_task.task_id)
        if self.logger is not None:
            self.logger.log_event(
                LogLevel.WARN,
                LOG_EVT_COOS_HANDOFF_LIMIT,
                target_task.task_id,
                self.consecutive_handoffs,
                0,
                0,
            )
        self.consecutive_handoffs = 0
        return (ChannelAction.YIELD, None)

    def notify_interrupt(self, irq_id: int) -> bool:
        """Non-blocking ISR notification to event queue."""
        if len(self.interrupt_event_queue) >= FB_CONF_INTERRUPT_QUEUE_SIZE:
            self.dropped_irqs += 1
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.WARN,
                    LOG_EVT_COOS_IRQ_OVERFLOW,
                    irq_id,
                    self.dropped_irqs,
                    0,
                    0,
                )
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

    def pending_task_count(self) -> int:
        return len(self._ready) + sum(len(w) for _, w in self.irq_waiters)

    def step(self) -> Task | None:
        """Executes a single ready task from the front of the queue."""
        self.drain_interrupts()
        if not self._ready:
            return None
        task = self._ready.popleft()
        self.current_task = task
        task.state = TaskState.RUNNING
        if task.coro is None:
            task.state = TaskState.READY
            self._ready.append(task)
            self.current_task = None
            return task
        try:
            wait_on = next(task.coro)
        except StopIteration as e:
            task.result = e.value
            task.state = TaskState.TERMINATED
            self.current_task = None
            return task

        if wait_on is None or wait_on[0] == ChannelAction.YIELD:
            task.state = TaskState.READY
            self._ready.append(task)

        self.current_task = None
        return task

    def run_until_idle(self) -> None:
        """Runs cooperative tasks until all coroutines block, yield or terminate, then fires idle hooks."""
        self.drain_interrupts()
        budget = len(self._ready) * 4 + 16
        while self._ready and budget > 0:
            budget -= 1
            if all(t.coro is None for t in self._ready):
                break
            task = self._ready.popleft()
            self.current_task = task
            task.state = TaskState.RUNNING
            if task.coro is None:
                task.state = TaskState.READY
                self._ready.append(task)
                self.current_task = None
                continue
            try:
                wait_on = next(task.coro)
            except StopIteration as e:
                task.result = e.value
                task.state = TaskState.TERMINATED
                self.current_task = None
                continue
            if wait_on is None:
                task.state = TaskState.READY
                self._ready.append(task)
            elif wait_on[0] == ChannelAction.YIELD:
                task.state = TaskState.READY
                self._ready.append(task)
                if all(t.coro is None for t in self._ready):
                    break
            # else: a (ChannelAction.BLOCK, None) CSP wait -- channel_send()/channel_recv()
            # already parked the task (TaskState.SUSPENDED_CSP) and record who
            # will wake it; there is nothing left for this loop to do.

            self.current_task = None

        for hook in self.idle_hooks:
            hook()

    def run_to_completion(self, max_sweeps: int = 1000) -> None:
        for _ in range(max_sweeps):
            self.run_until_idle()
            if not self._ready and not self.irq_waiters:
                return
        raise RuntimeError(
            f"scheduler did not reach idle within {max_sweeps} sweeps "
            "(a task is stuck BLOCKED on an event nobody notifies)"
        )
