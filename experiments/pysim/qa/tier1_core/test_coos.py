from __future__ import annotations

"""
Unit tests for Tier 1 Core: COOS Rendezvous & Handoff
Traceability: os_coos_test_spec.md
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
from interrupt_event import InterruptEvent
from scheduler import ChannelAction, Scheduler, Task, TaskState, WaitDir


def _activate_task(scheduler: Scheduler, task: Task) -> None:
    scheduler.current_task = None
    scheduler.detach(task)
    scheduler.activate_task(task)


def test_coos_01_send_first_suspends_csp():
    """TEST-COOS-01: Sender arriving first transitions to SUSPENDED_CSP; value stays in frame."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1_id = sched.spawn("t1")
    t1 = sched.get_task(t1_id)
    _activate_task(sched, t1)
    action, _ = ch.send(42)
    assert action == ChannelAction.BLOCK
    assert t1.state == TaskState.SUSPENDED_CSP
    assert t1.pending_val == 42
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND


def test_coos_02_recv_after_send_completes_rendezvous():
    """TEST-COOS-02: Receiver arriving second completes rendezvous and takes ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t1)
    ch.send("DATA_PAYLOAD")
    _activate_task(sched, t2)
    action, _ = ch.recv()
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == "DATA_PAYLOAD"
    assert t1.pending_val is None, "Pending value must be cleared on sender (no double-ownership)"
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_03_recv_first_suspends_csp():
    """TEST-COOS-03: Receiver arriving first transitions to SUSPENDED_CSP."""
    sched = Scheduler()
    ch = sched.create_channel()
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t2)
    action, _ = ch.recv()
    assert action == ChannelAction.BLOCK
    assert t2.state == TaskState.SUSPENDED_CSP
    assert ch.waiter_task == t2
    assert ch.waiter_dir == WaitDir.RECV


def test_coos_04_send_after_recv_completes_rendezvous():
    """TEST-COOS-04: Sender arriving second completes rendezvous and transfers ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t2)
    ch.recv()
    _activate_task(sched, t1)
    action, _ = ch.send(12345)
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == 12345
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_05_one_waiter_per_channel_enforced():
    """TEST-COOS-05: Only one waiter per channel direction; second waiter asserts."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t1)
    ch.send(1)
    _activate_task(sched, t2)
    with expect_assertion("separate channels"):
        ch.send(2)


def test_coos_06_csp_handoff_direct_switch():
    """TEST-COOS-06: Rendezvous completion performs direct symmetric handoff to head of READY queue."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t1)
    ch.send(99)
    _activate_task(sched, t2)
    action, target_id = ch.recv()
    assert action == ChannelAction.DIRECT_SWITCH
    assert target_id == t1.task_id
    assert sched._ready[0] == t1, "Target task must be placed at front of READY queue"


def test_coos_07_consecutive_handoff_limit_yields():
    """TEST-COOS-07: Consecutive handoff limit (4) forces yield back to main loop."""
    sched = Scheduler(max_handoffs=2)
    ch1 = sched.create_channel()
    ch2 = sched.create_channel()
    ch3 = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t1)
    ch1.send(1)
    _activate_task(sched, t2)
    act1, _ = ch1.recv()
    assert act1 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 1
    _activate_task(sched, t1)
    ch2.send(2)
    _activate_task(sched, t2)
    act2, _ = ch2.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 2
    _activate_task(sched, t1)
    ch3.send(3)
    _activate_task(sched, t2)
    act3, _ = ch3.recv()
    assert act3 == ChannelAction.YIELD, (
        "Must yield back to scheduler when consecutive handoffs reach threshold"
    )
    assert sched.consecutive_handoffs == 0


def test_coos_08_interrupt_notification_and_drain():
    """TEST-COOS-08 / GOTCHA-COOS-03: ISR notification queues interrupt without direct mutation
    (non-blocking ISR-side enqueue); drain wakes the waiting task on the next idle pass."""
    sched = Scheduler()
    woken = []
    received = []

    def irq_handler():
        sched.wait_for_interrupt(16)
        yield (ChannelAction.BLOCK, None)
        event = sched.consume_interrupt_event()
        assert event is not None
        received.append(event.words())
        woken.append("IRQ_PROCESSED")

    sched.spawn("handler", irq_handler())
    sched.run_until_idle()
    assert len(woken) == 0
    sched.notify_interrupt(InterruptEvent(16, 0, 0, 0, 0))
    sched.run_until_idle()
    assert woken == ["IRQ_PROCESSED"]
    assert received == [(16, 0, 0, 0, 0)]


def test_coos_09_interrupt_queue_overflow_drops():
    """TEST-COOS-09: Overflowing ISR queue drops notification and increments dropped_irqs counter."""
    sched = Scheduler()
    for i in range(16):
        assert sched.notify_interrupt(InterruptEvent(i, 0, 0, 0, 0))

    # 17th notification must drop
    assert not sched.notify_interrupt(InterruptEvent(17, 0, 0, 0, 0))
    assert sched.dropped_irqs == 1


def test_coos_10_idle_detection_when_all_blocked():
    """TEST-COOS-10: the idle hook fires once the READY queue empties because every task is blocked
    (SUSPENDED_CSP), not merely because the run loop happened to stop."""
    sched = Scheduler()
    ch = sched.create_channel()
    idle_calls: list[int] = []
    sched.set_idle_hook(lambda: idle_calls.append(1))

    def blocked_receiver():
        ch.recv()
        yield (ChannelAction.BLOCK, None)

    task_id = sched.spawn("blocked_receiver", blocked_receiver())
    assert idle_calls == [], "idle hook must not fire before the run loop is driven"
    sched.run_until_idle()
    assert idle_calls == [1], "idle hook must fire exactly once when READY queue empties"
    task = sched.get_task(task_id)
    assert task.state == TaskState.SUSPENDED_CSP, "the sole task must be blocked, not terminated"


def test_coos_11_no_double_ownership_sanity():
    """TEST-COOS-11: at rendezvous completion, the sender's pending_val and the receiver's
    received_val are never both populated at once (single-owner invariant; mirrors
    coos_channel_model.py's AG(Not(double_owned)))."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    _activate_task(sched, t1)
    ch.send("PAYLOAD")
    assert t1.pending_val == "PAYLOAD" and ch.waiter_task == t1
    _activate_task(sched, t2)
    ch.recv()
    # Immediately after rendezvous completes: sender no longer holds the value,
    # receiver now holds it -- never both simultaneously.
    assert t1.pending_val is None, "sender must relinquish the value at rendezvous completion"
    assert t2.received_val == "PAYLOAD", "receiver must hold the value at rendezvous completion"


def test_coos_12_task_killed_removes_csp_and_irq_wait_registrations():
    """An external kill terminates blocked tasks without stale wake-up registrations."""
    sched = Scheduler()
    channel_a = sched.create_channel()
    channel_b = sched.create_channel()

    def selected_receiver():
        channel_a.recv()
        yield (ChannelAction.BLOCK, None)

    task_id = sched.spawn("selected_receiver", selected_receiver())
    task = sched.get_task(task_id)
    assert task is not None
    _activate_task(sched, task)
    action, _ = sched.channel_select_recv((channel_a, channel_b))
    assert action == ChannelAction.BLOCK
    sched.current_task = None
    assert sched.task_killed(task_id)
    assert task.state == TaskState.TERMINATED
    assert channel_a.waiter_task is None
    assert channel_b.waiter_task is None
    assert task.coro is None

    irq_task_id = sched.spawn("irq_waiter")
    irq_task = sched.get_task(irq_task_id)
    assert irq_task is not None
    _activate_task(sched, irq_task)
    sched.wait_for_interrupt(7)
    sched.current_task = None
    assert sched.notify_interrupt(InterruptEvent(7, 1, 2, 3, 4))
    assert sched.task_killed(irq_task_id)
    assert irq_task.state == TaskState.TERMINATED
    assert irq_task.waiting_irq is None
    assert sched.drain_interrupts() == 1
    assert irq_task.state == TaskState.TERMINATED
    assert not sched.task_killed(-1)
    assert not sched.task_killed(irq_task_id)

    running_task_id = sched.spawn("running_task")
    running_task = sched.get_task(running_task_id)
    assert running_task is not None
    _activate_task(sched, running_task)
    running_task.state = TaskState.RUNNING
    with expect_assertion("a running task cannot be killed externally"):
        sched.task_killed(running_task_id)
    assert running_task.state == TaskState.RUNNING
    sched.current_task = None
    running_task.state = TaskState.READY
    sched.attach(running_task)


# ===========================================================================
# 2. Tier 1 Scheduler: Pure Round-Robin (os_scheduler_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_coos_01_send_first_suspends_csp()
    test_coos_02_recv_after_send_completes_rendezvous()
    test_coos_03_recv_first_suspends_csp()
    test_coos_04_send_after_recv_completes_rendezvous()
    test_coos_05_one_waiter_per_channel_enforced()
    test_coos_06_csp_handoff_direct_switch()
    test_coos_07_consecutive_handoff_limit_yields()
    test_coos_08_interrupt_notification_and_drain()
    test_coos_09_interrupt_queue_overflow_drops()
    test_coos_10_idle_detection_when_all_blocked()
    test_coos_11_no_double_ownership_sanity()
    test_coos_12_task_killed_removes_csp_and_irq_wait_registrations()
    print("[PASS] All 12 COOS Rendezvous & Handoff tests passed.")
