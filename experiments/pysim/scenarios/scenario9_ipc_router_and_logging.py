from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

"""Integration Scenario 9: Tier 1 Interface IPC Router & Structured System Logging.

Tests:
- 3-Stage IPC Router pipeline (Static URI FlatMapView lookup, RBAC role check,
  bufferless synchronous CSP handoff via scheduler.Channel)
- Message KV-pair static buffer limit (ERR_MSG_TOO_LARGE)
- Dictionary-based structured logging (LogDictionary, LogLevel filtering, UART transport emission)
- Safety check rejecting unsafe format specifiers (%s/%p) at dictionary registration
"""

from hal import UartTransport
from ipc_router import (
    DataType,
    IPCMessage,
    IPCRouter,
    IpcStatus,
    OwnershipState,
    Role,
    ScopeKind,
    pack_key32,
)
from logger import LogDictionary, Logger, LogLevel
from scheduler import Scheduler
from system_containers import FlatMapStorage

# kv_pair key_ids (ipc_router.md §3.3): Functional scope, UINT32 values.
_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_TASK_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=2)
_CMD_START_TASK = 1
_CMD_KILL = 2


def test_scenario_ipc_router_and_logging():
    print("[*] Running Scenario 9: Tier 1 Interface IPC Router & Structured Logging...")
    # -------------------------------------------------------------------------
    # Phase 1: IPC Router 3-Stage Pipeline (URI lookup -> RBAC -> CSP handoff)
    # -------------------------------------------------------------------------
    sched = Scheduler()
    router = IPCRouter(sched)

    # IPC is inter-*task* communication: both parties below are genuine
    # scheduler tasks, each performing its own sequence of sends/recvs as its
    # own coroutine -- never a bare top-level function call.
    sent: list[tuple[str, IpcStatus, IPCMessage]] = []

    def client_app_task():
        # 1. Full CSP rendezvous with coos_receiver (spawned alongside this
        #    task below): whichever of the two runs first genuinely blocks,
        #    and the other's matching call completes the handoff.
        s1 = FlatMapStorage([(_KEY_CMD, _CMD_START_TASK), (_KEY_TASK_ID, 10)])
        msg1 = IPCMessage(s1)
        status, _ = yield from router.send(Role.RUNTIME, "fireball://core/coos/0", msg1)
        sent.append(("1_rendezvous", status, msg1))

        # 2. RBAC Permission Denied: no RUNTIME -> DEBUGGER edge exists.
        s2 = FlatMapStorage([(_KEY_CMD, _CMD_KILL)])
        msg2 = IPCMessage(s2)
        status, _ = yield from router.send(Role.RUNTIME, "fireball://dbg/manager/0", msg2)
        sent.append(("2_permission_denied", status, msg2))

        # 3. URI Not Found
        msg3 = IPCMessage()
        status, _ = yield from router.send(Role.RUNTIME, "fireball://unknown/service", msg3)
        sent.append(("3_not_found", status, msg3))

        # 4. Message exceeds the static 8 kv_pair buffer (ipc_router.md §3.3/§5.1).
        s_oversized = FlatMapStorage([(i, i) for i in range(9)])
        oversized = IPCMessage(s_oversized)
        status, _ = yield from router.send(Role.RUNTIME, "fireball://core/coos/0", oversized)
        sent.append(("4_too_large", status, oversized))

    received: list[IPCMessage] = []

    def coos_receiver():
        # recv() selects across every allowed incoming edge (RUNTIME and
        # DEBUGGER may both legitimately send to CORE_SERVICE) rather than
        # committing to just one sender_role upfront.
        status, msg = yield from router.recv("fireball://core/coos/0")
        received.append(msg)

    sched.spawn("coos_receiver", coos_receiver())
    sched.spawn("client_app", client_app_task())
    sched.run_until_idle()

    results = {name: (status, msg) for name, status, msg in sent}
    status1, msg1 = results["1_rendezvous"]
    assert status1 == IpcStatus.COMPLETED
    assert msg1.ownership == OwnershipState.RECEIVER_OWNS, (
        "ownership transfers atomically the instant the rendezvous completes"
    )
    assert received == [msg1]
    assert received[0][_KEY_CMD] == _CMD_START_TASK
    assert received[0][_KEY_TASK_ID] == 10
    print(
        "    [Phase 1.1] IPC CSP Rendezvous (blocking recv -> sender handoff) -> RECEIVER_OWNS [PASS]"
    )

    status2, msg2 = results["2_permission_denied"]
    assert status2 == IpcStatus.ERR_PERMISSION_DENIED
    assert msg2.ownership == OwnershipState.SENDER_OWNS
    print("    [Phase 1.2] IPC RBAC Check (RUNTIME -> DEBUGGER) -> ERR_PERMISSION_DENIED [PASS]")

    status3, _ = results["3_not_found"]
    assert status3 == IpcStatus.ERR_NOT_FOUND
    print("    [Phase 1.3] IPC URI Lookup (Unknown URI) -> ERR_NOT_FOUND [PASS]")

    status4, msg4 = results["4_too_large"]
    assert status4 == IpcStatus.ERR_MSG_TOO_LARGE
    assert msg4.ownership == OwnershipState.SENDER_OWNS
    print("    [Phase 1.4] IPC Message KV-pair Limit (9 > 8) -> ERR_MSG_TOO_LARGE [PASS]")

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
