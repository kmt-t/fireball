"""
experiments/pysim/tier1_core/scheduler.py
Cooperative round-robin scheduler and Hoare CSP rendezvous engine, mirroring
docs/components/tier1_core/os_scheduler.md and docs/components/tier1_core/os_coos.md.

Implementation Invariants & Gotchas:
- GOTCHA-COOS-01: Channel has no internal value buffer (ADR_RendezvousChannel).
  Values stay in sender frame until receiver handoff, eliminating double-ownership.
- GOTCHA-COOS-02: 1-channel-1-waiter constraint triggers assertion on duplicate wait
  direction (no queues, no priority inversion, no dynamic allocation).
- GOTCHA-COOS-03: ISR interrupt notification queue is non-blocking (drain_interrupts
  wakes tasks deterministically at scheduler yield points).
- ADR_InterruptRescheduleGeneration: an accepted interrupt starts one cooperative
  reschedule generation. Each task in the fixed round snapshot observes it once.
- GOTCHA-SCHED-01: Consecutive direct handoff bound (FB_CONF_MAX_CONSECUTIVE_HANDOFFS)
  returns control to the scheduler after the limit; it does not guarantee task fairness
  or real-time response bounds.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from enum import IntEnum
from typing import Protocol, cast

from interrupt_event import InterruptEvent
from system_containers import RingBuffer, StaticVector

FB_CONF_MAX_TASKS = 16
FB_CONF_MAX_CHANNELS = FB_CONF_MAX_TASKS * 4
FB_CONF_MAX_CONSECUTIVE_HANDOFFS = 4
FB_CONF_INTERRUPT_QUEUE_SIZE = 16
FB_CONF_MAX_IDLE_HOOKS = 8

# Tier 1 owns the scheduler diagnostics identifiers. The Tier 2 logger can
# consume them, but COOS must remain independent of the runtime implementation.
LOG_EVT_COOS_HANDOFF_LIMIT = 0x0101
LOG_EVT_COOS_TASK_CAPACITY = 0x0102
LOG_EVT_COOS_DUPLICATE_TASK = 0x0103
LOG_EVT_COOS_IRQ_OVERFLOW = 0x0104


class SchedulerLogLevel(IntEnum):
    WARN = 2
    ERROR = 3


class _SchedulerLogger(Protocol):
    def log_event(
        self,
        level: IntEnum,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> str: ...


class ChannelPayload(Protocol):
    """Opaque rendezvous payload; ownership is transferred by the caller."""

    pass


class MovableChannelPayload(ChannelPayload, Protocol):
    """Payload whose channel performs an explicit move-only handoff."""

    def move_to(self, new_owner: int) -> ChannelPayload | None: ...


class ChannelTransferMode(IntEnum):
    """Selects the payload contract enforced by a rendezvous channel."""

    BORROWED = 0
    MOVABLE = 1


class BoundedReadyQueue:
    """Fixed-capacity intrusive circular FIFO for READY tasks ({ADR_IntrusiveTcbList})."""

    __slots__ = ("_head", "_size", "capacity")

    def __init__(self, capacity: int = FB_CONF_MAX_TASKS):
        assert capacity > 0, "READY queue capacity must be positive"
        self.capacity = capacity
        self._head: Task | None = None
        self._size = 0

    def enqueue(self, task: Task) -> bool:
        if self._size >= self.capacity:
            return False
        assert task.ready_prev is None and task.ready_next is None, (
            "task is already linked into a READY queue"
        )
        if self._head is None:
            self._link_as_only_task(task)
        else:
            self._link_before(self._head, task)
        self._size += 1
        return True

    def enqueue_front(self, task: Task) -> bool:
        if self._size >= self.capacity:
            return False
        assert task.ready_prev is None and task.ready_next is None, (
            "task is already linked into a READY queue"
        )
        if self._head is None:
            self._link_as_only_task(task)
        else:
            self._link_before(self._head, task)
            self._head = task
        self._size += 1
        return True

    def _link_as_only_task(self, task: Task) -> None:
        task.ready_prev = task
        task.ready_next = task
        self._head = task

    @staticmethod
    def _link_before(reference: Task, task: Task) -> None:
        previous = reference.ready_prev
        assert previous is not None, "READY ring is missing its previous link"
        task.ready_prev = previous
        task.ready_next = reference
        previous.ready_next = task
        reference.ready_prev = task

    def dequeue(self) -> Task:
        task = self._head
        assert task is not None, "pop from an empty ready queue"
        following = task.ready_next
        previous = task.ready_prev
        assert following is not None and previous is not None, "READY ring is missing a task link"
        if self._size == 1:
            self._head = None
        else:
            following.ready_prev = previous
            previous.ready_next = following
            self._head = following
        task.ready_prev = None
        task.ready_next = None
        self._size -= 1
        return task

    def remove(self, task: Task) -> bool:
        previous = task.ready_prev
        following = task.ready_next
        if previous is None or following is None:
            return False
        if self._size == 1:
            assert self._head is task, "task is not in this READY queue"
            self._head = None
        else:
            previous.ready_next = following
            following.ready_prev = previous
            if self._head is task:
                self._head = following
        task.ready_prev = None
        task.ready_next = None
        self._size -= 1
        return True

    def clear(self) -> None:
        while self._head is not None:
            self.dequeue()

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def contains(self, task: Task) -> bool:
        return task.ready_prev is not None and task.ready_next is not None

    def __contains__(self, task: Task) -> bool:
        return self.contains(task)

    def __iter__(self) -> Iterator[Task]:
        task = self._head
        for _ in range(self._size):
            assert task is not None, "READY ring ended before its recorded size"
            yield task
            task = task.ready_next

    def __getitem__(self, index: int) -> Task:
        if index < 0:
            index += self._size
        assert 0 <= index < self._size, "READY queue index out of range"
        task = self._head
        for _ in range(index):
            assert task is not None, "READY ring ended before its recorded size"
            task = task.ready_next
        assert task is not None, "READY ring ended before its recorded size"
        return task


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

    __slots__ = (
        "scheduler",
        "transfer_mode",
        "waiter_dir",
        "waiter_group",
        "waiter_task",
    )

    def __init__(
        self,
        scheduler: "Scheduler | None" = None,
        transfer_mode: ChannelTransferMode = ChannelTransferMode.BORROWED,
    ):
        self.scheduler = scheduler
        self.transfer_mode = transfer_mode
        self.waiter_task: Task | None = None
        self.waiter_dir: WaitDir = WaitDir.NONE
        # Set only while waiter_task is a receiver waiting via
        # channel_select_recv(). When this channel completes the wait, it
        # walks group.channels to clear waiter_task from the non-winning
        # channels, preserving the one-waiter-per-channel invariant.
        self.waiter_group: SelectGroup | None = None

    def send(self, data: ChannelPayload) -> tuple[ChannelAction, ChannelPayload | None]:
        """Synchronous CSP send on this channel."""
        assert self.scheduler is not None, "Channel not attached to a scheduler"
        return self.scheduler.channel_send(self, data)

    def recv(self) -> tuple[ChannelAction, ChannelPayload | None]:
        """Synchronous CSP recv on this channel."""
        assert self.scheduler is not None, "Channel not attached to a scheduler"
        return self.scheduler.channel_recv(self)


class Task:
    """A single coroutine-based task with explicit cooperative lifecycle state."""

    __slots__ = (
        "coro",
        "name",
        "pending_val",
        "ready_next",
        "ready_prev",
        "received_val",
        "result",
        "role",
        "state",
        "task_id",
        "pending_interrupt_event",
        "last_seen_generation",
        "waiting_irq",
    )

    def __init__(
        self,
        task_id: int,
        name: str,
        coro: Generator[ChannelPayload, None, None] | None = None,
        role: int = 0,
    ):
        self.task_id = task_id
        self.name = name
        self.coro = coro
        self.role = role
        self.state = TaskState.READY
        self.ready_prev: Task | None = None
        self.ready_next: Task | None = None
        self.pending_val: ChannelPayload | None = None
        self.received_val: ChannelPayload | None = None
        self.result: ChannelPayload | None = None
        self.pending_interrupt_event: InterruptEvent | None = None
        self.last_seen_generation = 0
        self.waiting_irq: int | None = None


class Scheduler:
    __slots__ = (
        "_all",
        "_channels",
        "_next_id",
        "_ready",
        "_ready_coro_count",
        "consecutive_handoffs",
        "current_task",
        "dropped_irqs",
        "idle_hooks",
        "interrupt_event_queue",
        "logger",
        "max_handoffs",
        "max_tasks",
        "reschedule_generation",
        "reschedule_pending",
        "round_target_generation",
        "round_target_mask",
    )

    def __init__(
        self,
        max_tasks: int = FB_CONF_MAX_TASKS,
        max_handoffs: int = FB_CONF_MAX_CONSECUTIVE_HANDOFFS,
        logger: _SchedulerLogger | None = None,
    ):
        self.max_tasks = max_tasks
        self.max_handoffs = max_handoffs
        self.logger = logger
        self.consecutive_handoffs = 0
        self._ready: BoundedReadyQueue = BoundedReadyQueue(capacity=self.max_tasks)
        self._all: StaticVector[Task] = StaticVector(capacity=self.max_tasks)
        self._channels: StaticVector[Channel] = StaticVector(capacity=FB_CONF_MAX_CHANNELS)
        self.current_task: Task | None = None
        self._next_id = 1
        self.idle_hooks: StaticVector[Callable[[], None]] = StaticVector(
            capacity=FB_CONF_MAX_IDLE_HOOKS
        )
        self.interrupt_event_queue: RingBuffer[InterruptEvent] = RingBuffer(
            capacity=FB_CONF_INTERRUPT_QUEUE_SIZE
        )
        self.dropped_irqs = 0
        self._ready_coro_count = 0
        self.reschedule_generation = 0
        self.reschedule_pending = False
        self.round_target_generation = 0
        self.round_target_mask = 0

    @staticmethod
    def _task_bit(task: Task) -> int:
        """Return the fixed-mask bit assigned to a registered task identity."""
        assert task.task_id >= 0, "task IDs must be non-negative for round masks"
        return 1 << task.task_id

    def _remove_round_target(self, task: Task) -> None:
        """Remove a task that blocked or terminated before observing the round."""
        self.round_target_mask &= ~self._task_bit(task)

    def _begin_reschedule_round(self) -> None:
        """Snapshot RUNNING/READY tasks exactly once for the pending generation."""
        if not self.reschedule_pending:
            return
        if self.round_target_generation == self.reschedule_generation:
            return
        target_mask = 0
        for task in self._all:
            if (
                (task.state == TaskState.RUNNING or task.state == TaskState.READY)
                and task.last_seen_generation != self.reschedule_generation
            ):
                target_mask |= self._task_bit(task)
        self.round_target_mask = target_mask
        self.round_target_generation = self.reschedule_generation
        self._complete_reschedule_if_ready()

    def _complete_reschedule_if_ready(self) -> None:
        """Clear a generation only after its snapshot and FIFO are both complete."""
        if not self.reschedule_pending:
            return
        if self.round_target_generation != self.reschedule_generation:
            return
        if self.round_target_mask != 0 or len(self.interrupt_event_queue) != 0:
            return
        self.reschedule_pending = False
        self.round_target_generation = 0

    def observe_reschedule_generation(self, task: Task | None = None) -> bool:
        """Observe the current generation at a cooperative execution boundary.

        Returns ``True`` exactly once per target task and generation. The caller
        must turn that result into its normal cooperative yield; this method does
        not perform a context switch itself.
        """
        observed_task = task if task is not None else self.current_task
        assert observed_task is not None, "generation observation requires an active task"
        assert self.get_task(observed_task.task_id) is observed_task, (
            "generation observation requires a registered task"
        )
        self._begin_reschedule_round()
        if not self.reschedule_pending:
            return False
        bit = self._task_bit(observed_task)
        if self.round_target_mask & bit == 0:
            return False
        self.round_target_mask &= ~bit
        if observed_task.last_seen_generation == self.reschedule_generation:
            self._complete_reschedule_if_ready()
            return False
        observed_task.last_seen_generation = self.reschedule_generation
        self._complete_reschedule_if_ready()
        return True

    def get_task(self, task_id: int) -> Task | None:
        for t in self._all:
            if t.task_id == task_id:
                return t
        return None

    @property
    def current_task_id(self) -> int:
        """Returns the authenticated identity of the task currently running."""
        assert self.current_task is not None, "No task is currently running"
        return self.current_task.task_id

    def activate_task(self, task: Task) -> None:
        """Activates a scheduler-registered task for a synchronous host entrypoint."""
        assert self.get_task(task.task_id) is task, "Task must be registered with this scheduler"
        assert self.current_task is None, "A scheduler task is already active"
        self.current_task = task

    def require_active_task(self, task: Task) -> None:
        """Checks the scheduler-selected task without permitting replacement."""
        assert self.get_task(task.task_id) is task, "Task must be registered with this scheduler"
        assert self.current_task is task, "The requested task is not scheduler-selected"

    @contextmanager
    def task_context(self, task: Task) -> Iterator[None]:
        """Temporarily runs scheduler-owned work under a registered task identity."""
        assert self.get_task(task.task_id) is task, "Task must be registered with this scheduler"
        previous = self.current_task
        self.current_task = task
        try:
            yield
        finally:
            self.current_task = previous

    def spawn(
        self,
        name: str,
        coro: Generator[ChannelPayload, None, None] | None = None,
        task_id: int | None = None,
        role: int = 0,
    ) -> int:
        """Spawn a new task within FB_CONF_MAX_TASKS bounds."""
        if len(self._all) >= self.max_tasks:
            if self.logger is not None:
                self.logger.log_event(
                    SchedulerLogLevel.ERROR,
                    LOG_EVT_COOS_TASK_CAPACITY,
                    self.max_tasks,
                    len(self._all) + 1,
                    0,
                    0,
                )
            assert False, f"Task capacity exceeded (max {self.max_tasks})"
        if task_id is not None:
            assigned_id = task_id
            if self.get_task(assigned_id) is not None:
                if self.logger is not None:
                    self.logger.log_event(
                        SchedulerLogLevel.ERROR,
                        LOG_EVT_COOS_DUPLICATE_TASK,
                        assigned_id,
                        0,
                        0,
                        0,
                    )
                assert False, f"Task with ID {assigned_id} already exists"
        else:
            while self.get_task(self._next_id) is not None:
                self._next_id += 1
            assigned_id = self._next_id
            self._next_id += 1

        task = Task(assigned_id, name, coro, role=role)
        # A task created during an active generation belongs to the next round.
        task.last_seen_generation = self.reschedule_generation
        self._all.push_back(task)
        self._ready.enqueue(task)
        if coro is not None:
            self._ready_coro_count += 1
        return task.task_id

    def detach(self, task: Task) -> None:
        """
        Removes a task from the READY queue so it will never be picked up by
        run_until_idle().
        """
        if self._ready.contains(task):
            if task.coro is not None:
                self._ready_coro_count -= 1
            self._ready.remove(task)

    def attach(self, task: Task) -> None:
        """
        Puts an external/detached task back on the READY queue.
        """
        if not self._ready.contains(task):
            self._ready.enqueue(task)
            if task.coro is not None:
                self._ready_coro_count += 1

    def create_channel(
        self,
        transfer_mode: ChannelTransferMode = ChannelTransferMode.BORROWED,
    ) -> Channel:
        """
        Creates an unbuffered synchronous CSP rendezvous channel (ADR_RendezvousChannel).
        Call channel.send(data) or channel.recv() directly on the returned Channel.
        """
        channel = Channel(scheduler=self, transfer_mode=transfer_mode)
        channel_added = self._channels.push_back(channel)
        assert channel_added, (
            f"Channel capacity exceeded (max {FB_CONF_MAX_CHANNELS})"
        )
        return channel

    def task_killed(self, task_id: int) -> bool:
        """Externally terminate a blocked task and remove every wait registration."""

        task = self.get_task(task_id)
        if task is None or task.state == TaskState.TERMINATED:
            return False
        assert task is not self.current_task, "a running task cannot be killed externally"
        assert task.state == TaskState.BLOCKED or task.state == TaskState.SUSPENDED_CSP, (
            "task_killed requires a blocked task"
        )

        self.detach(task)
        task.waiting_irq = None
        task.pending_interrupt_event = None
        self._remove_round_target(task)
        for channel in self._channels:
            if channel.waiter_task is not task:
                continue
            group = channel.waiter_group
            channel.waiter_task = None
            channel.waiter_dir = WaitDir.NONE
            channel.waiter_group = None
            if group is not None:
                for other in group.channels:
                    if other.waiter_task is task:
                        other.waiter_task = None
                        other.waiter_dir = WaitDir.NONE
                        other.waiter_group = None
            task.pending_val = None
            break

        task.received_val = None
        if task.coro is not None:
            task.coro.close()
            task.coro = None
        task.result = None
        task.state = TaskState.TERMINATED
        self._complete_reschedule_if_ready()
        return True

    def channel_send(
        self, channel: Channel, data: ChannelPayload
    ) -> tuple[ChannelAction, ChannelPayload | None]:
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
            if ch.transfer_mode == ChannelTransferMode.MOVABLE:
                movable = cast(MovableChannelPayload, data)
                val = movable.move_to(receiver.task_id)
            else:
                val = data
            receiver.received_val = val
            receiver.state = TaskState.READY
            sender.state = TaskState.READY
            return self._handoff_or_yield(receiver)
        assert ch.waiter_dir != WaitDir.SEND, (
            "one waiter per channel: concurrent senders must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir = sender, WaitDir.SEND
        sender.pending_val = data
        sender.state = TaskState.SUSPENDED_CSP
        self._remove_round_target(sender)
        self._complete_reschedule_if_ready()
        return (ChannelAction.BLOCK, None)

    def channel_recv(self, channel: Channel) -> tuple[ChannelAction, ChannelPayload | None]:
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
            assert val is not None
            if ch.transfer_mode == ChannelTransferMode.MOVABLE:
                movable = cast(MovableChannelPayload, val)
                val = movable.move_to(receiver.task_id)
            receiver.received_val = val
            sender.state = TaskState.READY
            receiver.state = TaskState.READY
            return self._handoff_or_yield(sender)
        assert ch.waiter_dir != WaitDir.RECV, (
            "one waiter per channel: concurrent receivers must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir, ch.waiter_group = receiver, WaitDir.RECV, None
        receiver.state = TaskState.SUSPENDED_CSP
        self._remove_round_target(receiver)
        self._complete_reschedule_if_ready()
        return (ChannelAction.BLOCK, None)

    def channel_select_recv(
        self, channels: Sequence[Channel]
    ) -> tuple[ChannelAction, ChannelPayload | None]:
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
                assert val is not None
                if ch.transfer_mode == ChannelTransferMode.MOVABLE:
                    movable = cast(MovableChannelPayload, val)
                    val = movable.move_to(receiver.task_id)
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
        self._remove_round_target(receiver)
        self._complete_reschedule_if_ready()
        return (ChannelAction.BLOCK, None)

    def _handoff_or_yield(self, target_task: Task) -> tuple[ChannelAction, ChannelPayload | None]:
        """CSP direct handoff or scheduler yield upon consecutive threshold."""
        if self.consecutive_handoffs < self.max_handoffs:
            if self.reschedule_pending:
                # The rendezvous itself remains atomic, but a pending generation
                # ends the direct-handoff chain at this safe boundary.
                assert self._ready.enqueue(target_task), "READY queue capacity exceeded"
                if target_task.coro is not None:
                    self._ready_coro_count += 1
                self.consecutive_handoffs = 0
                return (ChannelAction.YIELD, None)
            self.consecutive_handoffs += 1
            assert self._ready.enqueue_front(target_task), "READY queue capacity exceeded"
            if target_task.coro is not None:
                self._ready_coro_count += 1
            return (ChannelAction.DIRECT_SWITCH, target_task.task_id)
        if self.logger is not None:
            self.logger.log_event(
                SchedulerLogLevel.WARN,
                LOG_EVT_COOS_HANDOFF_LIMIT,
                target_task.task_id,
                self.consecutive_handoffs,
                0,
                0,
            )
        self.consecutive_handoffs = 0
        assert self._ready.enqueue(target_task), "READY queue capacity exceeded"
        if target_task.coro is not None:
            self._ready_coro_count += 1
        return (ChannelAction.YIELD, None)

    def notify_interrupt(self, event: InterruptEvent) -> bool:
        """Non-blocking ISR notification of a fixed five-word event."""
        if len(self.interrupt_event_queue) >= FB_CONF_INTERRUPT_QUEUE_SIZE:
            self.dropped_irqs += 1
            if self.logger is not None:
                self.logger.log_event(
                    SchedulerLogLevel.WARN,
                    LOG_EVT_COOS_IRQ_OVERFLOW,
                    event.vector_id,
                    self.dropped_irqs,
                    0,
                    0,
                )
            return False
        self.interrupt_event_queue.push(event)
        if not self.reschedule_pending:
            self.reschedule_generation += 1
            self.reschedule_pending = True
        return True

    def drain_interrupts(self) -> int:
        """Drain IRQ queue, hand off one event, and wake its registered task."""
        count = 0
        while len(self.interrupt_event_queue) > 0:
            event = self.interrupt_event_queue.pop()
            if event is None:
                break
            count += 1
            delivered = False
            for task in self._all:
                if task.waiting_irq == event.vector_id and task.state == TaskState.BLOCKED:
                    assert task.pending_interrupt_event is None, (
                        "a blocked interrupt waiter cannot already own an event"
                    )
                    task.pending_interrupt_event = event
                    task.waiting_irq = None
                    task.state = TaskState.READY
                    assert self._ready.enqueue(task), "READY queue capacity exceeded"
                    if task.coro is not None:
                        self._ready_coro_count += 1
                    delivered = True
                    break
            if not delivered:
                self.dropped_irqs += 1
        self._complete_reschedule_if_ready()
        return count

    def wait_for_interrupt(self, irq_id: int) -> None:
        task = self.current_task
        assert task is not None
        assert task.pending_interrupt_event is None, (
            "interrupt event must be consumed before waiting again"
        )
        task.state = TaskState.BLOCKED
        task.waiting_irq = irq_id
        self._remove_round_target(task)
        self._complete_reschedule_if_ready()

    def consume_interrupt_event(self) -> InterruptEvent | None:
        """Consume the event handed to the current vSoC runtime task."""
        task = self.current_task
        assert task is not None, "consume_interrupt_event requires an active task"
        event = task.pending_interrupt_event
        task.pending_interrupt_event = None
        return event

    def set_idle_hook(self, fn: Callable[[], None]) -> None:
        hook_added = self.idle_hooks.push_back(fn)
        assert hook_added, (
            f"Idle hooks capacity exceeded (max {FB_CONF_MAX_IDLE_HOOKS})"
        )

    def pending_task_count(self) -> int:
        blocked_irq_count = sum(
            1 for t in self._all if t.waiting_irq is not None and t.state == TaskState.BLOCKED
        )
        return len(self._ready) + blocked_irq_count

    def step(self) -> Task | None:
        """Executes a single ready task from the front of the queue."""
        self.drain_interrupts()
        self._begin_reschedule_round()
        if not self._ready:
            return None
        task = self._ready.dequeue()
        if task.coro is not None:
            self._ready_coro_count -= 1
        self.current_task = task
        task.state = TaskState.RUNNING
        if self.observe_reschedule_generation(task):
            task.state = TaskState.READY
            self._ready.enqueue(task)
            if task.coro is not None:
                self._ready_coro_count += 1
            self.current_task = None
            return task
        if task.coro is None:
            task.state = TaskState.READY
            self._ready.enqueue(task)
            self.current_task = None
            return task
        try:
            wait_on = next(task.coro)
        except StopIteration as e:
            task.result = e.value
            task.state = TaskState.TERMINATED
            self._remove_round_target(task)
            self._complete_reschedule_if_ready()
            self.current_task = None
            return task

        if wait_on is None or wait_on[0] == ChannelAction.YIELD:
            task.state = TaskState.READY
            self._ready.enqueue(task)
            if task.coro is not None:
                self._ready_coro_count += 1

        self.current_task = None
        return task

    def run_until_idle(self, budget: int | None = None) -> None:
        """Runs cooperative tasks until all coroutines block, yield or terminate, then fires idle hooks."""
        previous_task = self.current_task
        self.drain_interrupts()
        self._begin_reschedule_round()
        step_budget = budget if budget is not None else max(1000, len(self._ready) * 64 + 16)
        while self._ready and step_budget > 0:
            step_budget -= 1
            if self._ready_coro_count == 0:
                break
            task = self._ready.dequeue()
            if task.coro is not None:
                self._ready_coro_count -= 1
            self.current_task = task
            task.state = TaskState.RUNNING
            if self.observe_reschedule_generation(task):
                task.state = TaskState.READY
                self._ready.enqueue(task)
                if task.coro is not None:
                    self._ready_coro_count += 1
                self.current_task = None
                continue
            if task.coro is None:
                task.state = TaskState.READY
                self._ready.enqueue(task)
                self.current_task = None
                continue
            try:
                wait_on = next(task.coro)
            except StopIteration as e:
                task.result = e.value
                task.state = TaskState.TERMINATED
                self._remove_round_target(task)
                self._complete_reschedule_if_ready()
                self.current_task = None
                continue
            if wait_on is None:
                task.state = TaskState.READY
                self._ready.enqueue(task)
                if task.coro is not None:
                    self._ready_coro_count += 1
            elif wait_on[0] == ChannelAction.YIELD:
                task.state = TaskState.READY
                self._ready.enqueue(task)
                if task.coro is not None:
                    self._ready_coro_count += 1
                if self._ready_coro_count == 0:
                    break
            # else: a (ChannelAction.BLOCK, None) CSP wait -- channel_send()/channel_recv()
            # already parked the task (TaskState.SUSPENDED_CSP) and record who
            # will wake it; there is nothing left for this loop to do.

            self.current_task = None

        for hook in self.idle_hooks:
            hook()
        self.current_task = previous_task

    def run_to_completion(self, max_sweeps: int = 1000) -> None:
        for _ in range(max_sweeps):
            self.run_until_idle()
            has_irq_waiters = any(
                t.waiting_irq is not None and t.state == TaskState.BLOCKED for t in self._all
            )
            if not self._ready and not has_irq_waiters:
                return
        assert False, (
            f"scheduler did not reach idle within {max_sweeps} sweeps "
            "(a task is stuck BLOCKED on an event nobody notifies)"
        )
