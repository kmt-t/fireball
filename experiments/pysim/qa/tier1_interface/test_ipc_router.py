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


from helpers import expect_assertion, make_test_ipc_message
from ipc_router import (
    FB_CONF_ROUTER_ROLE_MATRIX,
    DataType,
    IPCMessage,
    IPCRouter,
    IPCStatus,
    OwnershipState,
    Role,
    ScopeKind,
    pack_key32,
)
from memory import FB_CONF_MEMORY_POOL_SIZE, MemoryManager
from scheduler import ChannelAction, Scheduler, TaskState, WaitDir
from system import (
    System,
)

_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_SHM_ID = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=1)
_CMD_PIN_HIGH = 1


def _make_router(sched: Scheduler) -> IPCRouter:
    manager = MemoryManager(sched)
    assert manager.init_manager(0x20020000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    return IPCRouter(sched, manager)


def _run_immediate(gen):
    """Drives an IPCRouter.send()/recv() generator that is expected to reject
    at Stage 1/2 (URI lookup / RBAC) -- i.e. never touch a CSP channel and so
    never actually block -- and returns its final (IPCStatus, ...) value."""
    try:
        next(gen)
    except StopIteration as e:
        return e.value
    raise AssertionError("expected immediate Stage 1/2 rejection, but the call blocked")


def test_ipc_01_uri_lookup_and_permission_matrix():
    """TEST-IPC-01: Service URI lookup and role-based access control."""
    sched = Scheduler()
    router = _make_router(sched)
    entry = router.find_service("fireball://hal/gpio/0")
    assert entry is not None
    assert entry.role == Role.HAL_GPIO

    sender_id = sched.spawn("sender", role=Role.RUNTIME)
    sched.current_task = sched.get_task(sender_id)

    # RUNTIME has permission to send to HAL_GPIO
    status1, ch1 = router.lookup("fireball://hal/gpio/0")
    assert status1 == IPCStatus.COMPLETED and ch1 is not None

    msg1 = make_test_ipc_message([(_KEY_CMD, _CMD_PIN_HIGH)], memory_manager=router.memory_manager)
    gen = router.send(ch1, msg1)
    assert next(gen) == (ChannelAction.BLOCK, None)
    assert msg1.ownership == OwnershipState.IN_FLIGHT

    # HAL_GPIO has no outgoing edges at all (role matrix row is all-DENY).
    hal_task_id = sched.spawn("hal_task", role=Role.HAL_GPIO)
    sched.current_task = sched.get_task(hal_task_id)

    status_bad, ch_bad = router.lookup("fireball://hal/gpio/0")
    assert status_bad == IPCStatus.ERR_PERMISSION_DENIED
    assert ch_bad is None

    # Anti-spoofing verification: even if HAL_GPIO holds ch1 (from RUNTIME),
    # send() enforces TCB role check and denies transmission.
    msg2 = make_test_ipc_message([(_KEY_CMD, _CMD_PIN_HIGH)], memory_manager=router.memory_manager)
    gen_spoof = router.send(ch1, msg2)
    try:
        next(gen_spoof)
    except StopIteration as e:
        status_spoof, _ = e.value
        assert status_spoof == IPCStatus.ERR_PERMISSION_DENIED
    assert msg2.ownership == OwnershipState.SENDER_OWNS


def test_ipc_02_e2e_shared_block_transfer():
    """TEST-IPC-02: End-to-end zero-copy SharedBlock transfer via IPC router (CSP rendezvous)."""
    sysv = System()
    try:
        sent: list[IPCStatus] = []
        received: list[IPCMessage] = []
        msg: IPCMessage

        def client_app_task():
            status, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
            assert status == IPCStatus.COMPLETED and ch is not None
            status, response = yield from sysv.ipc.send(ch, msg)
            assert response is msg
            sent.append(status)

        def gpio_receiver():
            status, recv_msg = yield from sysv.ipc.recv()
            received.append(recv_msg)
            assert recv_msg.sender_id == 2
            assert recv_msg.response_code == 0xFFFF_FFFF
            recv_msg.append(0x100, 0xCAFE)
            assert sysv.ipc.reply(recv_msg, 0) == IPCStatus.COMPLETED

        sysv.scheduler.spawn("client_app", client_app_task(), task_id=2, role=Role.RUNTIME)
        sysv.scheduler.current_task = sysv.scheduler.get_task(2)
        # Sender allocates SharedBlock
        sb = sysv.memory_manager.allocate_shared(size=256).unwrap()
        assert sb.get_owner() == 2
        addr = sb.get_address()
        assert addr >= 0x20020000

        # Sender puts shm_id directly in the message entry's value inside shared memory!
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, sb.shm_id)],
            memory_manager=sysv.memory_manager,
        )

        # Spawn receiver (task 1) then sender (task 2)
        sysv.scheduler.spawn("gpio_receiver", gpio_receiver(), task_id=1, role=Role.HAL_GPIO)
        sysv.scheduler.run_until_idle()

        assert sent == [IPCStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # Channel automatically granted ownership of entry's shm_id to receiver task (task 1)!
        recv_shm_id = recv_msg[_KEY_SHM_ID]
        assert recv_shm_id == sb.shm_id

        sysv.scheduler.current_task = sysv.scheduler.get_task(2)
        recv_sb = sysv.memory_manager.claim(recv_shm_id).unwrap()
        assert recv_sb.get_owner() == 2
        assert recv_sb.get_address() == addr
        assert msg.response_code == 0
        assert msg.get(0x100) == 0xCAFE
    finally:
        sysv.shutdown()


def test_ipc_03_send_failure_restores_owner():
    """TEST-IPC-03: If IPC send is rejected (e.g. RBAC denial), sender can rollback."""
    sysv = System()
    try:
        sender_id = sysv.scheduler.spawn("message_builder", task_id=1, role=Role.RUNTIME)
        sysv.scheduler.current_task = sysv.scheduler.get_task(sender_id)
        sb = sysv.memory_manager.allocate_shared(size=256).unwrap()
        shm_id = sb.release()
        msg = IPCMessage.from_entries(
            [(_KEY_SHM_ID, shm_id)],
            memory_manager=sysv.memory_manager,
        )
        sender_id = sysv.scheduler.spawn("hal_sender", task_id=2, role=Role.HAL_UART)
        sysv.scheduler.current_task = sysv.scheduler.get_task(sender_id)

        # HAL_UART has no outgoing edges: rejected at Stage 2 before ever
        # touching a channel.
        status, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
        assert status == IPCStatus.ERR_PERMISSION_DENIED
        assert ch is None
        assert msg.ownership == OwnershipState.SENDER_OWNS
        # Rollback
        sysv.scheduler.current_task = sysv.scheduler.get_task(1)
        assert sysv.scheduler.current_task is not None
        sysv.memory_manager.rollback_transfer(shm_id=shm_id)
        assert sysv.memory_manager.page_registry.get_owner(sb.page_idx) == 1
    finally:
        sysv.shutdown()


def test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group():
    """
    TEST-IPC-04: recv()'s guarded external choice (select) completes with
    whichever allowed sender arrives first -- CORE_SERVICE is reachable from
    both RUNTIME and DEBUGGER, so a receiver must not commit to just one
    upfront. After the select resolves, the losing edge must be cleared (not
    left as a stale waiter) so it remains independently usable afterward.
    """
    sched = Scheduler()
    router = _make_router(sched)

    received: list[tuple[IPCStatus, IPCMessage]] = []

    def core_receiver():
        status, msg = yield from router.recv()
        received.append((status, msg))
        assert msg.sender_id > 0
        msg.append(2, 100)
        assert router.reply(msg, 0x10) == IPCStatus.COMPLETED

    def debugger_sender():
        status, ch = router.lookup("fireball://core/coos/0")
        assert status == IPCStatus.COMPLETED and ch is not None
        status, _ = yield from router.send(
            ch, make_test_ipc_message([(1, 99)], memory_manager=router.memory_manager)
        )
        assert status == IPCStatus.COMPLETED

    recv_id = sched.spawn("core_receiver", core_receiver(), role=Role.CORE_SERVICE)
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

    sched.spawn("debugger_sender", debugger_sender(), role=Role.DEBUGGER)
    sched.run_until_idle()

    assert len(received) == 1
    status, msg = received[0]
    assert status == IPCStatus.COMPLETED
    assert msg.get(1) == 99
    # The losing edge (RUNTIME->CORE_SERVICE) must have been cleared, not
    # left pointing at the now-terminated receiver.
    assert runtime_ch.waiter_dir == WaitDir.NONE
    assert runtime_ch.waiter_task is None

    # That edge must still be independently usable by a fresh receiver.
    received2: list[tuple[IPCStatus, IPCMessage]] = []

    def core_receiver2():
        status, msg = yield from router.recv()
        received2.append((status, msg))
        assert router.reply(msg, 0) == IPCStatus.COMPLETED

    def runtime_sender():
        status, ch = router.lookup("fireball://core/coos/0")
        assert status == IPCStatus.COMPLETED and ch is not None
        status, _ = yield from router.send(
            ch, make_test_ipc_message([(1, 7)], memory_manager=router.memory_manager)
        )
        assert status == IPCStatus.COMPLETED

    sched.spawn("core_receiver2", core_receiver2(), role=Role.CORE_SERVICE)
    sched.spawn("runtime_sender", runtime_sender(), role=Role.RUNTIME)
    sched.run_until_idle()

    assert len(received2) == 1
    assert received2[0][1].get(1) == 7


def test_ipc_05_message_storage_ownership_and_access_check():
    """TEST-IPC-05: IPCMessage owns its SharedBlock storage and enforces ownership checks upon access."""
    from ipc_router import OwnershipState

    msg = make_test_ipc_message([(10, 100), (20, 200)])
    assert msg.ownership == OwnershipState.SENDER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200
    assert len(msg) == 2
    assert 10 in msg
    msg.append(15, 150)
    assert msg.data is not None
    assert msg.flat_map_view.find(15) == 150
    assert msg.flat_map_view.find(99) is None

    # Transition to IN_FLIGHT (sending): access to entries is strictly prohibited
    msg.ownership = OwnershipState.IN_FLIGHT
    with expect_assertion("Cannot access IPCMessage entries while ownership is IN_FLIGHT"):
        _ = msg.get(10)

    with expect_assertion("Cannot access IPCMessage entries while ownership is IN_FLIGHT"):
        _ = msg.entries

    with expect_assertion("Cannot access IPCMessage entries while ownership is IN_FLIGHT"):
        _ = len(msg)

    # Transition to RECEIVER_OWNS: access is permitted again
    msg.ownership = OwnershipState.RECEIVER_OWNS
    assert msg.get(10) == 100
    assert msg.get(20) == 200


def test_ipc_06_router_create_channel_authorization():
    """TEST-IPC-06: router.create_channel() resolves destination, binds current task, checks RBAC, and returns Channel."""
    sched = Scheduler()
    router = _make_router(sched)

    # Task with Role.RUNTIME can open channel to HAL (ALLOWED)
    runtime_task_id = sched.spawn("runtime_task", role=Role.RUNTIME)
    sched.current_task = sched.get_task(runtime_task_id)

    ch_hal = router.create_channel("fireball://hal/gpio/0")
    assert ch_hal is not None, "RUNTIME -> HAL_GPIO must be allowed"

    # Task with Role.HAL_GPIO cannot open channel to DEBUGGER (DENIED)
    hal_task_id = sched.spawn("hal_task", role=Role.HAL_GPIO)
    sched.current_task = sched.get_task(hal_task_id)

    ch_denied = router.create_channel("fireball://debugger/control")
    assert ch_denied is None, "HAL_GPIO -> DEBUGGER must be denied by RBAC"

    sched.current_task = sched.get_task(runtime_task_id)
    # Communication over the authorized channel; the scheduler stamper also
    # authenticates that the current task owns the message SHM.
    msg = make_test_ipc_message([(1, 42)], memory_manager=router.memory_manager)
    action, _ = ch_hal.send(msg)
    assert action == ChannelAction.BLOCK
    assert ch_hal.waiter_dir == WaitDir.SEND


def test_ipc_07_message_in_shm_and_payload_shm_transfer():
    """TEST-IPC-07: The message is resident in shared memory, and can carry another payload SHM ID inside its entries."""
    from ipc_router import DataType, ScopeKind, pack_key32

    sysv = System()
    try:
        sender_id = 2
        receiver_id = 1
        sent: list[IPCStatus] = []
        received: list[IPCMessage] = []
        msg: IPCMessage

        def client_sender():
            status, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
            assert status == IPCStatus.COMPLETED and ch is not None
            status, _ = yield from sysv.ipc.send(ch, msg)
            sent.append(status)

        def hal_receiver():
            status, recv_msg = yield from sysv.ipc.recv()
            received.append(recv_msg)
            assert sysv.ipc.reply(recv_msg, 0) == IPCStatus.COMPLETED

        sysv.scheduler.spawn(
            "hal_receiver", hal_receiver(), task_id=receiver_id, role=Role.HAL_GPIO
        )
        sysv.scheduler.spawn("client_sender", client_sender(), task_id=sender_id, role=Role.RUNTIME)
        sysv.scheduler.current_task = sysv.scheduler.get_task(sender_id)

        # 1. Allocate SharedBlock for the message itself (message is shared memory!)
        sysv.scheduler.current_task = sysv.scheduler.get_task(sender_id)
        msg_sb = sysv.memory_manager.allocate_shared(size=256).unwrap()
        assert msg_sb.get_owner() == sender_id

        # 2. Allocate another SharedBlock for payload bulk data
        payload_sb = sysv.memory_manager.allocate_shared(size=768).unwrap()
        assert payload_sb.get_owner() == sender_id
        payload_shm_id = payload_sb.shm_id

        # 3. Embed payload SHM ID into the message's KV entries (in the memory block!)
        k_payload_id = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=0x14)
        k_payload_len = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=0x01)
        entries = [(k_payload_id, payload_shm_id), (k_payload_len, 768)]

        # Construct message backed by msg_sb and write entries into its memory block!
        msg = IPCMessage(msg_sb, memory_manager=sysv.memory_manager)
        msg.write_entries(entries)
        assert msg.block is msg_sb
        assert msg[k_payload_id] == payload_shm_id

        # Receiver is task 1, Sender is task 2.
        sysv.scheduler.run_until_idle()

        assert sent == [IPCStatus.COMPLETED]
        assert received and received[0] is msg
        recv_msg = received[0]

        # 1. The reply returns the message's own SHM block to the sender.
        assert recv_msg.block is not None
        assert recv_msg.block.get_owner() == sender_id

        # 2. The payload SHM ID is also granted back to the sender.
        retrieved_shm_id = recv_msg.get_by_key_id(0x14, ScopeKind.RESOURCE)
        assert retrieved_shm_id == payload_shm_id

        # Sender claims the payload SharedBlock after the response.
        sysv.scheduler.current_task = sysv.scheduler.get_task(sender_id)
        recv_payload_sb = recv_msg.claim_resource(sysv.memory_manager, key_id=0x14)
        assert recv_payload_sb is not None
        assert recv_payload_sb.get_owner() == sender_id
        assert recv_payload_sb.shm_id == payload_shm_id
    finally:
        sysv.shutdown()


# ===========================================================================
# 9. fireball_call Full Syscall Surface (runtime_syscall_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_ipc_01_uri_lookup_and_permission_matrix()
    test_ipc_02_e2e_shared_block_transfer()
    test_ipc_03_send_failure_restores_owner()
    test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group()
    test_ipc_05_message_storage_ownership_and_access_check()
    test_ipc_06_router_create_channel_authorization()
    test_ipc_07_message_in_shm_and_payload_shm_transfer()
    print("[PASS] All IPC Router & Shared Block Transfer tests passed.")
