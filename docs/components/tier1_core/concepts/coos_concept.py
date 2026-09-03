"""
docs/components/tier1_core/concepts/coos_concept.py
Reference Concept Implementation: COOS (Cooperative OS)
Implementation Invariants & Gotchas:
- COOS-GOTCHA-01: Channel has no internal value slot (ADR_RendezvousChannel). Values stay
  in sender frame until receiver handoff, preventing double-ownership.
- COOS-GOTCHA-02: 1-channel-1-waiter constraint enforces single-waiter per direction;
  concurrent senders or receivers trigger assertion error.
- COOS-GOTCHA-03: ISR interrupt notification queue is non-blocking; task wake-up is
  deferred to cooperative drain_interrupts at scheduler yield points.
- SCHED-GOTCHA-01: Consecutive direct handoff bound forces yield back to main loop to
  prevent starvation of periodic/monitoring tasks.
"""

from collections.abc import Generator
from enum import IntEnum
from typing import Generic, TypeVar

MsgT = TypeVar("MsgT")


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
    BLOCK = 1
    DIRECT_SWITCH = 2
    YIELD = 3


class Channel(Generic[MsgT]):
    """Bufferless synchronous CSP rendezvous channel (ADR_RendezvousChannel)."""

    def __init__(self, kernel: "COOSKernel | None" = None):
        self.kernel = kernel
        self.waiter_task: str | None = None
        self.waiter_dir: int = WaitDir.NONE

    def send(self, data: MsgT) -> tuple[ChannelAction, str | None]:
        assert self.kernel is not None
        return self.kernel.channel_send(self, data)

    def recv(self) -> tuple[ChannelAction, str | None]:
        assert self.kernel is not None
        return self.kernel.channel_recv(self)


class TaskControlBlock:
    """TCB tracking coroutine execution and rendezvous transfer states."""

    __slots__ = ("coro", "id", "pending_val", "received_val", "state")

    def __init__(self, task_id: str, coro: Generator):
        self.id = task_id
        self.coro = coro
        self.state = TaskState.READY
        self.pending_val: object | None = None
        self.received_val: object | None = None


class COOSKernel:
    def __init__(self, max_consecutive_handoffs: int = 4):
        self.tasks: dict[str, TaskControlBlock] = {}
        self.ready_queue: list[str] = []
        self.current_task: str | None = None
        self.channels: dict[str, Channel] = {}
        self.interrupt_event_queue: list[int] = []  # Ring buffer for IRQ IDs
        self.irq_waiters: dict[int, list[str]] = {}  # irq_id -> [task_ids]
        self.max_consecutive_handoffs = max_consecutive_handoffs
        self.consecutive_handoffs = 0
        self.idle_hook_called = False
        self.log_flush_count = 0

    def register_task(self, task_id: str, coroutine: Generator) -> None:
        assert task_id not in self.tasks, f"Task {task_id} already registered"
        self.tasks[task_id] = TaskControlBlock(task_id, coroutine)
        self.ready_queue.append(task_id)

    def create_channel(self) -> Channel:
        return Channel(kernel=self)

    # --- Synchronous Hoare CSP Rendezvous ---
    def channel_send(self, channel: Channel, data: object) -> tuple[ChannelAction, str | None]:
        """Synchronously send value into CSP channel.
        If a receiver is waiting: rendezvous matches immediately, data transfers,
        and scheduler either hands off directly or forces a yield.
        If no receiver is waiting: sender suspends into SUSPENDED_CSP."""
        ch = channel
        sender = self.current_task
        assert sender is not None
        if ch.waiter_dir == WaitDir.RECV:
            receiver = ch.waiter_task
            assert receiver is not None
            ch.waiter_task, ch.waiter_dir = None, WaitDir.NONE
            self.tasks[receiver].received_val = data
            self.tasks[sender].state = TaskState.READY
            self.tasks[receiver].state = TaskState.READY
            return self._handoff_or_yield(receiver)

        # No peer yet: the value stays in the sender's own frame. The channel holds
        # nothing, so there is no buffer to overflow and no send to roll back.
        assert ch.waiter_dir != WaitDir.SEND, (
            "one waiter per channel: concurrent senders must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir = sender, WaitDir.SEND
        self.tasks[sender].pending_val = data
        self.tasks[sender].state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def channel_recv(self, channel: Channel) -> tuple[ChannelAction, str | None]:
        """Receive value from CSP channel."""
        ch = channel
        receiver = self.current_task
        assert receiver is not None
        if ch.waiter_dir == WaitDir.SEND:
            # Rendezvous matched: take the value out of the sender's frame, so that
            # it is never reachable from two owners at once.
            sender = ch.waiter_task
            assert sender is not None
            ch.waiter_task, ch.waiter_dir = None, WaitDir.NONE
            data = self.tasks[sender].pending_val
            self.tasks[sender].pending_val = None
            self.tasks[receiver].received_val = data
            self.tasks[sender].state = TaskState.READY
            self.tasks[receiver].state = TaskState.READY
            return self._handoff_or_yield(sender)
        assert ch.waiter_dir != WaitDir.RECV, (
            "one waiter per channel: concurrent receivers must use separate channels"
        )
        ch.waiter_task, ch.waiter_dir = receiver, WaitDir.RECV
        self.tasks[receiver].state = TaskState.SUSPENDED_CSP
        return (ChannelAction.BLOCK, None)

    def _handoff_or_yield(self, target: str) -> tuple[ChannelAction, str | None]:
        """Bounds the handoff chain so the scheduler main loop stays reachable.
        This bound is exactly what os_coos.md 6.1 'main loop return guarantee'
        proves via AG(at_max_limit -> AF(main_loop))."""
        if self.consecutive_handoffs < self.max_consecutive_handoffs:
            self.consecutive_handoffs += 1
            return (ChannelAction.DIRECT_SWITCH, target)
        self.consecutive_handoffs = 0
        self.ready_queue.append(target)
        return (ChannelAction.YIELD, None)

    def get_received_value(self) -> object:
        task_id = self.current_task
        assert task_id is not None
        val = self.tasks[task_id].received_val
        self.tasks[task_id].received_val = None
        return val

    # --- Interrupt Handling ---
    def notify_interrupt(self, irq_id: int) -> None:
        """Called from ISR context: non-blocking enqueue of IRQ event."""
        self.interrupt_event_queue.append(irq_id)

    def drain_interrupts(self) -> None:
        """Called at yield point: wake up tasks waiting on received IRQs."""
        while self.interrupt_event_queue:
            irq_id = self.interrupt_event_queue.pop(0)
            waiters = self.irq_waiters.pop(irq_id, [])
            for t_id in waiters:
                if self.tasks[t_id].state in (
                    TaskState.BLOCKED,
                    TaskState.SUSPENDED_CSP,
                ):
                    self.tasks[t_id].state = TaskState.READY
                    self.ready_queue.append(t_id)

    def wait_for_interrupt(self, irq_id: int) -> tuple[str, int]:
        task_id = self.current_task
        assert task_id is not None
        self.irq_waiters.setdefault(irq_id, []).append(task_id)
        self.tasks[task_id].state = TaskState.BLOCKED
        return ("BLOCK_IRQ", irq_id)

    # --- Main Dispatcher & Idle Loop ---
    def idle_hook(self) -> None:
        """Executed only when all tasks are blocked and event queue is empty."""
        self.idle_hook_called = True
        self.log_flush_count += 1

    def run_step(self) -> bool:
        """Executes one scheduling step. Returns False when all tasks terminated."""
        self.drain_interrupts()
        # Check for active tasks
        active_tasks = [t for t in self.tasks.values() if t.state != TaskState.TERMINATED]
        if not active_tasks:
            return False
        if not self.ready_queue:
            # All tasks blocked => trigger idle hook
            self.idle_hook()
            return True
        task_id = self.ready_queue.pop(0)
        self.current_task = task_id
        tcb = self.tasks[task_id]
        tcb.state = TaskState.RUNNING
        try:
            action, arg = tcb.coro.send(None)
            if action == ChannelAction.YIELD:
                tcb.state = TaskState.READY
                self.ready_queue.append(task_id)
            elif action == ChannelAction.DIRECT_SWITCH:
                next_task = arg
                tcb.state = TaskState.READY
                # Re-queue current task and immediately switch to target (head of queue)
                self.ready_queue.append(task_id)
                if next_task in self.ready_queue:
                    self.ready_queue.remove(next_task)
                self.ready_queue.insert(0, next_task)
            elif action == ChannelAction.BLOCK or action == "BLOCK_IRQ":
                pass  # Task already in SUSPENDED_CSP or BLOCKED state
        except StopIteration:
            tcb.state = TaskState.TERMINATED

        self.current_task = None
        return True


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_coos_synchronous_rendezvous() -> None:
    kernel = COOSKernel()
    ch = kernel.create_channel()
    received_log = []

    def sender() -> Generator[tuple[ChannelAction, str | None], None, None]:
        # Send first message (42)
        action, arg = ch.send(42)
        yield (action, arg)
        # Send second message (100)
        action, arg = ch.send(100)
        yield (action, arg)

    def receiver() -> Generator[tuple[ChannelAction, str | None], None, None]:
        # Receive first message
        action, arg = ch.recv()
        yield (action, arg)
        val1 = kernel.get_received_value()
        received_log.append(val1)
        # Receive second message
        action, arg = ch.recv()
        yield (action, arg)
        val2 = kernel.get_received_value()
        received_log.append(val2)

    kernel.register_task("receiver", receiver())
    kernel.register_task("sender", sender())
    steps = 0
    while kernel.run_step() and steps < 20:
        steps += 1

    assert received_log == [42, 100], f"Expected [42, 100], got {received_log}"
    assert kernel.tasks["sender"].state == TaskState.TERMINATED
    assert kernel.tasks["receiver"].state == TaskState.TERMINATED


def test_value_has_exactly_one_owner_across_a_rendezvous() -> None:
    """ADR_RendezvousChannel: while a sender waits, the value lives only in the
    sender's frame; after the rendezvous it lives only in the receiver's. It is
    never reachable from the channel, and never from both tasks at once."""
    kernel = COOSKernel()
    ch = kernel.create_channel()

    def sender() -> Generator[tuple[ChannelAction, str | None], None, None]:
        yield ch.send(42)

    def receiver() -> Generator[tuple[ChannelAction, str | None], None, None]:
        yield ch.recv()

    kernel.register_task("sender", sender())
    kernel.register_task("receiver", receiver())
    # Sender runs first and blocks: value is in its own frame, not in the channel.
    kernel.run_step()
    assert kernel.tasks["sender"].state == TaskState.SUSPENDED_CSP
    assert kernel.tasks["sender"].pending_val == 42
    assert not hasattr(ch, "buffer"), "a rendezvous channel must not carry a value slot"
    # Receiver arrives: ownership transfers, and the sender's copy is gone.
    kernel.run_step()
    assert kernel.tasks["receiver"].received_val == 42
    assert kernel.tasks["sender"].pending_val is None, (
        "sender must not retain the value after the rendezvous (double ownership)"
    )


def test_one_waiter_per_channel_is_enforced() -> None:
    """Two senders on one channel is a design violation, not a runtime condition
    to be queued -- the orthogonal table marks it unreachable by construction."""
    kernel = COOSKernel()
    ch = kernel.create_channel()
    kernel.tasks["a"] = TaskControlBlock("a", None)
    kernel.tasks["a"].state = TaskState.RUNNING
    kernel.tasks["b"] = TaskControlBlock("b", None)
    kernel.tasks["b"].state = TaskState.RUNNING
    kernel.current_task = "a"
    ch.send(1)
    kernel.current_task = "b"
    try:
        ch.send(2)
        raise AssertionError("second sender on the same channel must assert")
    except AssertionError as e:
        assert "separate channels" in str(e)


def test_coos_interrupt_wakeup() -> None:
    kernel = COOSKernel()
    irq_received = []

    def irq_task() -> Generator[tuple[ChannelAction, str | None], None, None]:
        action, arg = kernel.wait_for_interrupt(16)
        yield (action, arg)
        irq_received.append("IRQ_16_PROCESSED")

    kernel.register_task("worker", irq_task())
    # Step 1: Worker blocks on IRQ 16
    kernel.run_step()
    assert kernel.tasks["worker"].state == TaskState.BLOCKED
    # Step 2: With all tasks blocked, next step triggers idle hook
    kernel.run_step()
    assert kernel.idle_hook_called
    # Step 3: External ISR fires notify_interrupt(16)
    kernel.notify_interrupt(16)
    # Step 4: Next kernel step drains IRQ, wakes worker and completes
    kernel.run_step()
    assert irq_received == ["IRQ_16_PROCESSED"]
    assert kernel.tasks["worker"].state == TaskState.TERMINATED


if __name__ == "__main__":
    test_coos_synchronous_rendezvous()
    test_value_has_exactly_one_owner_across_a_rendezvous()
    test_one_waiter_per_channel_is_enforced()
    test_coos_interrupt_wakeup()
    print("[PASS] All COOS concept tests passed successfully.")
