"""
experiments/pysim/tier1_core/ipc_router.py
Fireball IPC Router: URI/RBAC front-end over the CSP rendezvous engine.
- Stage 1: Static URI Lookup to Service Descriptor via ReadOnlyFlatMapView (binary search)
- Stage 2: Role-Based Access Control (RBAC)
- Stage 3: Bufferless synchronous CSP handoff (scheduler.Channel).
  - GOTCHA-IPCR-01: Duplicate send on a waiting channel triggers assertion error
    (no queue overflow error, as queue does not exist).
  - GOTCHA-IPCR-02: Preflight validation failure preserves sender ownership
    (never revoke ownership before target and permissions are verified).
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from enum import IntEnum

from logging_interface import Logger, LogLevel
from memory_interface import MemoryManager, SharedBlock
from scheduler import (
    Channel,
    ChannelAction,
    ChannelTransferMode,
    Scheduler,
    WaitDir,
)
from system_containers import ReadOnlyFlatMapView, StaticVector

LOG_EVT_IPC_RBAC_DENIED = 0x0201
LOG_EVT_IPC_UNKNOWN_URI = 0x0202
LOG_EVT_IPC_MSG_TOO_LARGE = 0x0203
LOG_EVT_IPC_INVALID_OWNERSHIP = 0x0204
LOG_EVT_IPC_CHANNEL_COLLISION = 0x0205


# ipc_router.md {3.3}: a message is a static, fixed-size buffer of at most 8
# kv_pair entries.
FB_CONF_ROUTER_MAX_KV_PAIRS = 8
IPC_RESPONSE_PENDING = 0xFFFF_FFFF

# Canonical HAL endpoint URIs. The registry and upper runtime layers import
# these constants instead of duplicating endpoint spelling.
FB_URI_HAL_UART = "fireball://hal/uart/0"
FB_URI_HAL_STDOUT = "fireball://hal/stdout/0"
FB_URI_HAL_TIMER = "fireball://hal/timer/0"
FB_URI_HAL_GPIO = "fireball://hal/gpio/0"
FB_URI_HAL_I2C = "fireball://hal/i2c/0"
FB_URI_HAL_SPI = "fireball://hal/spi/0"


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


def unpack_key32(key_32: int) -> tuple[int, int, int]:
    """
    Unpacks a 32-bit key into (scope_kind, data_type, key_id):
      - Bits 31..29 (3 bits): ScopeKind
      - Bits 28..24 (5 bits): DataType
      - Bits 23..0  (24 bits): Key Identifier
    """
    type_scope = (key_32 >> 24) & 0xFF
    scope_kind = (type_scope >> 5) & 0x7
    data_type = type_scope & 0x1F
    key_id = key_32 & 0xFFFFFF
    return (scope_kind, data_type, key_id)


class Role(IntEnum):
    """
    ipc_router.md §3.3 registry_entry's "セキュリティロール" is a bit flag,
    not a string -- a fixed, small enum, so RBAC/channel lookup tables below
    can be plain constexpr-style arrays indexed by role value instead of a
    hash map.

    Role identifies only the device kind.  The URI registry identifies the
    configured device instance; an instance ID is never encoded into this
    enum or into the RBAC matrix.
    """

    RUNTIME = 0
    CORE_SERVICE = 1
    HAL_UART = 2
    HAL_STDOUT = 3
    HAL_GPIO = 4
    HAL_TIMER = 5
    HAL_I2C = 6
    HAL_SPI = 7
    DEBUGGER = 8


class ServiceDescriptor(tuple):
    """registry_entry: device kind plus its configured instance ID. The
    listening channel is not a single fixed ID here -- each (sender_role,
    this role) edge of the RBAC DAG gets its own dedicated CSP channel (see
    _EDGE_CHANNEL_NAMES), since a Channel is a strict 1:1 pairing."""

    def __new__(cls, role: Role, device_id: int = 0):
        assert device_id >= 0
        return super().__new__(cls, (role, device_id))

    @property
    def role(self) -> Role:
        return self[0]

    @property
    def device_id(self) -> int:
        return self[1]


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
        block: SharedBlock | None = None,
        *,
        memory_manager: MemoryManager | None = None,
    ):
        self.ownership = OwnershipState.SENDER_OWNS
        self._block: SharedBlock | None = block
        self._memory_manager = memory_manager
        self._in_flight_shm_id: int | None = None
        self._in_flight_resource_ids: StaticVector[int] = StaticVector(
            capacity=FB_CONF_ROUTER_MAX_KV_PAIRS
        )
        self._sender_id = IPC_RESPONSE_PENDING
        self._response_code = IPC_RESPONSE_PENDING
        self._reply_channel: Channel | None = None

    def stamp_sender(self, sender_id: int) -> None:
        """Stamp the scheduler TCB and revoke all sender SHM access."""
        assert sender_id > 0, "IPC sender task IDs must be positive"
        assert self.ownership == OwnershipState.SENDER_OWNS
        assert self._reply_channel is None, "message is still bound to a reply channel"
        self._prepare_shared_transfer()
        self._sender_id = sender_id
        self._response_code = IPC_RESPONSE_PENDING
        self.ownership = OwnershipState.IN_FLIGHT

    def prepare_reply(self) -> None:
        """Revoke receiver SHM access before the scheduler returns the reply."""
        assert self.ownership == OwnershipState.RECEIVER_OWNS
        self._prepare_shared_transfer()
        self.ownership = OwnershipState.IN_FLIGHT

    def _prepare_shared_transfer(self) -> None:
        """Move the message and embedded resource pages into scheduler flight."""
        assert self._memory_manager is not None, "IPC transfer requires a memory manager"
        entries = self.entries
        self._in_flight_resource_ids.clear()
        if self._block is not None:
            released_shm_id = self._block.release()
            assert released_shm_id >= 0, "message SharedBlock must belong to current task"
            self._in_flight_shm_id = released_shm_id
        for key, value in entries:
            scope, _, _ = unpack_key32(key)
            if scope == ScopeKind.RESOURCE and value >= 0:
                assert self._memory_manager.revoke_shared(value), (
                    "RESOURCE handle must be backed by an allocated SHM block"
                )
                self._in_flight_resource_ids.append(value)

    @property
    def sender_id(self) -> int:
        """Return the scheduler-authenticated sender TCB ID."""
        self._check_ownership()
        assert self._sender_id != IPC_RESPONSE_PENDING, "message has not been sent"
        return self._sender_id

    @property
    def response_code(self) -> int:
        """Return the receiver's response code after a reply is available."""
        self._check_ownership()
        return self._response_code

    def set_response_code(self, response_code: int) -> None:
        """Set a non-pending response code while the receiver owns the message."""
        self._check_ownership()
        assert self.ownership == OwnershipState.RECEIVER_OWNS, (
            "only the receiver may set an IPC response code"
        )
        assert 0 <= response_code < IPC_RESPONSE_PENDING
        self._response_code = response_code

    def _check_ownership(self) -> None:
        """Ensures the caller task/context currently holds ownership of the message."""
        assert (
            self.ownership == OwnershipState.SENDER_OWNS
            or self.ownership == OwnershipState.RECEIVER_OWNS
        ), f"Cannot access IPCMessage entries while ownership is {self.ownership.name}!"

    def move_to(self, new_owner: int) -> IPCMessage | None:
        """Grant message and embedded resource pages to the scheduler target TCB."""
        valid = self.ownership == OwnershipState.IN_FLIGHT and new_owner > 0
        assert valid
        if not valid:
            return None
        assert self._memory_manager is not None, "IPC transfer requires a memory manager"
        if self._in_flight_shm_id is not None:
            assert self._memory_manager.grant_shared(self._in_flight_shm_id)
            result = self._memory_manager.claim(self._in_flight_shm_id)
            assert not result.is_err, "message SharedBlock grant must be claimable"
            self._block = result.unwrap()
            self._in_flight_shm_id = None
        for shm_id in self._in_flight_resource_ids:
            assert self._memory_manager.grant_shared(shm_id)
        self._in_flight_resource_ids.clear()
        return self

    @property
    def block(self) -> SharedBlock | None:
        """Returns the RAII SharedBlock backing this message itself in shared memory."""
        self._check_ownership()
        return self._block

    @property
    def data(self) -> bytearray | None:
        self._check_ownership()
        return self._block.data if self._block is not None else None

    def _read_entries(self) -> StaticVector[tuple[int, int]]:
        self._check_ownership()
        if self._block is None or self._block.u64_capacity() < 1:
            return StaticVector(capacity=0)
        count = self._block.read_u64(0)
        count = min(count, FB_CONF_ROUTER_MAX_KV_PAIRS)
        res: StaticVector[tuple[int, int]] = StaticVector(capacity=FB_CONF_ROUTER_MAX_KV_PAIRS)
        for i in range(count):
            if i + 1 < self._block.u64_capacity():
                k, v = self._block.read_entry(i + 1)
                res.push_back((k, v))
        return res

    def write_entries(self, entries: Sequence[tuple[int, int]]) -> None:
        """Writes a batch of (key, value) pairs into the backing uint64_t shared memory array."""
        self._check_ownership()
        assert self._block is not None, "Cannot write entries without a backing SharedBlock"
        sorted_entries = sorted(entries, key=lambda e: e[0])
        self._block.write_u64(0, len(sorted_entries))
        for i, (k, v) in enumerate(sorted_entries):
            self._block.write_entry(i + 1, k, v)

    def append(self, key: int, value: int) -> None:
        """Appends an entry (key32, value32) into the shared memory block, keeping it sorted."""
        entries = self._read_entries()
        entries.append((key, value))
        self.write_entries(entries)

    @classmethod
    def from_entries(
        cls,
        entries: Sequence[tuple[int, int]] = (),
        *,
        memory_manager: MemoryManager,
    ) -> IPCMessage:
        """Allocate and populate a message for the scheduler's current task."""
        assert memory_manager is not None, (
            "IPCMessage.from_entries requires an injected memory manager; "
            "test callers must construct the adapter on the test side"
        )
        sb = memory_manager.allocate_shared(size=256).unwrap()
        msg = cls(sb, memory_manager=memory_manager)
        if entries:
            msg.write_entries(entries)
        return msg

    @property
    def entries(self) -> Sequence[tuple[int, int]]:
        """Returns the sorted AoS (key, value) entries read from the shared memory block."""
        return self._read_entries()

    @property
    def payload(self) -> ReadOnlyFlatMapView:
        self._check_ownership()
        return ReadOnlyFlatMapView(self._read_entries())

    @property
    def flat_map_view(self) -> ReadOnlyFlatMapView:
        """Returns the non-owning flat-map view for zero-copy lookup."""
        return self.payload

    def claim_resource(
        self,
        memory_manager: MemoryManager,
        key_id: int,
        scope_kind: int = ScopeKind.RESOURCE,
        data_type: int = DataType.UINT32,
    ) -> SharedBlock | None:
        """Looks up a shm_id from the message's entries and claims the SharedBlock."""
        self._check_ownership()
        shm_id = self.get_by_key_id(key_id, scope_kind=scope_kind, data_type=data_type)
        if shm_id is None:
            return None
        res = memory_manager.claim(shm_id)
        return res.unwrap() if not res.is_err else None

    def get_by_key_id(
        self,
        key_id: int,
        scope_kind: int = ScopeKind.FUNCTIONAL,
        data_type: int = DataType.UINT32,
    ) -> int | None:
        """Looks up a value by (scope_kind, data_type, key_id), i.e. pack_key32(...)."""
        self._check_ownership()
        target_k = pack_key32(scope_kind, data_type, key_id)
        return self.get(target_k)

    def get(self, key: int, default: int | None = None) -> int | None:
        """Retrieves a value via flat_map_view binary search over entries in the memory block."""
        self._check_ownership()
        value = self.payload.find(key)
        return default if value is None else value

    def __getitem__(self, key: int) -> int:
        val = self.get(key)
        assert val is not None, key
        return val

    def __contains__(self, key: int) -> bool:
        self._check_ownership()
        return self.payload.find(key) is not None

    def __len__(self) -> int:
        self._check_ownership()
        if self._block is None or self._block.u64_capacity() < 1:
            return 0
        return self._block.read_u64(0)


def bytes_to_kv_entries(data: bytes) -> StaticVector[tuple[int, int]]:
    """Packs arbitrary byte buffer into AoS (key32, val32) entries with length metadata."""
    entries: StaticVector[tuple[int, int]] = StaticVector(capacity=(len(data) + 3) // 4 + 1)
    entries.push_back((0, len(data)))
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        v = int.from_bytes(chunk, "little")
        entries.push_back((i // 4 + 1, v))
    return entries


def bytes_to_kv_storage(data: bytes) -> StaticVector[tuple[int, int]]:
    """Backward-compatible alias for bytes_to_kv_entries."""
    return bytes_to_kv_entries(data)


def kv_entries_to_bytes(entries: Sequence[tuple[int, int]], max_len: int | None = None) -> bytes:
    """Unpacks AoS (key32, val32) entries back into raw bytes using length metadata."""
    entries_view = ReadOnlyFlatMapView(entries)
    length_value = entries_view.find(0)
    total_len = 0 if length_value is None else length_value
    if max_len is not None:
        total_len = min(total_len, max_len)

    buf = bytearray()
    idx = 1
    while len(buf) < total_len:
        value = entries_view.find(idx)
        v = 0 if value is None else value
        chunk = v.to_bytes(4, "little")
        buf.extend(chunk)
        idx += 1
    return bytes(buf[:total_len])


# Static service table: ipc_router.md §3.1 -- a ROM-resident constexpr sorted
# array backing a flat_map_view<std::string_view, registry_entry>. URIs are
# used as the flat_map_view key directly (no hashing): std::string_view
# comparison is a bounded, allocation-free lexicographic compare.
#
# URI -> Role is many-to-one.  Role identifies only the device kind; the URI
# identifies the configured endpoint instance.
_SERVICE_ENTRIES: tuple[tuple[str, "ServiceDescriptor"], ...] = (
    ("fireball://core/coos/0", ServiceDescriptor(Role.CORE_SERVICE)),
    ("fireball://dbg/manager/0", ServiceDescriptor(Role.DEBUGGER)),
    (FB_URI_HAL_GPIO, ServiceDescriptor(Role.HAL_GPIO)),
    (FB_URI_HAL_I2C, ServiceDescriptor(Role.HAL_I2C)),
    (FB_URI_HAL_SPI, ServiceDescriptor(Role.HAL_SPI)),
    (FB_URI_HAL_STDOUT, ServiceDescriptor(Role.HAL_STDOUT)),
    (FB_URI_HAL_TIMER, ServiceDescriptor(Role.HAL_TIMER)),
    (FB_URI_HAL_UART, ServiceDescriptor(Role.HAL_UART)),
)

# ipc_router.md §4.1.1's FB_CONF_ROUTER_ROLE_MATRIX (9x9 constexpr array,
# rows = sender, columns = target); every DENY cell is listed explicitly, per
# the spec's own note that an absent cell must not be read as "undefined".
# Row/column order matches Role's declaration order. Every HAL_* role is a
# leaf (all-DENY row, never a sender), same as the single PLATFORM_HAL role
# it replaces; RUNTIME/CORE_SERVICE/DEBUGGER's former single "-> PLATFORM_HAL"
# ALLOW cell is now one ALLOW cell per HAL_* column, preserving the original
# permission semantics exactly.
_HAL_ROLES: tuple[Role, ...] = (
    Role.HAL_UART,
    Role.HAL_STDOUT,
    Role.HAL_GPIO,
    Role.HAL_TIMER,
    Role.HAL_I2C,
    Role.HAL_SPI,
)


def _role_row(allowed_targets: Sequence[Role]) -> StaticVector[bool]:
    row: StaticVector[bool] = StaticVector(capacity=len(Role))
    for role in Role:
        row.append(any(role == target for target in allowed_targets))
    return row


FB_CONF_ROUTER_ROLE_MATRIX: tuple[StaticVector[bool], ...] = (
    _role_row(
        (
            Role.CORE_SERVICE,
            Role.HAL_UART,
            Role.HAL_STDOUT,
            Role.HAL_GPIO,
            Role.HAL_TIMER,
            Role.HAL_I2C,
            Role.HAL_SPI,
        )
    ),  # from RUNTIME
    _role_row(_HAL_ROLES),  # from CORE_SERVICE
    _role_row(()),  # from HAL_UART (leaf)
    _role_row(()),  # from HAL_STDOUT (leaf)
    _role_row(()),  # from HAL_GPIO (leaf)
    _role_row(()),  # from HAL_TIMER (leaf)
    _role_row(()),  # from HAL_I2C (leaf)
    _role_row(()),  # from HAL_SPI (leaf)
    _role_row(
        (
            Role.CORE_SERVICE,
            Role.HAL_UART,
            Role.HAL_STDOUT,
            Role.HAL_GPIO,
            Role.HAL_TIMER,
            Role.HAL_I2C,
            Role.HAL_SPI,
        )
    ),  # from DEBUGGER
)


class IPCStatus(IntEnum):
    """
    Outcome of IPCRouter.send()/recv()/reply(). A successful send means that
    the request rendezvous and its response rendezvous both completed. The
    sender remains suspended between those two scheduler events.
    """

    COMPLETED = 0
    ERR_NOT_FOUND = 1
    ERR_PERMISSION_DENIED = 2
    ERR_MSG_TOO_LARGE = 3
    ERR_INVALID_OWNERSHIP = 4
    ERR_INVALID_RESPONSE = 5


class IPCRouter:
    """
    URI/RBAC front-end (Stage 1 + Stage 2) over the CSP rendezvous engine
    (Stage 3, delegated to scheduler.Channel via integer edge handles).
    """

    def __init__(
        self,
        scheduler: Scheduler,
        memory_manager: MemoryManager,
        logger: Logger | None = None,
    ):
        self.scheduler = scheduler
        self.logger = logger
        self.memory_manager = memory_manager
        # Non-owning view borrowing ROM-resident AoS storage array (_SERVICE_ENTRIES)
        self.registry = ReadOnlyFlatMapView(_SERVICE_ENTRIES)

        # Pre-allocate one dedicated CSP rendezvous channel per allowed
        # (sender role, destination URI) edge. Role is only the device kind;
        # the service handle keeps same-kind instances isolated.
        self._service_channels: StaticVector[StaticVector[Channel | None]] = StaticVector(
            capacity=len(_SERVICE_ENTRIES)
        )
        for _uri, descriptor in _SERVICE_ENTRIES:
            channels: StaticVector[Channel | None] = StaticVector(capacity=len(Role))
            for sender_role in Role:
                allowed = FB_CONF_ROUTER_ROLE_MATRIX[int(sender_role)][int(descriptor.role)]
                channels.append(
                    self.scheduler.create_channel(
                        transfer_mode=ChannelTransferMode.MOVABLE,
                        sender_stamper=IPCMessage.stamp_sender,
                        reply_stamper=IPCMessage.prepare_reply,
                        request_reply=True,
                    )
                    if allowed
                    else None
                )
            self._service_channels.append(channels)

    def lookup_service_handle(self, uri: str) -> int:
        """Resolves URI to integer service handle via ReadOnlyFlatMapView binary search (O(log N))."""
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
        """Return the first configured instance channel for compatibility with unit probes."""
        for service_handle, (_uri, descriptor) in enumerate(_SERVICE_ENTRIES):
            if descriptor.role == target_role:
                return self._service_channels[service_handle][int(sender_role)]
        return None

    def lookup(self, destination_uri: str) -> tuple[IPCStatus, Channel | None]:
        """
        Stage 1 URI lookup + Stage 2 RBAC authorization.
        Derives caller's security role strictly from current_task.role (preventing spoofing).
        Returns (IPCStatus.COMPLETED, Channel) if permitted, or (error_status, None).
        """
        current = self.scheduler.current_task
        assert current is not None, "IPC lookup requires an active scheduler task"
        sender_role = Role(current.role)

        handle = self.lookup_service_handle(destination_uri)
        desc = self.get_service_descriptor(handle)
        if desc is None:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.WARN, LOG_EVT_IPC_UNKNOWN_URI, handle if handle >= 0 else 0, 0, 0, 0
                )
            return (IPCStatus.ERR_NOT_FOUND, None)

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
            return (IPCStatus.ERR_PERMISSION_DENIED, None)

        return (IPCStatus.COMPLETED, self._service_channels[handle][int(sender_role)])

    def create_channel(
        self,
        destination_uri: str,
    ) -> Channel | None:
        """
        Backward-compatible helper: resolves URI and returns Channel if permitted.
        Role is always obtained from current_task.role.
        """
        status, ch = self.lookup(destination_uri)
        return ch if status == IPCStatus.COMPLETED else None

    def send(
        self, channel: Channel, message: IPCMessage
    ) -> Generator[tuple[ChannelAction, None], None, tuple[IPCStatus, IPCMessage | None]]:
        """
        Stage 3: synchronous CSP request/reply on the pre-authorized Channel.
        Zero-copy: `message` itself is never duplicated, only its `ownership`
        flag and reference move. The sender remains blocked until the receiver
        replies with the same message object.
        Caller role is verified against the channel's allowed edges.
        """
        current = self.scheduler.current_task
        assert current is not None, "IPC send requires an active scheduler task"
        sender_role = Role(current.role)
        channel_allowed = False
        for service_channels in self._service_channels:
            allowed_channel = service_channels[int(sender_role)]
            if allowed_channel is channel:
                channel_allowed = True
                break
        if not channel_allowed:
            if self.logger is not None:
                self.logger.log_event(
                    LogLevel.WARN,
                    LOG_EVT_IPC_RBAC_DENIED,
                    int(sender_role),
                    0,
                    0,
                    0,
                )
            return (
                IPCStatus.ERR_PERMISSION_DENIED,
                None,
            )

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
                IPCStatus.ERR_MSG_TOO_LARGE,
                None,
            )

        assert channel.waiter_dir != WaitDir.SEND, (
            "one waiter per channel: a second sender cannot overwrite a pending request"
        )
        assert channel.reply_sender_task is None and channel.reply_payload is None, (
            "one request/reply transaction per channel"
        )
        action, _ = channel.send(message)
        message._reply_channel = channel
        if action == ChannelAction.BLOCK:
            yield (ChannelAction.BLOCK, None)

        response_action, response = channel.wait_reply()
        if response_action == ChannelAction.BLOCK:
            yield (ChannelAction.BLOCK, None)
            response_action, response = channel.wait_reply()
        assert response_action != ChannelAction.BLOCK
        assert response is message, "CSP reply must return the original IPC message"
        message._reply_channel = None
        message.ownership = OwnershipState.SENDER_OWNS
        return (IPCStatus.COMPLETED, message)

    def recv(
        self,
    ) -> Generator[tuple[ChannelAction, None], None, tuple[IPCStatus, IPCMessage | None]]:
        """
        Guarded external choice (select) across every incoming Channel allowed
        for current running task's role.
        URI is never used in the hot transfer path.
        """
        receiver = self.scheduler.current_task
        assert receiver is not None, "IPC receive requires an active scheduler task"
        current_role = Role(receiver.role)

        channels: StaticVector[Channel] = StaticVector(capacity=len(_SERVICE_ENTRIES))
        if receiver.service_handle is not None:
            assert 0 <= receiver.service_handle < len(_SERVICE_ENTRIES)
            service_channels = self._service_channels[receiver.service_handle]
            for sender_role in Role:
                ch = service_channels[int(sender_role)]
                if ch is not None:
                    channels.append(ch)
        else:
            for service_handle, (_uri, descriptor) in enumerate(_SERVICE_ENTRIES):
                if descriptor.role != current_role:
                    continue
                service_channels = self._service_channels[service_handle]
                for sender_role in Role:
                    ch = service_channels[int(sender_role)]
                    if ch is not None:
                        channels.append(ch)
        if len(channels) == 0:
            return (IPCStatus.ERR_PERMISSION_DENIED, None)

        action, _ = self.scheduler.channel_select_recv(channels)
        if action == ChannelAction.BLOCK:
            yield (ChannelAction.BLOCK, None)
        message: IPCMessage = receiver.received_val
        receiver.received_val = None
        message.ownership = OwnershipState.RECEIVER_OWNS
        assert self.scheduler.get_task(message.sender_id) is not None, (
            "IPC sender TCB must remain registered during a request"
        )

        return (IPCStatus.COMPLETED, message)

    def reply(
        self, message: IPCMessage, response_code: int | None = None
    ) -> IPCStatus:
        """Return the received message, optionally extended, to its sender."""
        receiver = self.scheduler.current_task
        assert receiver is not None, "IPC reply requires an active scheduler task"
        assert message.ownership == OwnershipState.RECEIVER_OWNS
        assert message._reply_channel is not None, "message is not awaiting a reply"
        assert message.sender_id != receiver.task_id, "a task cannot reply to itself"
        if response_code is not None:
            message.set_response_code(response_code)
        assert message.response_code != IPC_RESPONSE_PENDING, (
            "receiver must set a response code before replying"
        )
        channel = message._reply_channel
        channel.reply(message)
        return IPCStatus.COMPLETED
