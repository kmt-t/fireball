from __future__ import annotations

"""
Unit tests for Tier 1 Interface: IPC Router & Shared Block Transfer
Traceability: ipc_router_test_spec.md
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


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
    unpack_key32,
)
from qa.shared.helpers import expect_assertion, make_test_ipc_message
from qa.shared.ipc import lookup_channel_as
from qa.shared.memory import snapshot_ipc_storage
from scheduler import ChannelAction, Scheduler, Task, TaskState, WaitDir
from system import (
    System,
)
from tier2_runtime.memory.manager import (
    FB_CONF_MEMORY_POOL_SIZE,
    FB_CONF_SHM_SIM_BASE,
    FB_CONF_SHM_SIZE,
    FB_TASK_ID_FLIGHT,
    MemoryManager,
)

_KEY_CMD = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_SHM_ID = pack_key32(ScopeKind.RESOURCE, DataType.UINT32, key_id=1)
_CMD_PIN_HIGH = 1

# ipc_router.md の通信許可表と登録 URI から導く。製品のマトリックスを期待値に使わない。
_REGISTERED_TARGETS = (
    (Role.CORE_SERVICE, "fireball://core/coos/0"),
    (Role.DEBUGGER, "fireball://dbg/manager/0"),
    (Role.HAL_UART, "fireball://hal/uart/0"),
    (Role.HAL_STDOUT, "fireball://hal/stdout/0"),
    (Role.HAL_GPIO, "fireball://hal/gpio/0"),
    (Role.HAL_TIMER, "fireball://hal/timer/0"),
    (Role.HAL_I2C, "fireball://hal/i2c/0"),
    (Role.HAL_SPI, "fireball://hal/spi/0"),
)
_ALLOWED_HAL_SENDERS = (Role.RUNTIME, Role.CORE_SERVICE, Role.DEBUGGER)


def _make_router(sched: Scheduler) -> IPCRouter:
    manager = MemoryManager(sched)
    assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    return IPCRouter(sched, manager)


@contextmanager
def _drive_task(sched: Scheduler, task: Task) -> Iterator[None]:
    """手動で操作列を進める前に、実スケジューラと同様に READY 登録を取り除く。"""
    sched.detach(task)
    with sched.task_context(task):
        yield


IpcResultT = TypeVar("IpcResultT")


def _run_immediate(
    gen: Generator[tuple[ChannelAction, None], None, IpcResultT],
) -> IpcResultT:
    """Return the final status of an IPC call that must complete without blocking."""
    try:
        next(gen)
    except StopIteration as e:
        return e.value
    raise AssertionError("expected immediate IPC completion, but the call blocked")


def test_ipc_01_uri_lookup_and_permission_matrix():
    """TEST-IPCR-02/05: 登録 URI の検索と、借用チャネルによるロール偽装の拒否。"""
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
    status_spoof, response = _run_immediate(gen_spoof)
    assert status_spoof == IPCStatus.ERR_PERMISSION_DENIED
    assert response is None
    assert msg2.ownership == OwnershipState.SENDER_OWNS
    assert tuple(msg2.entries) == ((_KEY_CMD, _CMD_PIN_HIGH),)
    assert ch1.waiter_task is sched.get_task(sender_id)
    assert ch1.reply_payload is msg1


def test_ipc_02_e2e_shared_block_transfer():
    """TEST-IPCR-07/21/22/24: 実タスク間で同じメッセージを追加応答付きで返す。"""
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
            assert (yield from sysv.ipc.reply(recv_msg, 0)) == IPCStatus.COMPLETED

        sysv.scheduler.spawn("client_app", client_app_task(), task_id=2, role=Role.RUNTIME)
        sysv.scheduler.current_task = sysv.scheduler.get_task(2)
        # Sender allocates SharedBlock
        sb = sysv.memory_manager.allocate_shared(size=256).unwrap()
        assert sb.get_owner() == 2
        addr = sb.get_address()
        assert FB_CONF_SHM_SIM_BASE <= addr < FB_CONF_SHM_SIM_BASE + FB_CONF_SHM_SIZE

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


@pytest.mark.parametrize("rejection", ("wrong_role_channel", "too_many_entries"))
def test_send_preflight_rejection_preserves_shared_memory(rejection: str) -> None:
    """TEST-IPCR-05/14/15, GOTCHA-IPCR-02: 拒否時は Revoke せず、後続の正規送信ができる。"""
    sched = Scheduler()
    router = _make_router(sched)
    sender_id = sched.spawn("runtime_sender", role=Role.RUNTIME)
    sender = sched.get_task(sender_id)
    assert sender is not None
    sched.current_task = sender
    status, valid_channel = router.lookup("fireball://hal/gpio/0")
    assert status == IPCStatus.COMPLETED and valid_channel is not None
    payload = router.memory_manager.allocate_shared(size=64).unwrap()
    payload.write_u32(0, 0xCAFE_BABE)
    entries = [(_KEY_SHM_ID, payload.shm_id), (_KEY_CMD, 41)]
    msg = make_test_ipc_message(entries, memory_manager=router.memory_manager)
    block = msg.block
    assert block is not None
    registry = router.memory_manager.page_registry
    generations = (
        registry.get_generation(block.page_idx),
        registry.get_generation(payload.page_idx),
    )
    initial_bytes = snapshot_ipc_storage(block)
    initial_task_state = sender.state

    if rejection == "wrong_role_channel":
        denied_channel = lookup_channel_as(router, Role.DEBUGGER, "fireball://hal/gpio/0")
        assert denied_channel is not None
        expected_status = IPCStatus.ERR_PERMISSION_DENIED
    else:
        # 正本の上限 8 を超える 9 要素で送信境界を検査する。構築の成否はこの契約ではない。
        entries.extend((key, key * 10) for key in range(2, 9))
        # Inject a malformed wire count at the receiving boundary. The
        # internal construction API must itself assert on >8 entries.
        block.write_u64(0, 9)
        initial_bytes = snapshot_ipc_storage(block)
        denied_channel = valid_channel
        expected_status = IPCStatus.ERR_MSG_TOO_LARGE

    status, response = _run_immediate(router.send(denied_channel, msg))
    assert status == expected_status
    assert response is None
    assert msg.ownership == OwnershipState.SENDER_OWNS
    assert msg.block is block
    assert snapshot_ipc_storage(block) == initial_bytes
    assert payload.read_u32(0) == 0xCAFE_BABE
    assert registry.get_owner(block.page_idx) == sender_id
    assert registry.get_owner(payload.page_idx) == sender_id
    assert (
        registry.get_generation(block.page_idx),
        registry.get_generation(payload.page_idx),
    ) == generations
    assert sender.state == initial_task_state
    assert denied_channel.waiter_dir == WaitDir.NONE
    assert denied_channel.waiter_task is None
    assert denied_channel.reply_sender_task is None
    assert denied_channel.reply_payload is None
    assert denied_channel.reply_sender_task is None

    # rollback を挟まず、同じメッセージを正規チャネルへ再送する。
    if rejection == "too_many_entries":
        msg.write_entries(entries[:8])
    assert next(router.send(valid_channel, msg)) == (ChannelAction.BLOCK, None)
    assert msg.ownership == OwnershipState.IN_FLIGHT
    assert valid_channel.waiter_task is sender


def test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group():
    """
    TEST-IPCR-17/18: recv()'s guarded external choice (select) completes with
    whichever allowed sender arrives first -- CORE_SERVICE is reachable from
    both RUNTIME and DEBUGGER, so a receiver must not commit to just one
    upfront. After the select resolves, the losing edge must be cleared (not
    left as a stale waiter) so it remains independently usable afterward.
    """
    sched = Scheduler()
    router = _make_router(sched)

    received: list[tuple[IPCStatus, int, int | None]] = []

    def core_receiver():
        status, msg = yield from router.recv()
        assert msg.sender_id > 0
        received.append((status, msg.sender_id, msg.get(1)))
        msg.append(2, 100)
        assert (yield from router.reply(msg, 0x10)) == IPCStatus.COMPLETED

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
    runtime_ch = lookup_channel_as(router, Role.RUNTIME, "fireball://core/coos/0")
    debugger_ch = lookup_channel_as(router, Role.DEBUGGER, "fireball://core/coos/0")
    assert runtime_ch is not None and debugger_ch is not None
    assert runtime_ch.waiter_dir == WaitDir.RECV
    assert debugger_ch.waiter_dir == WaitDir.RECV
    assert runtime_ch.waiter_task is debugger_ch.waiter_task

    debugger_sender_id = sched.spawn("debugger_sender", debugger_sender(), role=Role.DEBUGGER)
    sched.run_until_idle()

    assert len(received) == 1
    status, sender_id, received_value = received[0]
    assert status == IPCStatus.COMPLETED
    assert sender_id == debugger_sender_id
    assert received_value == 99
    # The losing edge (RUNTIME->CORE_SERVICE) must have been cleared, not
    # left pointing at the now-terminated receiver.
    assert runtime_ch.waiter_dir == WaitDir.NONE
    assert runtime_ch.waiter_task is None

    # That edge must still be independently usable by a fresh receiver.
    received2: list[tuple[IPCStatus, int | None]] = []

    def core_receiver2():
        status, msg = yield from router.recv()
        received2.append((status, msg.get(1)))
        assert (yield from router.reply(msg, 0)) == IPCStatus.COMPLETED

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
    assert received2[0] == (IPCStatus.COMPLETED, 7)


def test_ipc_05_message_storage_ownership_and_access_check():
    """TEST-IPCR-19: IPCMessage owns its SharedBlock storage and enforces ownership checks upon access."""
    from ipc_router import OwnershipState

    scheduler = Scheduler()
    owner_id = scheduler.spawn("message_owner")
    owner = scheduler.get_task(owner_id)
    assert owner is not None
    manager = MemoryManager(scheduler)
    assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok

    with scheduler.task_context(owner):
        msg = make_test_ipc_message([(10, 100), (20, 200)], memory_manager=manager)
        assert msg.ownership == OwnershipState.SENDER_OWNS
        assert msg.get(10) == 100
        assert msg.get(20) == 200
        assert len(msg) == 2
        assert 10 in msg
        msg.append(15, 150)
        assert msg.block is not None
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

        # Transition to RECEIVER_OWNS: this state gate permits access after flight ends.
        msg.ownership = OwnershipState.RECEIVER_OWNS
        assert msg.get(10) == 100
        assert msg.get(20) == 200


@given(
    st.lists(
        st.tuples(st.integers(0, 0xFFFFFFFF), st.integers(0, 0xFFFFFFFF)),
        unique_by=lambda entry: entry[0],
        max_size=8,
    )
)
def test_ipc_batch_sorted_unique_map_roundtrip(entries: list[tuple[int, int]]) -> None:
    """TEST-IPCR-19/26: u32境界を含む一意なKVを、実共有ブロックへ格納して検索する。"""
    scheduler = Scheduler()
    router = _make_router(scheduler)
    owner_id = scheduler.spawn("owner")
    scheduler.current_task = scheduler.get_task(owner_id)
    message = make_test_ipc_message(entries, memory_manager=router.memory_manager)
    assert tuple(message.entries) == tuple(sorted(entries))
    assert len(message) == len(entries)
    for key, value in entries:
        assert message.payload.find(key) == value
    block = message.block
    assert block is not None
    assert block.read_u64(0) == len(entries)
    assert tuple(block.read_entry(i + 1) for i in range(len(entries))) == tuple(sorted(entries))


@pytest.mark.parametrize(
    "entries, reason",
    [
        ([(1, 10), (1, 20)], "duplicate IPC key"),
        ([(i, i) for i in range(9)], "IPC entry count exceeds capacity"),
        ([(-1, 10)], "IPC key must fit u32"),
        ([(0x100000000, 10)], "IPC key must fit u32"),
        ([(1, -1)], "IPC value must fit u32"),
        ([(1, 0x100000000)], "IPC value must fit u32"),
    ],
)
def test_ipc_invalid_internal_batch_asserts_before_write(
    entries: list[tuple[int, int]], reason: str
) -> None:
    """TEST-IPCR-26: OS内部の構築契約違反はassertし、共有バイトを変更しない。"""
    scheduler = Scheduler()
    router = _make_router(scheduler)
    scheduler.current_task = scheduler.get_task(scheduler.spawn("owner"))
    message = make_test_ipc_message([(2, 20), (4, 40)], memory_manager=router.memory_manager)
    block = message.block
    assert block is not None
    before = snapshot_ipc_storage(block)
    with expect_assertion(reason):
        message.write_entries(entries)
    assert snapshot_ipc_storage(block) == before
    assert tuple(message.entries) == ((2, 20), (4, 40))


def test_ipc_borrow_and_narrowed_entries_read_live_shared_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST-IPCR-19: 借用時に全KVをコピーせず、狭めた区間も実体を参照する。"""
    from tier2_runtime.memory.manager import SharedBlock

    scheduler = Scheduler()
    router = _make_router(scheduler)
    scheduler.current_task = scheduler.get_task(scheduler.spawn("owner"))
    message = make_test_ipc_message(
        [(i, i * 10) for i in range(8)], memory_manager=router.memory_manager
    )
    block = message.block
    assert block is not None
    reads: list[int] = []
    original = SharedBlock.read_entry

    def read_entry(self: SharedBlock, index: int) -> tuple[int, int]:
        reads.append(index)
        return original(self, index)

    monkeypatch.setattr(SharedBlock, "read_entry", read_entry)
    view = message.payload
    assert reads == []
    assert view.find(7) == 70
    assert len(reads) <= 5  # binary search plus equality/value reads, not 8-entry materialization
    narrowed = view.slice(2, 5)
    entries = narrowed.entries
    reads.clear()
    block.write_entry(4, 3, 0xFFFFFFFF)
    assert reads == []
    assert narrowed.find(3) == 0xFFFFFFFF
    assert tuple(entries) == ((2, 20), (3, 0xFFFFFFFF), (4, 40))
    assert narrowed.find(1) is None
    assert narrowed.find(5) is None


def test_ipc_old_borrows_fail_after_actual_handoff_and_reply() -> None:
    """TEST-IPCR-07/19: Revoke前のviewはGrant後も失効し、新所有者は新たに借用する。"""
    scheduler = Scheduler()
    router = _make_router(scheduler)
    sender = scheduler.get_task(scheduler.spawn("sender", role=Role.RUNTIME))
    receiver = scheduler.get_task(scheduler.spawn("receiver", role=Role.HAL_GPIO))
    assert sender is not None and receiver is not None
    with scheduler.task_context(sender):
        message = make_test_ipc_message(
            [(10, 100), (20, 200)], memory_manager=router.memory_manager
        )
        old_full = message.payload
        old_range = old_full.slice(1, 2).entries
        message.stamp_sender(sender.task_id)
        with expect_assertion():
            old_full.find(10)
        with expect_assertion():
            tuple(old_range)
    with scheduler.task_context(receiver):
        assert message.move_to(receiver.task_id) is message
        message.ownership = OwnershipState.RECEIVER_OWNS
        assert message.payload.find(10) == 100
        receiver_view = message.payload
        with expect_assertion():
            old_full.find(10)
        message.prepare_reply()
        with expect_assertion():
            receiver_view.find(20)
    with scheduler.task_context(sender):
        assert message.move_to(sender.task_id) is message
        message.ownership = OwnershipState.SENDER_OWNS
        assert message.payload.find(20) == 200
        with expect_assertion():
            old_full.find(10)
        with expect_assertion():
            receiver_view.find(20)


def test_ipc_corrupt_count_is_not_silently_clamped() -> None:
    """TEST-IPCR-26: 実体の上限違反を8要素へ丸めて処理を続けない。"""
    scheduler = Scheduler()
    router = _make_router(scheduler)
    scheduler.current_task = scheduler.get_task(scheduler.spawn("owner"))
    message = make_test_ipc_message([(1, 10)], memory_manager=router.memory_manager)
    block = message.block
    assert block is not None
    block.write_u64(0, 9)
    with expect_assertion("IPC entry count exceeds capacity"):
        _ = message.payload
    assert block.read_u64(0) == 9


def test_ipc_06_router_lookup_authorization():
    """TEST-IPCR-02/05: lookup は登録 URI の RBAC を検査する。"""
    sched = Scheduler()
    router = _make_router(sched)

    # Task with Role.RUNTIME can open channel to HAL (ALLOWED)
    runtime_task_id = sched.spawn("runtime_task", role=Role.RUNTIME)
    sched.current_task = sched.get_task(runtime_task_id)

    status, ch_hal = router.lookup("fireball://hal/gpio/0")
    assert status == IPCStatus.COMPLETED
    assert ch_hal is not None, "RUNTIME -> HAL_GPIO must be allowed"

    # Task with Role.HAL_GPIO cannot open channel to DEBUGGER (DENIED)
    hal_task_id = sched.spawn("hal_task", role=Role.HAL_GPIO)
    sched.current_task = sched.get_task(hal_task_id)

    assert router.find_service("fireball://dbg/manager/0") is not None
    status, ch_denied = router.lookup("fireball://dbg/manager/0")
    assert status == IPCStatus.ERR_PERMISSION_DENIED
    assert ch_denied is None, "HAL_GPIO -> DEBUGGER must be denied by RBAC"

    sched.current_task = sched.get_task(runtime_task_id)
    # Communication over the authorized channel; the scheduler stamper also
    # authenticates that the current task owns the message SHM.
    msg = make_test_ipc_message([(1, 42)], memory_manager=router.memory_manager)
    action, _ = ch_hal.send(msg)
    assert action == ChannelAction.BLOCK
    assert ch_hal.waiter_dir == WaitDir.SEND


def test_ipc_07_message_in_shm_and_payload_shm_transfer():
    """TEST-IPCR-07/19: メッセージ本体と埋込みリソースの所有権を往復移譲する。"""
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
            assert (yield from sysv.ipc.reply(recv_msg, 0)) == IPCStatus.COMPLETED

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


@pytest.mark.parametrize("sender_role", tuple(Role), ids=lambda role: role.name)
@pytest.mark.parametrize(
    "target_role,uri", _REGISTERED_TARGETS, ids=[role.name for role, _ in _REGISTERED_TARGETS]
)
def test_lookup_matches_specification_permission_matrix(
    sender_role: Role, target_role: Role, uri: str
) -> None:
    """TEST-IPCR-02/04/05/06/15: 9 ロール × 8 登録 URI の許可表を独立に照合する。"""
    sched = Scheduler()
    router = _make_router(sched)
    sender_id = sched.spawn("caller", role=sender_role)
    sched.current_task = sched.get_task(sender_id)
    descriptor = router.find_service(uri)
    assert descriptor is not None and descriptor.role == target_role

    allowed = sender_role in _ALLOWED_HAL_SENDERS and target_role not in (
        Role.CORE_SERVICE,
        Role.DEBUGGER,
    )
    if target_role == Role.CORE_SERVICE:
        allowed = sender_role in (Role.RUNTIME, Role.DEBUGGER)

    status, channel = router.lookup(uri)
    if allowed:
        assert status == IPCStatus.COMPLETED
        assert channel is not None
        assert sched.get_channel(channel.channel_id) is channel
        assert channel.waiter_dir == WaitDir.NONE
    else:
        assert status == IPCStatus.ERR_PERMISSION_DENIED
        assert channel is None


def test_unknown_uri_rejection_preserves_message() -> None:
    """TEST-IPCR-03, GOTCHA-IPCR-02: 未登録 URI は宛先チャネルも Revoke も発生させない。"""
    sched = Scheduler()
    router = _make_router(sched)
    sender_id = sched.spawn("sender", role=Role.RUNTIME)
    sender = sched.get_task(sender_id)
    assert sender is not None
    sched.current_task = sender
    msg = make_test_ipc_message([(13, 57)], memory_manager=router.memory_manager)
    block = msg.block
    assert block is not None
    initial_bytes = snapshot_ipc_storage(block)
    initial_generation = router.memory_manager.page_registry.get_generation(block.page_idx)
    initial_task_state = sender.state

    assert router.lookup("fireball://hal/gpio/not_registered") == (IPCStatus.ERR_NOT_FOUND, None)

    assert msg.ownership == OwnershipState.SENDER_OWNS
    assert msg.get(13) == 57
    assert snapshot_ipc_storage(block) == initial_bytes
    assert router.memory_manager.page_registry.get_owner(block.page_idx) == sender_id
    assert router.memory_manager.page_registry.get_generation(block.page_idx) == initial_generation
    assert sender.state == initial_task_state


@pytest.mark.parametrize("scope", (0, 1, 2))
@pytest.mark.parametrize("data_type", (0, 1, 2, 3))
@pytest.mark.parametrize("key_id", (0, 0xFF_FFFF), ids=("minimum", "maximum"))
def test_key_scope_type_and_identifier_have_independent_bit_positions(
    scope: int, data_type: int, key_id: int
) -> None:
    """TEST-IPCR-13: 正本の 3/5/24 bit 配置を pack/unpack の往復だけに依存せず検査する。"""
    encoded = (scope << 29) | (data_type << 24) | key_id
    assert pack_key32(scope, data_type, key_id) == encoded
    assert unpack_key32(encoded) == (scope, data_type, key_id)


@pytest.mark.parametrize("receiver_first", (False, True), ids=("sender_first", "receiver_first"))
@pytest.mark.parametrize("extend_reply", (False, True), ids=("echo", "extended"))
def test_request_reply_preserves_contents_and_transfers_exclusive_ownership(
    receiver_first: bool, extend_reply: bool
) -> None:
    """TEST-IPCR-07/11/12/21/22/23/24/25: 各到達順で SHM・内容・応答待機を直接確認する。"""
    sched = Scheduler()
    router = _make_router(sched)
    manager = router.memory_manager
    sender_id = sched.spawn("sender", task_id=17, role=Role.RUNTIME)
    receiver_id = sched.spawn("receiver", task_id=29, role=Role.HAL_GPIO)
    sender = sched.get_task(sender_id)
    receiver = sched.get_task(receiver_id)
    assert sender is not None and receiver is not None

    with _drive_task(sched, sender):
        payload = manager.allocate_shared(size=64).unwrap()
        payload.write_u32(0, 0xDEAD_BEEF)
        payload_addr = payload.get_address()
        msg = make_test_ipc_message(
            [(_KEY_SHM_ID, payload.shm_id), (_KEY_CMD, 41)], memory_manager=manager
        )
        block = msg.block
        assert block is not None
        block_addr = block.get_address()
        expected_entries = ((_KEY_CMD, 41), (_KEY_SHM_ID, payload.shm_id))
        assert tuple(msg.entries) == expected_entries
        status, channel = router.lookup("fireball://hal/gpio/0")
        assert status == IPCStatus.COMPLETED and channel is not None
        send = router.send(channel, msg)

    receive = router.recv()
    if receiver_first:
        with _drive_task(sched, receiver):
            assert next(receive) == (ChannelAction.BLOCK, None)
        assert receiver.state == TaskState.SUSPENDED_CSP

    with _drive_task(sched, sender):
        assert next(send) == (ChannelAction.BLOCK, None)
        assert msg.ownership == OwnershipState.IN_FLIGHT
        with expect_assertion("IN_FLIGHT"):
            msg.get(_KEY_CMD)
        with expect_assertion("inactive or in-flight"):
            block.read_u8(0)
        with expect_assertion():
            payload.read_u32(0)
    if not receiver_first:
        assert manager.page_registry.get_owner(block.page_idx) == FB_TASK_ID_FLIGHT
        assert manager.page_registry.get_owner(payload.page_idx) == FB_TASK_ID_FLIGHT

    with _drive_task(sched, receiver):
        status, received = _run_immediate(receive)
        assert status == IPCStatus.COMPLETED and received is msg
        assert msg.ownership == OwnershipState.RECEIVER_OWNS
        assert msg.sender_id == sender_id
        assert msg.response_code == 0xFFFF_FFFF
        assert tuple(msg.entries) == expected_entries
        received_block = msg.block
        assert received_block is not None
        assert received_block.get_address() == block_addr
        assert manager.page_registry.get_owner(block.page_idx) == receiver_id
        assert manager.page_registry.get_owner(payload.page_idx) == receiver_id
        received_payload = msg.claim_resource(manager, key_id=1)
        assert received_payload is not None
        assert received_payload.get_address() == payload_addr
        assert received_payload.read_u32(0) == 0xDEAD_BEEF

    if not receiver_first:
        with _drive_task(sched, sender):
            assert next(send) == (ChannelAction.BLOCK, None)
    assert sender.state == TaskState.SUSPENDED_CSP
    assert channel.reply_waiter_task is sender

    with _drive_task(sched, receiver):
        # pending の reply を拒否しても受信側の所有権と送信側の待機を保つ。
        with expect_assertion("receiver must set a response code"):
            _run_immediate(router.reply(msg))
        assert msg.ownership == OwnershipState.RECEIVER_OWNS
        assert msg.response_code == 0xFFFF_FFFF
        assert sender.state == TaskState.SUSPENDED_CSP
        assert manager.page_registry.get_owner(block.page_idx) == receiver_id
        assert manager.page_registry.get_owner(payload.page_idx) == receiver_id
        if extend_reply:
            msg.append(0x100, 0xCAFE)
        assert _run_immediate(router.reply(msg, 0x23)) == IPCStatus.COMPLETED
        with expect_assertion("inactive or in-flight"):
            received_block.read_u8(0)
        with expect_assertion():
            received_payload.read_u32(0)

    assert sender.state == TaskState.READY
    with _drive_task(sched, sender):
        status, response = _run_immediate(send)
        assert status == IPCStatus.COMPLETED and response is msg
        assert msg.ownership == OwnershipState.SENDER_OWNS
        assert msg.sender_id == sender_id
        assert msg.response_code == 0x23
        expected_reply = expected_entries
        if extend_reply:
            expected_reply = ((0x100, 0xCAFE), *expected_entries)
        assert tuple(msg.entries) == expected_reply
        returned_block = msg.block
        assert returned_block is not None and returned_block.get_address() == block_addr
        returned_payload = msg.claim_resource(manager, key_id=1)
        assert returned_payload is not None
        assert returned_payload.get_address() == payload_addr
        assert returned_payload.read_u32(0) == 0xDEAD_BEEF
        assert manager.page_registry.get_owner(block.page_idx) == sender_id
        assert manager.page_registry.get_owner(payload.page_idx) == sender_id
    assert channel.reply_sender_task is None
    assert channel.reply_payload is None
    assert sender.received_val is None
    assert channel.reply_waiter_task is None


@pytest.mark.parametrize("response_sender_first", (False, True))
def test_reply_rendezvous_blocks_response_sender_until_receive(response_sender_first: bool) -> None:
    """応答の両到達順で、成立前保持・Grant・COMPLETED境界を直接検査する。"""
    sched = Scheduler()
    router = _make_router(sched)
    sender = sched.get_task(sched.spawn("sender", role=Role.RUNTIME))
    receiver = sched.get_task(sched.spawn("receiver", role=Role.HAL_GPIO))
    assert sender is not None and receiver is not None
    with _drive_task(sched, sender):
        status, channel = router.lookup("fireball://hal/gpio/0")
        assert status == IPCStatus.COMPLETED and channel is not None
        msg = make_test_ipc_message([(1, 101)], memory_manager=router.memory_manager)
        send = router.send(channel, msg)
        assert next(send) == (ChannelAction.BLOCK, None)
    with _drive_task(sched, receiver):
        assert _run_immediate(router.recv()) == (IPCStatus.COMPLETED, msg)
        assert msg.ownership == OwnershipState.RECEIVER_OWNS
        assert msg.response_code == 0xFFFF_FFFF
        block = msg.block
        assert block is not None
        assert router.memory_manager.page_registry.get_owner(block.page_idx) == receiver.task_id
    if not response_sender_first:
        with _drive_task(sched, sender):
            assert next(send) == (ChannelAction.BLOCK, None)
        assert channel.reply_waiter_task is sender
    with _drive_task(sched, receiver):
        reply = router.reply(msg, 0x23)
        if response_sender_first:
            assert next(reply) == (ChannelAction.BLOCK, None)
            assert receiver.state == TaskState.SUSPENDED_CSP
            assert receiver.pending_val is msg
            assert channel.waiter_task is receiver
            assert channel.reply_waiter_task is None
            assert sender.received_val is None
            assert msg.ownership == OwnershipState.IN_FLIGHT
            assert (
                router.memory_manager.page_registry.get_owner(block.page_idx) == FB_TASK_ID_FLIGHT
            )
            with expect_assertion("IN_FLIGHT"):
                msg.get(1)
            with expect_assertion("inactive or in-flight"):
                block.read_u8(0)
            # A request receiver must not mistake the suspended response for
            # the next request or overwrite the original authenticated sender.
            with expect_assertion("pending response"):
                sched.channel_recv(channel)
            with expect_assertion("pending response"):
                sched.channel_select_recv((channel,))
            assert channel.reply_sender_task is sender
            assert channel.waiter_task is receiver and receiver.pending_val is msg
            assert msg.ownership == OwnershipState.IN_FLIGHT
        else:
            assert _run_immediate(reply) == IPCStatus.COMPLETED
    with _drive_task(sched, sender):
        assert _run_immediate(send) == (IPCStatus.COMPLETED, msg)
        assert msg.ownership == OwnershipState.SENDER_OWNS
        assert msg.response_code == 0x23
        assert msg.get(1) == 101
        assert router.memory_manager.page_registry.get_owner(block.page_idx) == sender.task_id
    assert receiver.pending_val is None
    assert channel.waiter_task is None and channel.waiter_dir == WaitDir.NONE
    assert channel.reply_sender_task is None and channel.reply_payload is None
    assert channel.reply_waiter_task is None
    if response_sender_first:
        assert receiver.state == TaskState.READY
        with _drive_task(sched, receiver):
            assert _run_immediate(reply) == IPCStatus.COMPLETED


def test_router_role_matrix_is_immutable_after_build() -> None:
    """ビルド時RBAC表のDENYセルは実行時に書き換えできない。"""
    row = FB_CONF_ROUTER_ROLE_MATRIX[int(Role.RUNTIME)]
    index = int(Role.DEBUGGER)
    assert row[index] is False
    with expect_assertion("frozen"):
        row[index] = True
    assert row[index] is False


@pytest.mark.parametrize("request_received", (False, True), ids=("request_wait", "reply_wait"))
def test_second_send_cannot_overwrite_active_transaction(request_received: bool) -> None:
    """TEST-IPCR-08/10, GOTCHA-IPCR-01: 要求待機・応答待機の双方で二重送信を拒否する。"""
    sched = Scheduler()
    router = _make_router(sched)
    first_id = sched.spawn("first_sender", role=Role.RUNTIME)
    second_id = sched.spawn("second_sender", role=Role.RUNTIME)
    receiver_id = sched.spawn("receiver", role=Role.HAL_GPIO)
    first = sched.get_task(first_id)
    second = sched.get_task(second_id)
    receiver = sched.get_task(receiver_id)
    assert first is not None and second is not None and receiver is not None
    with _drive_task(sched, first):
        msg = make_test_ipc_message([(1, 101)], memory_manager=router.memory_manager)
        status, channel = router.lookup("fireball://hal/gpio/0")
        assert status == IPCStatus.COMPLETED and channel is not None
        send = router.send(channel, msg)
        assert next(send) == (ChannelAction.BLOCK, None)
    if request_received:
        with _drive_task(sched, receiver):
            assert _run_immediate(router.recv()) == (IPCStatus.COMPLETED, msg)
        with _drive_task(sched, first):
            assert next(send) == (ChannelAction.BLOCK, None)

    with _drive_task(sched, second):
        second_msg = make_test_ipc_message([(1, 202)], memory_manager=router.memory_manager)
        second_block = second_msg.block
        assert second_block is not None
        with expect_assertion("one request/reply" if request_received else "one waiter"):
            next(router.send(channel, second_msg))
        assert second_msg.ownership == OwnershipState.SENDER_OWNS
        assert second_msg.get(1) == 202
        assert router.memory_manager.page_registry.get_owner(second_block.page_idx) == second_id
    assert first.state == TaskState.SUSPENDED_CSP
    assert channel.reply_sender_task is first
    assert channel.reply_payload is msg
    if not request_received:
        assert channel.waiter_task is first
        with _drive_task(sched, receiver):
            assert _run_immediate(router.recv()) == (IPCStatus.COMPLETED, msg)
        with _drive_task(sched, first):
            assert next(send) == (ChannelAction.BLOCK, None)
    with _drive_task(sched, receiver):
        assert msg.get(1) == 101
        assert _run_immediate(router.reply(msg, 0)) == IPCStatus.COMPLETED
    with _drive_task(sched, first):
        assert _run_immediate(send) == (IPCStatus.COMPLETED, msg)
    with _drive_task(sched, second):
        assert next(router.send(channel, second_msg)) == (ChannelAction.BLOCK, None)
    assert channel.waiter_task is second


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
