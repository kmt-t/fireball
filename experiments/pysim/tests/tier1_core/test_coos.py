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
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from scheduler import ChannelAction, Scheduler, TaskState, WaitDir


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_coos_01_send_first_suspends_csp():
    """COOS-01: Sender arriving first transitions to SUSPENDED_CSP; value stays in frame."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1_id = sched.spawn("t1")
    t1 = sched.get_task(t1_id)
    sched.current_task = t1
    action, _ = ch.send(42)
    assert action == ChannelAction.BLOCK
    assert t1.state == TaskState.SUSPENDED_CSP
    assert t1.pending_val == 42
    assert ch.waiter_task == t1
    assert ch.waiter_dir == WaitDir.SEND


def test_coos_02_recv_after_send_completes_rendezvous():
    """COOS-02: Receiver arriving second completes rendezvous and takes ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send("DATA_PAYLOAD")
    sched.current_task = t2
    action, _ = ch.recv()
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == "DATA_PAYLOAD"
    assert t1.pending_val is None, "Pending value must be cleared on sender (no double-ownership)"
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_03_recv_first_suspends_csp():
    """COOS-03: Receiver arriving first transitions to SUSPENDED_CSP."""
    sched = Scheduler()
    ch = sched.create_channel()
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t2
    action, _ = ch.recv()
    assert action == ChannelAction.BLOCK
    assert t2.state == TaskState.SUSPENDED_CSP
    assert ch.waiter_task == t2
    assert ch.waiter_dir == WaitDir.RECV


def test_coos_04_send_after_recv_completes_rendezvous():
    """COOS-04: Sender arriving second completes rendezvous and transfers ownership."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t2
    ch.recv()
    sched.current_task = t1
    action, _ = ch.send(12345)
    assert action in (ChannelAction.DIRECT_SWITCH, ChannelAction.YIELD)
    assert t2.received_val == 12345
    assert t1.state == TaskState.READY
    assert t2.state == TaskState.READY


def test_coos_05_one_waiter_per_channel_enforced():
    """COOS-05: Only one waiter per channel direction; second waiter asserts."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send(1)
    sched.current_task = t2
    try:
        ch.send(2)
        raise AssertionError("Expected AssertionError for second sender on same channel")
    except AssertionError as e:
        assert "separate channels" in str(e)


def test_coos_06_csp_handoff_direct_switch():
    """COOS-06: Rendezvous completion performs direct symmetric handoff to head of READY queue."""
    sched = Scheduler()
    ch = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch.send(99)
    sched.current_task = t2
    action, target_id = ch.recv()
    assert action == ChannelAction.DIRECT_SWITCH
    assert target_id == t1.task_id
    assert sched._ready[0] == t1, "Target task must be placed at front of READY queue"


def test_coos_07_consecutive_handoff_limit_yields():
    """COOS-07: Consecutive handoff limit (4) forces yield back to main loop."""
    sched = Scheduler(max_handoffs=2)
    ch1 = sched.create_channel()
    ch2 = sched.create_channel()
    ch3 = sched.create_channel()
    t1 = sched.get_task(sched.spawn("t1"))
    t2 = sched.get_task(sched.spawn("t2"))
    sched.current_task = t1
    ch1.send(1)
    sched.current_task = t2
    act1, _ = ch1.recv()
    assert act1 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 1
    sched.current_task = t1
    ch2.send(2)
    sched.current_task = t2
    act2, _ = ch2.recv()
    assert act2 == ChannelAction.DIRECT_SWITCH
    assert sched.consecutive_handoffs == 2
    sched.current_task = t1
    ch3.send(3)
    sched.current_task = t2
    act3, _ = ch3.recv()
    assert act3 == ChannelAction.YIELD, (
        "Must yield back to scheduler when consecutive handoffs reach threshold"
    )
    assert sched.consecutive_handoffs == 0


def test_coos_08_interrupt_notification_and_drain():
    """COOS-08: ISR notification queues interrupt without direct mutation; drain wakes task."""
    sched = Scheduler()
    woken = []

    def irq_handler():
        sched.wait_for_interrupt(16)
        yield (ChannelAction.BLOCK, None)
        woken.append("IRQ_PROCESSED")

    sched.spawn("handler", irq_handler())
    sched.run_until_idle()
    assert len(woken) == 0
    sched.notify_interrupt(16)
    sched.run_until_idle()
    assert woken == ["IRQ_PROCESSED"]


def test_coos_09_interrupt_queue_overflow_drops():
    """COOS-09: Overflowing ISR queue drops notification and increments dropped_irqs counter."""
    sched = Scheduler()
    for i in range(16):
        assert sched.notify_interrupt(i)

    # 17th notification must drop
    assert not sched.notify_interrupt(17)
    assert sched.dropped_irqs == 1


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
    print("[PASS] All 9 COOS Rendezvous & Handoff tests passed.")
