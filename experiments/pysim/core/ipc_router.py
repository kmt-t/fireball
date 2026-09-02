"""
experiments/pysim/ipc_router.py
Fireball IPC Router: URI/RBAC front-end over the CSP rendezvous engine.
- Stage 1: Static URI Lookup to Service Descriptor via FlatMapView (binary search)
- Stage 2: Role-Based Access Control (RBAC)
- Stage 3: Bufferless synchronous CSP handoff (scheduler.Channel), one dedicated
  channel per (sender_role, target_role) edge of the communication DAG --
  ownership transfer is atomic (Channel enforces single-waiter-per-direction),
  so there is no separate in-flight/queued state and nothing to roll back or
  drop-recover: a message that never completes a rendezvous never leaves its
  sender's hands, per ipc_router.md §5.1's distinction from a buffered mailbox.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import Any

from logger import (
    LOG_EVT_IPC_INVALID_OWNERSHIP,
    LOG_EVT_IPC_MSG_TOO_LARGE,
    LOG_EVT_IPC_RBAC_DENIED,
    LOG_EVT_IPC_UNKNOWN_URI,
    LogLevel,
)
from scheduler import Channel, ChannelAction, Scheduler
from system_containers import FlatMapStorage, FlatMapView

_EMPTY_IPC_STORAGE: FlatMapStorage = FlatMapStorage(())

# ipc_router.md {3.3}: a message is a static, fixed-size buffer of at most 8
# kv_pair entries.
FB_CONF_ROUTER_MAX_KV_PAIRS = 8


class ScopeKind(IntEnum):
    """上位3ビット：スコープ種別"""

    FUNCTIONAL = 0b000  # 機能的 (Functional) - メソッド呼び出しやコマンド指示
    DICTIONARY = 0b001  # 辞書参照 (Dictionary) - 静的オフセットによるログ参照
    RESOURCE = 0b010  # リソース (Resource) - ハードウェア記述子


class DataType(IntEnum):
    """下位5ビット：データ型"""

    VOID = 0b00000  # void / 未定義
    UINT32 = 0b00001  # uint32_t / 32ビット即値
    INT32 = 0b00010  # int32_t / 32ビット符号付き整数
    UINT16 = 0b00011  # uint16_t / 16ビット即値


def pack_key32(scope_kind: int, data_type: int, key_id: int) -> int:
    """
    Packs the upper 32 bits of a kv_pair (ipc_router.md §3.3):
      - Bits 31..24 (8 bits): Type Scope [ScopeKind: 3 bits | DataType: 5 bits]
      - Bits 23..0  (24 bits): Key Identifier (key_id)
    This 32-bit value is the flat_map_view search key for a message's KV map.
    """
    type_scope = ((scope_kind & 0x7) << 5) | (data_type & 0x1F)
    return ((type_scope & 0xFF) << 24) | (key_id & 0xFFFFFF)


class Role(IntEnum):
    """
    ipc_router.md §3.3 registry_entry's "セキュリティロール" is a bit flag,
    not a string -- a fixed, small enum, so RBAC/channel lookup tables below
    can be plain constexpr-style arrays indexed by role value instead of a
    hash map.
    """

    RUNTIME = 0
    CORE_SERVICE = 1
    PLATFORM_HAL = 2
    DEBUGGER = 3


class ServiceDescriptor(tuple):
    """registry_entry (ipc_router.md §3.3): a service's security role. The
    listening channel is not a single fixed ID here -- each (sender_role,
    this role) edge of the RBAC DAG gets its own dedicated CSP channel (see
    _EDGE_CHANNEL_NAMES), since a Channel is a strict 1:1 pairing."""

    def __new__(cls, role: Role):
        return super().__new__(cls, (role,))

    @property
    def role(self) -> Role:
        return self[0]


class OwnershipState(IntEnum):
    """
    Tracks who may touch this exact message object (ipc_router.md {IPC_ZeroCopy}):
    the object itself is never copied -- only this flag and the reference move
    from sender to receiver -- so once ownership leaves SENDER_OWNS, the
    sender's own binding to the message must not be used again.
    """

    SENDER_OWNS = 1
    IN_FLIGHT = 2
    RECEIVER_OWNS = 3


class IPCMessage:
    """
    Fireball IPC message: an owning container of a fixed-size (<= FB_CONF_ROUTER_MAX_KV_PAIRS)
    sorted array of kv_pair entries (32-bit key, 32-bit value -- see pack_key32),
    searched via flat_map_view. The message directly owns its FlatMapStorage.

    Accessing entries or payload requires that the current task holds ownership
    (SENDER_OWNS or RECEIVER_OWNS). Access during IN_FLIGHT is strictly prohibited.

    Bulk data across tasks must be passed via RAII SharedBlock (shared_block),
    encapsulating shm_id entirely per {ADR_SharedBlockRaii}.
    """

    def __init__(
        self,
        entries: Sequence[tuple[int, int]] | None = None,
        storage: FlatMapStorage | None = None,
        shared_block: Any | None = None,
    ):
        self.ownership = OwnershipState.SENDER_OWNS
        if storage is not None:
            self._storage: FlatMapStorage = storage
        elif entries is not None:
            self._storage = FlatMapStorage(sorted(entries, key=lambda e: e[0]))
        else:
            self._storage = _EMPTY_IPC_STORAGE

        self._shared_block: Any | None = shared_block
        self._in_flight_shm_id: int | None = None

    def _check_ownership(self) -> None:
        """Ensures the caller task/context currently holds ownership of the message."""
        assert self.ownership in (
            OwnershipState.SENDER_OWNS,
            OwnershipState.RECEIVER_OWNS,
        ), f"Cannot access IPCMessage entries while ownership is {self.ownership.name}!"

    @property
    def storage(self) -> FlatMapStorage:
        self._check_ownership()
        return self._storage

    @property
    def entries(self) -> Sequence[tuple[Any, Any]]:
        """Returns the sorted AoS (key, value) entries."""
        self._check_ownership()
        return self._storage.entries

    @property
    def payload(self) -> FlatMapView:
        self._check_ownership()
        return self._storage.view()

    @property
    def flat_map_view(self) -> FlatMapView:
        """Returns the non-owning FlatMapView for zero-copy binary search access."""
        return self.payload

    @property
    def shared_block(self) -> Any | None:
        """Returns the RAII SharedBlock if present, encapsulating shm_id."""
        self._check_ownership()
        return self._shared_block

    def get_by_key_id(
        self,
        key_id: int,
        scope_kind: int = ScopeKind.FUNCTIONAL,
        data_type: int = DataType.UINT32,
    ) -> int | None:
        """Looks up a value by (scope_kind, data_type, key_id), i.e. pack_key32(...)."""
        self._check_ownership()
        return self._storage.view().find(pack_key32(scope_kind, data_type, key_id))

    def get(self, key: int, default: Any = None) -> Any:
        """Retrieves a value via flat_map_view binary search."""
        self._check_ownership()
        val = self._storage.view().find(key)
        return default if val is None else val

    def __getitem__(self, key: int) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: int) -> bool:
        self._check_ownership()
        return key in self._storage.view()

    def __len__(self) -> int:
        self._check_ownership()
        return len(self._storage.entries)


def bytes_to_kv_storage(data: bytes) -> FlatMapStorage:
    """Packs arbitrary byte buffer into AoS (key32, val32) entries with length metadata."""
    entries = [(0, len(data))]
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        v = int.from_bytes(chunk, "little")
        entries.append((i // 4 + 1, v))
    return FlatMapStorage(sorted(entries, key=lambda kv: kv[0]))


def kv_entries_to_bytes(entries: Sequence[tuple[int, int]], max_len: int | None = None) -> bytes:
    """Unpacks AoS (key32, val32) entries back into raw bytes using length metadata."""
    entries_dict = dict(entries)
    total_len = entries_dict.get(0, 0)
    if max_len is not None:
        total_len = min(total_len, max_len)

    buf = bytearray()
    idx = 1
    while len(buf) < total_len:
        v = entries_dict.get(idx, 0)
        chunk = v.to_bytes(4, "little")
        buf.extend(chunk)
        idx += 1
    return bytes(buf[:total_len])


# Static service table: ipc_router.md §3.1 -- a ROM-resident constexpr sorted
# array backing a flat_map_view<std::string_view, registry_entry>. URIs are
# used as the flat_map_view key directly (no hashing): std::string_view
# comparison is a bounded, allocation-free lexicographic compare.
_SERVICE_TABLE: list[tuple[str, "ServiceDescriptor"]] = sorted(
    [
        ("fireball://core/coos/0", ServiceDescriptor(Role.CORE_SERVICE)),
        ("fireball://dbg/manager/0", ServiceDescriptor(Role.DEBUGGER)),
        ("fireball://device/gpio/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://device/i2c/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://device/spi/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://device/timer/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://device/uart/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://hal/gpio/0", ServiceDescriptor(Role.PLATFORM_HAL)),
        ("fireball://service/stdout/0", ServiceDescriptor(Role.PLATFORM_HAL)),
    ],
    key=lambda entry: entry[0],
)

# Static ROM array owning the service table entries as (URI, ServiceDescriptor) pairs (AoS)
_SERVICE_ENTRIES: tuple[tuple[str, "ServiceDescriptor"], ...] = tuple(_SERVICE_TABLE)

# ipc_router.md §4.1.1's FB_CONF_ROUTER_ROLE_MATRIX (4x4 constexpr array,
# rows = sender, columns = target); every DENY cell is listed explicitly, per
# the spec's own note that an absent cell must not be read as "undefined".
# Row/column order matches Role's declaration order.
FB_CONF_ROUTER_ROLE_MATRIX: tuple[tuple[bool, ...], ...] = (
    # to:  RUNTIME  CORE_SERVICE  PLATFORM_HAL  DEBUGGER
    (False, True, True, False),  # from RUNTIME
    (False, False, True, False),  # from CORE_SERVICE
    (False, False, False, False),  # from PLATFORM_HAL
    (False, True, True, False),  # from DEBUGGER
)


class IpcStatus(IntEnum):
    """
    Outcome of IPCRouter.send()/recv(). Blocking is never part of this
    vocabulary: send()/recv() are generators that wait out a CSP block
    internally (see their docstrings) and only ever finish with COMPLETED or
    one of Stage 1/2's own rejections -- a caller never has to ask "did this
    block" any more than a normal blocking syscall makes its caller ask that.
    """

    COMPLETED = 0
    ERR_NOT_FOUND = 1
    ERR_PERMISSION_DENIED = 2
    ERR_MSG_TOO_LARGE = 3


class IPCRouter:
    """
    URI/RBAC front-end (Stage 1 + Stage 2) over the CSP rendezvous engine
    (Stage 3, delegated to scheduler.Channel via integer edge handles).
    """

    def __init__(
        self,
        scheduler: Scheduler,
        logger: Any = None,
        memory_manager: Any | None = None,
    ):
        self.scheduler = scheduler
        self.logger = logger
        self.memory_manager = memory_manager
        # Non-owning view borrowing ROM-resident AoS storage array (_SERVICE_ENTRIES)
        self.registry = FlatMapView(_SERVICE_ENTRIES)

        # Pre-allocate one dedicated CSP rendezvous channel per allowed edge in the RBAC matrix
        self._edge_channels: tuple[tuple[Channel | None, ...], ...] = tuple(
            tuple(self.scheduler.create_channel() if allowed else None for allowed in row)
            for row in FB_CONF_ROUTER_ROLE_MATRIX
        )

    def lookup_service_handle(self, uri: str) -> int:
        """Resolves URI to integer service handle via FlatMapView binary search (O(log N))."""
        return self.registry.find_index(uri)

    def get_service_descriptor(self, service_handle: int) -> ServiceDescriptor | None:
        """O(1) direct ROM array lookup of service descriptor by handle."""
        if 0 <= service_handle < len(_SERVICE_ENTRIES):
            return _SERVICE_ENTRIES[service_handle][1]
        return None

    def find_service(self, uri: str) -> ServiceDescriptor | None:
        """Finds service descriptor via flat_map_view binary search over the URI string."""
        handle = self.lookup_service_handle(uri)
        return self.get_service_descriptor(handle)

    def channel_for_edge(self, sender_role: Role, target_role: Role) -> Channel | None:
        """The dedicated CSP Channel for one specific (sender_role, target_role) RBAC edge."""
        return self._edge_channels[int(sender_role)][int(target_role)]

    def create_channel(
        self,
        destination_uri: str,
        sender_role: Role | None = None,
    ) -> Channel | None:
        """
        Opens a communication channel to the destination service URI.
        Binds current running task's role (or explicit sender_role), performs
        Stage 1 URI lookup and Stage 2 RBAC authorization, and returns
        the dedicated Channel instance if permitted (or None if denied/not found).
        """
        if sender_role is None:
            current = self.scheduler.current_task
            sender_role = Role(current.role) if current is not None else Role.RUNTIME

        handle = self.lookup_service_handle(destination_uri)
        desc = self.get_service_descriptor(handle)
        if desc is None:
            return None

        if not FB_CONF_ROUTER_ROLE_MATRIX[int(sender_role)][int(desc.role)]:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.WARN,
                    LOG_EVT_IPC_RBAC_DENIED,
                    int(sender_role),
                    int(desc.role),
                    0,
                    0,
                )
            return None

        return self._edge_channels[int(sender_role)][int(desc.role)]

    def send(self, sender_role: Role, destination_uri: str, message: IPCMessage):
        """
        Stage 1 (lookup handle) + Stage 2 (RBAC check), then Stage 3: synchronous
        CSP send on the dedicated Channel.
        Zero-copy: `message` itself is never duplicated, only its `ownership`
        flag and reference move.
        """
        if message.ownership != OwnershipState.SENDER_OWNS:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.ERROR,
                    LOG_EVT_IPC_INVALID_OWNERSHIP,
                    int(message.ownership),
                    1,
                    0,
                    0,
                )
            assert message.ownership == OwnershipState.SENDER_OWNS, (
                "sender must own the message before sending"
            )
        if len(message) > FB_CONF_ROUTER_MAX_KV_PAIRS:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.ERROR,
                    LOG_EVT_IPC_MSG_TOO_LARGE,
                    len(message),
                    FB_CONF_ROUTER_MAX_KV_PAIRS,
                    0,
                    0,
                )
            return (
                IpcStatus.ERR_MSG_TOO_LARGE,
                f"message has {len(message)} KV pairs, exceeds {FB_CONF_ROUTER_MAX_KV_PAIRS}",
            )

        # 1. Resolve URI to integer handle via binary search (no dict!)
        handle = self.lookup_service_handle(destination_uri)
        desc = self.get_service_descriptor(handle)
        if desc is None:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.WARN, LOG_EVT_IPC_UNKNOWN_URI, handle if handle >= 0 else 0, 0, 0, 0
                )
            return (IpcStatus.ERR_NOT_FOUND, f"Destination {destination_uri} is not registered")

        # 2. Lookup authorized Channel for destination via create_channel
        channel = self.create_channel(destination_uri, sender_role=sender_role)
        if channel is None:
            return (
                IpcStatus.ERR_PERMISSION_DENIED,
                f"Role {sender_role.name} not allowed to access {desc.role.name}",
            )

        # 3. Synchronous CSP send directly on the Channel endpoint
        # Revoke phase: prepare SharedBlock for in-flight transfer
        if message._shared_block is not None:
            message._in_flight_shm_id = message._shared_block.release()
            message._shared_block = None

        message.ownership = OwnershipState.IN_FLIGHT
        action, target = channel.send(message)
        if action == ChannelAction.BLOCK:
            yield (ChannelAction.BLOCK, None)
        message.ownership = OwnershipState.RECEIVER_OWNS

        # Grant phase: update PTE ownership and claim receiver-side SharedBlock
        if message._in_flight_shm_id is not None and self.memory_manager is not None:
            recv_task = self.scheduler.get_task(target) if target is not None else None
            recv_task_id = recv_task.task_id if recv_task is not None else int(desc.role)
            self.memory_manager.grant_shared(message._in_flight_shm_id, recv_task_id)
            res = self.memory_manager.claim(recv_task_id, message._in_flight_shm_id)
            if not res.is_err:
                message._shared_block = res.unwrap()
            message._in_flight_shm_id = None
        return (IpcStatus.COMPLETED, target)

    def recv(self, service_uri: str):
        """
        Stage 1 in reverse: resolves service handle, then guarded external choice
        (select) across every incoming Channel allowed for that role.
        """
        handle = self.lookup_service_handle(service_uri)
        desc = self.get_service_descriptor(handle)
        if desc is None:
            return (IpcStatus.ERR_NOT_FOUND, None)

        channels = [ch for row in self._edge_channels if (ch := row[int(desc.role)]) is not None]
        if not channels:
            return (IpcStatus.ERR_PERMISSION_DENIED, None)

        receiver = self.scheduler.current_task
        action, target = self.scheduler.channel_select_recv(channels)
        if action == ChannelAction.BLOCK:
            yield (ChannelAction.BLOCK, None)
        message: IPCMessage = receiver.received_val
        receiver.received_val = None
        message.ownership = OwnershipState.RECEIVER_OWNS

        # Grant phase: if an in-flight SHM block is attached, bind receiver-side SharedBlock
        if message._in_flight_shm_id is not None and self.memory_manager is not None:
            self.memory_manager.grant_shared(message._in_flight_shm_id, receiver.task_id)
            res = self.memory_manager.claim(receiver.task_id, message._in_flight_shm_id)
            if not res.is_err:
                message._shared_block = res.unwrap()
            message._in_flight_shm_id = None
        return (IpcStatus.COMPLETED, message)
