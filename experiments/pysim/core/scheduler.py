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
from enum import Enum, auto
from typing import Any

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


class ChannelAction(str, Enum):
    """Action returned by channel_send, channel_recv, and channel_select_recv."""

    BLOCK = "BLOCK"
    DIRECT_SWITCH = "DIRECT_SWITCH"
    YIELD = "YIELD"


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
    """Bufferless synchronous CSP rendezvous channel (ADR_RendezvousChannel)."""

    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.waiter_task: Task | None = None
        self.waiter_dir: WaitDir = WaitDir.NONE
        # Set only while waiter_task is a receiver waiting via
        # channel_select_recv(); None for a plain single-channel wait.
        self.waiter_group: SelectGroup | None = None


class Task:
    def __init__(self, task_id: int | str, name: str, coro: Generator[Any, None, None] | None):
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
    ):
        self.max_tasks = max_tasks
        self.max_handoffs = max_handoffs
        self.consecutive_handoffs = 0
        self._ready: deque[Task] = deque()
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

    def spawn(
        self,
        name: str,
        coro: Generator[Any, None, None] | None = None,
        task_id: int | str | None = None,
    ) -> int | str:
        """Spawn a new task within FB_CONF_MAX_TASKS bounds."""
        if len(self._all) >= self.max_tasks:
            raise RuntimeError(f"Task capacity exceeded (max {self.max_tasks})")
        if task_id is not None:
            assigned_id = task_id
            if self.get_task(assigned_id) is not None:
                raise ValueError(f"Task with ID {assigned_id} already exists")
        else:
            # Skip past any ID already taken (explicit or auto) without ever
            # inspecting what type that ID is: no isinstance/type() on a
            # primitive, matching the target build's RTTI-disabled C++.
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

    def channel_send(self, channel_id: str, data: Any) -> tuple[ChannelAction, Any]:
        """Synchronous CSP send with atomic ownership handoff."""
        ch = self.get_channel(channel_id)
        if ch is None:
            ch = self.create_channel(channel_id)

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

    def channel_recv(self, channel_id: str) -> tuple[ChannelAction, Any]:
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
        assert ch.waiter_dir != WaitDir.RECV, (
            "one waiter per channel: concurrent receivers must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir, ch.waiter_group = receiver, WaitDir.RECV, None
        receiver.state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def channel_select_recv(self, channel_ids: list[str]) -> tuple[ChannelAction, Any]:
        """
        Guarded external choice (receive-only select, {ADR_RendezvousChannel}):
        waits on whichever of `channel_ids` gets a matching sender first. If
        a sender is already waiting on any of them, completes immediately
        with that one (first match in the given order -- deterministic,
        since at most one sender can be legitimately waiting per channel at
        a time). Otherwise registers this task as the receiver-waiter on
        every one of them at once; whichever channel's channel_send()
        arrives first completes the rendezvous and clears this task's
        registration from the rest (see channel_send()'s SelectGroup
        cleanup), preserving the one-waiter-per-channel invariant.
        """
        receiver = self.current_task
        assert receiver is not None, "channel_select_recv requires active running task"
        channels = [self.get_channel(cid) or self.create_channel(cid) for cid in channel_ids]

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
        self.consecutive_handoffs = 0
        return (ChannelAction.YIELD, None)

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

    def pending_task_count(self) -> int:
        return len(self._ready) + sum(len(w) for _, w in self.irq_waiters)

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
            except StopIteration as e:
                task.result = e.value
                task.state = TaskState.TERMINATED
                self.current_task = None
                continue
            if wait_on is None:
                task.state = TaskState.READY
                self._ready.append(task)
            # else: a ("BLOCK", None) CSP wait -- channel_send()/channel_recv()
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
