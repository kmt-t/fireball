"""Integration Scenario 9: Tier 1 Interface IPC Router & Structured System Logging.

Tests:
- 3-Stage IPC Router pipeline (Static URI FlatMapView lookup, RBAC role check, Zero-copy ownership handoff)
- Queue capacity overflow handling & fault rollback
- Dictionary-based structured logging (LogDictionary, LogLevel filtering, UART transport emission)
- Safety check rejecting unsafe format specifiers (%s/%p) at dictionary registration
"""

from __future__ import annotations

from hal import UartTransport
from ipc_router import IPCMessage, IPCRouter, OwnershipState
from logger import LogDictionary, Logger, LogLevel


def test_scenario_ipc_router_and_logging():
    print("[*] Running Scenario 9: Tier 1 Interface IPC Router & Structured Logging...")

    # -------------------------------------------------------------------------
    # Phase 1: IPC Router 3-Stage Routing Pipeline
    # -------------------------------------------------------------------------
    router = IPCRouter()

    # 1. Successful routing: SENDER_OWNS -> IN_FLIGHT -> receive -> RECEIVER_OWNS
    msg1 = IPCMessage(resource_id="res_01", payload={"cmd": "START_TASK", "task_id": 10})
    status, detail = router.route_message("CLIENT_APP", "fireball://core/coos/0", msg1)
    assert status == "OK_ENQUEUED"
    assert msg1.ownership == OwnershipState.IN_FLIGHT
    assert len(router.queues["ch_coos"]) == 1

    # Receive message
    rcv_msg = router.receive_message("ch_coos")
    assert rcv_msg is msg1
    assert msg1.ownership == OwnershipState.RECEIVER_OWNS
    print("    [Phase 1.1] IPC Routing & Zero-Copy Ownership Handoff -> OK_ENQUEUED & RECEIVER_OWNS [PASS]")

    # 2. RBAC Permission Denied
    msg2 = IPCMessage(resource_id="res_02", payload={"cmd": "KILL"})
    status, reason = router.route_message("CLIENT_APP", "fireball://dbg/manager/0", msg2)
    assert status == "ERR_PERMISSION_DENIED"
    assert msg2.ownership == OwnershipState.SENDER_OWNS
    print("    [Phase 1.2] IPC RBAC Check (CLIENT_APP -> DEBUGGER) -> ERR_PERMISSION_DENIED [PASS]")

    # 3. URI Not Found
    msg3 = IPCMessage(resource_id="res_03", payload={})
    status, reason = router.route_message("CLIENT_APP", "fireball://unknown/service", msg3)
    assert status == "ERR_NOT_FOUND"
    print("    [Phase 1.3] IPC URI Lookup (Unknown URI) -> ERR_NOT_FOUND [PASS]")

    # 4. Queue Overflow & Rollback (fireball://hal/gpio/0 has max_queue 2)
    msg_q1 = IPCMessage(resource_id="res_q1", payload={})
    msg_q2 = IPCMessage(resource_id="res_q2", payload={})
    msg_q3 = IPCMessage(resource_id="res_q3", payload={})
    assert router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg_q1)[0] == "OK_ENQUEUED"
    assert router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg_q2)[0] == "OK_ENQUEUED"
    status_over, _ = router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg_q3)
    assert status_over == "ERR_QUEUE_FULL"
    assert msg_q3.ownership == OwnershipState.SENDER_OWNS
    print("    [Phase 1.4] IPC Queue Capacity Overflow & Rollback -> ERR_QUEUE_FULL [PASS]")

    # 5. Target Fault Drop Handler (Fault Recovery)
    reclaimed = router.trigger_drop_handler("ch_gpio")
    assert reclaimed == ["res_q1", "res_q2"]
    assert msg_q1.ownership == OwnershipState.RECLAIMED_BY_DROP
    assert msg_q2.ownership == OwnershipState.RECLAIMED_BY_DROP
    print("    [Phase 1.5] IPC Target Service Fault Drop Handler Recovery -> RECLAIMED [PASS]")

    # -------------------------------------------------------------------------
    # Phase 2: Structured System Logging & LogDictionary Safety
    # -------------------------------------------------------------------------
    transport = UartTransport()
    log_dict = LogDictionary(capacity=16)

    # 1. Register valid format strings
    log_dict.register(0x100, "TASK_INIT: id=%d priority=%d")
    log_dict.register(0x104, "COOS_STATE: state=0x%08X")

    # 2. Unsafe format specifier (%s) must be rejected
    rejected = False
    try:
        log_dict.register(0x108, "UNSAFE_STRING: name=%s")
    except ValueError:
        rejected = True
    assert rejected, "LogDictionary must reject %s pointer specifier"
    print("    [Phase 2.1] LogDictionary Pointer Specifier Rejection (%s) -> REJECTED [PASS]")

    # 3. Emit structured logs via Logger
    logger = Logger(transport=transport, dictionary=log_dict, min_level=LogLevel.INFO)
    assert logger.log_event(LogLevel.INFO, 0x100, 1, 5) == "QUEUED"
    assert logger.log_event(LogLevel.DEBUG, 0x104, 0x12345678) == "FILTERED"  # Filtered out
    assert logger.log_event(LogLevel.ERROR, 0x104, 0xDEADBEEF) == "QUEUED"

    # Flush buffered logs to UART (simulating COOS idle_hook flush)
    flushed_count = logger.flush()
    assert flushed_count == 2

    # Read UART output stream
    emitted = transport.drain().decode("ascii")
    assert "TASK_INIT: id=1 priority=5" in emitted
    assert "0x12345678" not in emitted  # DEBUG filtered
    assert "COOS_STATE: state=0xDEADBEEF" in emitted
    print("    [Phase 2.2] Buffered Logging & COOS Idle Flush -> 2 Entries Flushed [PASS]")

    print("    [PASS] Scenario 9 (IPC Router & Structured Logging) verified completely.")


if __name__ == "__main__":
    test_scenario_ipc_router_and_logging()
