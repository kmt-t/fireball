from __future__ import annotations

"""
Unit tests for Tier 1 Interface: IPC Router & Shared Block Transfer
Traceability: ipc_router_test_spec.md
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
from scheduler import ChannelAction, Scheduler, TaskState, WaitDir
from system import (
    System,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_SHM_ID = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=1)
_CMD_PIN_HIGH = 1


def _run_immediate(gen):
    """Drives an IPCRouter.send()/recv() generator that is expected to reject
    at Stage 1/2 (URI lookup / RBAC) -- i.e. never touch a CSP channel and so
    never actually block -- and returns its final (IpcStatus, ...) value."""
    try:
        next(gen)
    except StopIteration as e:
        return e.value
    raise AssertionError("expected immediate Stage 1/2 rejection, but the call blocked")


def test_ipc_01_uri_lookup_and_permission_matrix():
    """IPC-01: Service URI lookup and role-based access control."""
    sched = Scheduler()
    router = IPCRouter(sched)
    entry = router.find_service("fireball://hal/gpio/0")
    assert entry is not None
    assert entry.role == Role.PLATFORM_HAL

    sender_id = sched.spawn("sender")
    sched.current_task = sched.get_task(sender_id)

    # RUNTIME has permission, but nothing is receiving yet -> send() itself
    # genuinely waits (ipc_router.md §5.1), so single-step it exactly like
    # scheduler.Channel's own tests (test_coos_01 etc.) to observe the
    # CSP block directly instead of driving it to a rendezvous that will
    # never come.
    msg1 = IPCMessage.from_entries([(_KEY_CMD, _CMD_PIN_HIGH)])
    gen = router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg1)
    assert next(gen) == (ChannelAction.BLOCK, None)
    assert msg1.ownership == OwnershipState.IN_FLIGHT

    # PLATFORM_HAL has no outgoing edges at all (role matrix row is all-DENY).
    msg2 = IPCMessage.from_entries([(_KEY_CMD, _CMD_PIN_HIGH)])
    status_bad, _ = _run_immediate(router.send(Role.PLATFORM_HAL, "fireball://hal/gpio/0", msg2))
    assert status_bad == IpcStatus.ERR_PERMISSION_DENIED
    assert msg2.ownership == OwnershipState.SENDER_OWNS


def test_ipc_02_e2e_shared_block_transfer():
    """IPC-02: End-to-end zero-copy SharedBlock transfer via IPC router (CSP rendezvous)."""
    sysv = System()
    try:
        # Sender allocates SharedBlock
        sb = sysv.memory_manager.allocate_shared(caller_task_id=2, size=256).unwrap()
        assert sb.get_owner() == 2
        addr = sb.get_address()
        assert addr >= 0x20020000

        # Sender puts shm_id directly in the message entry's value inside shared memory!
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, sb.shm_id)],
            memory_manager=sysv.memory_manager,
            task_id=2,
        )
        sent: list[IpcStatus] = []

        def client_app_task():
            status, _ = yield from sysv.ipc.send(Role.RUNTIME, "fireball://hal/gpio/0", msg)
            sent.append(status)

        received: list[IPCMessage] = []

        def gpio_receiver():
            status, recv_msg = yield from sysv.ipc.recv("fireball://hal/gpio/0")
            received.append(recv_msg)

        # Spawn receiver (task 1) then sender (task 2)
        sysv.scheduler.spawn("gpio_receiver", gpio_receiver())
        sysv.scheduler.spawn("client_app", client_app_task())
        sysv.scheduler.run_until_idle()

        assert sent == [IpcStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # Channel automatically granted ownership of entry's shm_id to receiver task (task 1)!
        recv_shm_id = recv_msg[_KEY_SHM_ID]
        assert recv_shm_id == sb.shm_id

        recv_sb = sysv.memory_manager.claim(receiver_task_id=1, shm_id=recv_shm_id).unwrap()
        assert recv_sb.get_owner() == 1
        assert recv_sb.get_address() == addr
    finally:
        sysv.shutdown()


def test_ipc_03_send_failure_restores_owner():
    """IPC-03: If IPC send is rejected (e.g. RBAC denial), sender can rollback."""
    sysv = System()
    try:
        sb = sysv.memory_manager.allocate_shared(caller_task_id=1, size=256).unwrap()
        shm_id = sb.release()
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, shm_id)],
            memory_manager=sysv.memory_manager,
            task_id=1,
        )
        # PLATFORM_HAL has no outgoing edges: rejected at Stage 2 before ever
        # touching a channel, so this never actually blocks.
        status, _ = _run_immediate(sysv.ipc.send(Role.PLATFORM_HAL, "fireball://hal/gpio/0", msg))
        assert status == IpcStatus.ERR_PERMISSION_DENIED
        assert msg.ownership == OwnershipState.SENDER_OWNS
        # Rollback
        sysv.memory_manager.rollback_transfer(original_sender_id=1, shm_id=shm_id)
        assert sysv.memory_manager.page_registry.get_owner(sb.page_idx) == 1
    finally:
        sysv.shutdown()


def test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group():
    """
    IPC-04: recv()'s guarded external choice (select) completes with
    whichever allowed sender arrives first -- CORE_SERVICE is reachable from
    both RUNTIME and DEBUGGER, so a receiver must not commit to just one
    upfront. After the select resolves, the losing edge must be cleared (not
    left as a stale waiter) so it remains independently usable afterward.
    """
    sched = Scheduler()
    router = IPCRouter(sched)

    received: list[tuple[IpcStatus, IPCMessage]] = []

    def core_receiver():
        status, msg = yield from router.recv("fireball://core/coos/0")
        received.append((status, msg))

    def debugger_sender():
        status, _ = yield from router.send(
            Role.DEBUGGER, "fireball://core/coos/0", IPCMessage.from_entries([(1, 99)])
        )
        assert status == IpcStatus.COMPLETED

    recv_id = sched.spawn("core_receiver", core_receiver())
    sched.run_until_idle()
    assert sched.get_task(recv_id).state == TaskState.SUSPENDED_CSP
    # Selecting on both edges must not double-register: each channel still
    # has exactly one waiter, this same receiver task.
    runtime_ch = router.channel_for_edge(Role.RUNTIME, Role.CORE_SERVICE)
    debugger_ch = router.channel_for_edge(Role.DEBUGGER, Role.CORE_SERVICE)
    assert runtime_ch is not None and debugger_ch is not None
    assert runtime_ch.waiter_dir == WaitDir.RECV
    assert debugger_ch.waiter_dir == WaitDir.RECV
    assert runtime_ch.waiter_task is debugger_ch.waiter_task

    sched.spawn("debugger_sender", debugger_sender())
    sched.run_until_idle()

    assert len(received) == 1
    status, msg = received[0]
    assert status == IpcStatus.COMPLETED
    assert msg.get(1) == 99
    # The losing edge (RUNTIME->CORE_SERVICE) must have been cleared, not
    # left pointing at the now-terminated receiver.
    assert runtime_ch.waiter_dir == WaitDir.NONE
    assert runtime_ch.waiter_task is None

    # That edge must still be independently usable by a fresh receiver.
    received2: list[tuple[IpcStatus, IPCMessage]] = []

    def core_receiver2():
        status, msg = yield from router.recv("fireball://core/coos/0")
        received2.append((status, msg))

    def runtime_sender():
        status, _ = yield from router.send(
            Role.RUNTIME, "fireball://core/coos/0", IPCMessage.from_entries([(1, 7)])
        )
        assert status == IpcStatus.COMPLETED

    sched.spawn("core_receiver2", core_receiver2())
    sched.spawn("runtime_sender", runtime_sender())
    sched.run_until_idle()

    assert len(received2) == 1
    assert received2[0][1].get(1) == 7


def test_ipc_05_message_storage_ownership_and_access_check():
    """IPC-05: IPCMessage owns its SharedBlock storage and enforces ownership checks upon access."""
    from ipc_router import OwnershipState

    msg = IPCMessage.from_entries([(10, 100), (20, 200)])
    assert msg.ownership == OwnershipState.SENDER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200
    assert len(msg) == 2
    assert 10 in msg

    # Transition to IN_FLIGHT (sending): access to entries is strictly prohibited
    msg.ownership = OwnershipState.IN_FLIGHT
    try:
        _ = msg.get(10)
        raise AssertionError("Accessing entries during IN_FLIGHT must raise AssertionError")
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    try:
        _ = msg.entries
        raise AssertionError(
            "Accessing entries property during IN_FLIGHT must raise AssertionError"
        )
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    try:
        _ = len(msg)
        raise AssertionError("Calling len() during IN_FLIGHT must raise AssertionError")
    except AssertionError as e:
        assert "Cannot access IPCMessage entries while ownership is IN_FLIGHT" in str(e)

    # Transition to RECEIVER_OWNS: access is permitted again
    msg.ownership = OwnershipState.RECEIVER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200


def test_ipc_06_router_create_channel_authorization():
    """IPC-06: router.create_channel() resolves destination, binds current task, checks RBAC, and returns Channel."""
    sched = Scheduler()
    router = IPCRouter(sched)

    # Task with Role.RUNTIME can open channel to HAL (ALLOWED)
    runtime_task_id = sched.spawn("runtime_task", role=Role.RUNTIME)
    sched.current_task = sched.get_task(runtime_task_id)

    ch_hal = router.create_channel("fireball://hal/gpio/0")
    assert ch_hal is not None, "RUNTIME -> PLATFORM_HAL must be allowed"

    # Task with Role.PLATFORM_HAL cannot open channel to DEBUGGER (DENIED)
    hal_task_id = sched.spawn("hal_task", role=Role.PLATFORM_HAL)
    sched.current_task = sched.get_task(hal_task_id)

    ch_denied = router.create_channel("fireball://debugger/control")
    assert ch_denied is None, "PLATFORM_HAL -> DEBUGGER must be denied by RBAC"

    # Communication over the authorized channel
    msg = IPCMessage.from_entries([(1, 42)])
    sched.current_task = sched.get_task(runtime_task_id)
    action, _ = ch_hal.send(msg)
    assert action == ChannelAction.BLOCK
    assert ch_hal.waiter_dir == WaitDir.SEND


def test_ipc_07_message_in_shm_and_payload_shm_transfer():
    """IPC-07: The message is resident in shared memory, and can carry another payload SHM ID inside its entries."""
    from ipc_router import DataType, ScopeKind, pack_key32

    sysv = System()
    try:
        sender_id = 2
        receiver_id = 1

        # 1. Allocate SharedBlock for the message itself (message is shared memory!)
        msg_sb = sysv.memory_manager.allocate_shared(caller_task_id=sender_id, size=256).unwrap()
        assert msg_sb.get_owner() == sender_id

        # 2. Allocate another SharedBlock for payload bulk data
        payload_sb = sysv.memory_manager.allocate_shared(
            caller_task_id=sender_id, size=1024
        ).unwrap()
        assert payload_sb.get_owner() == sender_id
        payload_shm_id = payload_sb.shm_id

        # 3. Embed payload SHM ID into the message's KV entries (in the memory block!)
        k_payload_id = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=0x14)
        k_payload_len = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0x01)
        entries = [(k_payload_id, payload_shm_id), (k_payload_len, 1024)]

        # Construct message backed by msg_sb and write entries into its memory block!
        msg = IPCMessage(msg_sb)
        msg.write_entries(entries)
        assert msg.block is msg_sb
        assert msg[k_payload_id] == payload_shm_id

        sent: list[IpcStatus] = []

        def client_sender():
            status, _ = yield from sysv.ipc.send(Role.RUNTIME, "fireball://hal/gpio/0", msg)
            sent.append(status)

        received: list[IPCMessage] = []

        def hal_receiver():
            status, recv_msg = yield from sysv.ipc.recv("fireball://hal/gpio/0")
            received.append(recv_msg)

        # Receiver is task 1, Sender is task 2
        sysv.scheduler.spawn("hal_receiver", hal_receiver())
        sysv.scheduler.spawn("client_sender", client_sender())
        sysv.scheduler.run_until_idle()

        assert sent == [IpcStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # 1. Message's own SHM block is granted to receiver!
        assert recv_msg.block is not None
        assert recv_msg.block.get_owner() == receiver_id

        # 2. Payload SHM ID in entries was also automatically granted to receiver!
        retrieved_shm_id = recv_msg.get_by_key_id(0x14, ScopeKind.RESOURCE)
        assert retrieved_shm_id == payload_shm_id

        # Receiver claims the payload SharedBlock
        recv_payload_sb = recv_msg.claim_resource(sysv.memory_manager, receiver_id, key_id=0x14)
        assert recv_payload_sb is not None
        assert recv_payload_sb.get_owner() == receiver_id
        assert recv_payload_sb.shm_id == payload_shm_id
    finally:
        sysv.shutdown()


# ===========================================================================
# 9. fireball_call Full Syscall Surface (system_syscall_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_ipc_01_uri_lookup_and_permission_matrix()
    test_ipc_02_e2e_shared_block_transfer()
    test_ipc_03_send_failure_restores_owner()
    test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group()
    test_ipc_05_message_storage_ownership_and_access_check()
    test_ipc_06_router_create_channel_authorization()
    test_ipc_07_message_in_shm_and_payload_shm_transfer()
    print("[PASS] All 7 IPC Router & Shared Block Transfer tests passed.")
