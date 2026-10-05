from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent


"""Integration Scenario 9: Tier 1 Interface IPC Router & Structured System Logging.

Tests:
- 3-Stage IPC Router pipeline (Static URI ReadOnlyFlatMapView lookup, RBAC role check,
  bufferless synchronous CSP handoff via scheduler.Channel)
- Message KV-pair static buffer limit (ERR_MSG_TOO_LARGE)
- Dictionary-based structured logging (LogDictionary, LogLevel filtering, UART transport emission)
- Safety check rejecting %s at dictionary registration
"""

from ipc_router import (
    DataType,
    IPCMessage,
    IPCRouter,
    IPCStatus,
    OwnershipState,
    Role,
    ScopeKind,
    pack_key32,
)
from qa.shared.memory import snapshot_ipc_storage
from scheduler import Scheduler, WaitDir
from tier2_runtime.memory.manager import FB_CONF_MEMORY_POOL_SIZE, MemoryManager
from tier2_runtime.observability.logger import (
    LogDictionary,
    Logger,
    LogLevel,
    LogResult,
)
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.printk import PrintkSink

# kv_pair key_ids (ipc_router.md §3.3): Functional scope, UINT32 values.
_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_TASK_ID = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=2)
_CMD_START_TASK = 1
_CMD_KILL = 2


def _make_test_ipc_message(
    entries: Sequence[tuple[int, int]], memory_manager: MemoryManager
) -> IPCMessage:
    """Construct message storage from the scheduler-owned scenario manager."""
    return IPCMessage.from_entries(entries, memory_manager=memory_manager)


def test_scenario_ipc_router_and_logging():
    print("[*] Running Scenario 9: Tier 1 Interface IPC Router & Structured Logging...")
    # -------------------------------------------------------------------------
    # Stage 1: IPC Router 3-Stage Pipeline (URI lookup -> RBAC -> CSP handoff)
    # -------------------------------------------------------------------------
    sched = Scheduler()
    manager = MemoryManager(sched)
    assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    router = IPCRouter(sched, manager)

    # IPC is inter-*task* communication: both parties below are genuine
    # scheduler tasks, each performing its own sequence of sends/recvs as its
    # own coroutine -- never a bare top-level function call.
    sent: list[tuple[str, IPCStatus, IPCMessage]] = []

    def client_app_task():
        # 1. Full CSP rendezvous with coos_receiver (spawned alongside this
        #    task below): whichever of the two runs first genuinely blocks,
        #    and the other's matching call completes the handoff.
        status1, ch1 = router.lookup("fireball://core/coos/0")
        assert status1 == IPCStatus.COMPLETED and ch1 is not None

        msg1 = _make_test_ipc_message([(_KEY_CMD, _CMD_START_TASK), (_KEY_TASK_ID, 10)], manager)
        status, _ = yield from router.send(ch1, msg1)
        sent.append(("1_rendezvous", status, msg1))

        # 2. RBAC Permission Denied: no RUNTIME -> DEBUGGER edge exists.
        msg2 = _make_test_ipc_message([(_KEY_CMD, _CMD_KILL)], manager)
        status2, ch2 = router.lookup("fireball://dbg/manager/0")
        sent.append(("2_permission_denied", status2, msg2))

        # 3. URI Not Found
        msg3 = _make_test_ipc_message((), manager)
        status3, ch3 = router.lookup("fireball://unknown/service")
        sent.append(("3_not_found", status3, msg3))

        # 4. Message exceeds the static 8 kv_pair buffer (ipc_router.md §3.3/§5.1).
        oversized = _make_test_ipc_message([(i, i) for i in range(8)], manager)
        # Corrupt the wire count after a valid internal construction. Internal
        # construction of nine entries is an assert contract, not this status path.
        oversized.block.write_u64(0, 9)
        before = snapshot_ipc_storage(oversized.block)
        status4, _ = yield from router.send(ch1, oversized)
        assert snapshot_ipc_storage(oversized.block) == before
        assert ch1.waiter_task is None
        assert ch1.waiter_dir == WaitDir.NONE
        sent.append(("4_too_large", status4, oversized))

    received: list[IPCMessage] = []
    received_payloads: list[tuple[int, int]] = []

    def coos_receiver():
        # recv() selects across every allowed incoming edge (RUNTIME and
        # DEBUGGER may both legitimately send to CORE_SERVICE) rather than
        # committing to just one sender_role upfront.
        status, msg = yield from router.recv()
        received.append(msg)
        received_payloads.append((msg[_KEY_CMD], msg[_KEY_TASK_ID]))
        assert (yield from router.reply(msg, 0)) == IPCStatus.COMPLETED

    sched.spawn("coos_receiver", coos_receiver(), role=Role.CORE_SERVICE)
    sched.spawn("client_app", client_app_task(), role=Role.RUNTIME)
    sched.run_until_idle()

    results = {name: (status, msg) for name, status, msg in sent}
    status1, msg1 = results["1_rendezvous"]
    assert status1 == IPCStatus.COMPLETED
    assert msg1.ownership == OwnershipState.SENDER_OWNS, (
        "request/reply returns ownership to the sender after the receiver replies"
    )
    assert received == [msg1]
    assert received_payloads == [(_CMD_START_TASK, 10)]
    print(
        "    [Stage 1.1] IPC CSP Request/Reply (receiver handoff -> sender unblock) -> SENDER_OWNS [PASS]"
    )

    status2, msg2 = results["2_permission_denied"]
    assert status2 == IPCStatus.ERR_PERMISSION_DENIED
    assert msg2.ownership == OwnershipState.SENDER_OWNS
    print("    [Stage 1.2] IPC RBAC Check (RUNTIME -> DEBUGGER) -> ERR_PERMISSION_DENIED [PASS]")

    status3, _ = results["3_not_found"]
    assert status3 == IPCStatus.ERR_NOT_FOUND
    print("    [Stage 1.3] IPC URI Lookup (Unknown URI) -> ERR_NOT_FOUND [PASS]")

    status4, msg4 = results["4_too_large"]
    assert status4 == IPCStatus.ERR_MSG_TOO_LARGE
    assert msg4.ownership == OwnershipState.SENDER_OWNS
    print("    [Stage 1.4] IPC Message KV-pair Limit (9 > 8) -> ERR_MSG_TOO_LARGE [PASS]")

    # -------------------------------------------------------------------------
    # Section 2: Structured System Logging & LogDictionary Safety
    # -------------------------------------------------------------------------
    transport = StreamTransport()
    log_dict = LogDictionary(
        entries=((0x1100, "TASK_INIT: id=%d priority=%d"), (0x1104, "COOS_STATE: state=0x%08X"))
    )
    # This example checks %s rejection at dictionary build time.
    rejected = False
    try:
        LogDictionary(
            entries=((0x1108, "UNSAFE_STRING: name=%s"),), include_diagnostic_events=False
        )
    except AssertionError:
        rejected = True
    assert rejected, "LogDictionary must reject %s pointer specifier"
    print("    [Section 2.1] LogDictionary Pointer Specifier Rejection (%s) -> REJECTED [PASS]")
    # 3. Emit structured logs via Logger
    logger = Logger(
        transport=PrintkSink(transport, log_dict.decode_record),
        dictionary=log_dict,
        min_level=LogLevel.INFO,
    )
    assert logger.log_event(LogLevel.INFO, 0x1100, 1, 5) == LogResult.SUCCESS
    assert logger.log_event(LogLevel.DEBUG, 0x1104, 0x12345678) == LogResult.FILTERED
    assert logger.log_event(LogLevel.ERROR, 0x1104, 0xDEADBEEF) == LogResult.SUCCESS
    # Explicitly flush buffered logs; COOS idle_hook integration is not exercised here.
    flushed_count = logger.flush()
    assert flushed_count == 2
    # Read UART output stream
    emitted = transport.drain_output().decode("utf-8")
    assert "TASK_INIT: id=1 priority=5" in emitted
    assert "0x12345678" not in emitted  # DEBUG filtered
    assert "COOS_STATE: state=0xDEADBEEF" in emitted
    print("    [Section 2.2] Buffered Logging & Explicit Flush -> 2 Entries Flushed [PASS]")
    print(
        "    [PASS] Scenario 9 routing/logging examples; idle_hook and overwrite remain unverified."
    )


if __name__ == "__main__":
    test_scenario_ipc_router_and_logging()
