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
from enum import Enum, IntEnum
from typing import Any

from scheduler import Scheduler
from system_containers import FlatMapStorage, FlatMapView

_EMPTY_IPC_STORAGE: FlatMapStorage = FlatMapStorage((), ())

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
    FB_OFFSET = 0b00100  # fb_offset_t / ゲストメモリ相対オフセット


def pack_key32(scope_kind: int, data_type: int, key_id: int) -> int:
    """
    Packs the upper 32 bits of a kv_pair (ipc_router.md §3.3):
      - Bits 31..24 (8 bits): Type Scope [ScopeKind: 3 bits | DataType: 5 bits]
      - Bits 23..0  (24 bits): Key Identifier (key_id)
    This 32-bit value is the flat_map_view search key for a message's KV map.
    """
    type_scope = ((scope_kind & 0x7) << 5) | (data_type & 0x1F)
    return ((type_scope & 0xFF) << 24) | (key_id & 0xFFFFFF)


def pack_kv64(scope_kind: int, data_type: int, key_id: int, value_32: int) -> int:
    """
    Packs a full 64-bit kv_pair (key32 << 32 | value_32) per ipc_router.md §3.3.
    scope_kind, data_type, key_id, value_32 are all plain int (or an IntEnum,
    which behaves as one); this is an internal packing utility, not a system
    boundary, so it trusts its caller rather than inspecting argument types.
    """
    return (pack_key32(scope_kind, data_type, key_id) << 32) | (value_32 & 0xFFFFFFFF)


def unpack_kv64(kv64: int) -> tuple[int, int, int, int]:
    """
    Unpacks a 64-bit KV pair into (scope_kind, data_type, key_id, value_32).
    """
    type_scope = (kv64 >> 56) & 0xFF
    scope_kind = (type_scope >> 5) & 0x7
    data_type = type_scope & 0x1F
    key_id = (kv64 >> 32) & 0xFFFFFF
    value_32 = kv64 & 0xFFFFFFFF
    return scope_kind, data_type, key_id, value_32


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


class OwnershipState:
    """
    Tracks who may touch this exact message object (ipc_router.md {IPC_ZeroCopy}):
    the object itself is never copied -- only this flag and the reference move
    from sender to receiver -- so once ownership leaves SENDER_OWNS, the
    sender's own binding to the message must not be used again.
    """

    SENDER_OWNS = "SENDER_OWNS"
    IN_FLIGHT = "IN_FLIGHT"
    RECEIVER_OWNS = "RECEIVER_OWNS"


class IPCMessage:
    """
    Fireball IPC message: a fixed-size (<= FB_CONF_ROUTER_MAX_KV_PAIRS) sorted
    array of kv_pair entries (32-bit key, 32-bit value -- see pack_key32),
    searched via flat_map_view. This is the message's entire field set per
    ipc_router.md §3.3's "IPCメッセージ（message）" table (KV map only --
    no other field is specified there).

    A bulk byte body (e.g. a guest buffer) does not fit a 32-bit value slot at
    all -- a real implementation would pass it by shm-slice handle rather than
    inlining it, so it is carried here as a separate `raw_payload`, not as a
    kv_pair.
    """

    def __init__(
        self,
        storage: FlatMapStorage | None = None,
        raw_payload: bytes | None = None,
    ):
        self.ownership = OwnershipState.SENDER_OWNS
        self.storage: FlatMapStorage = storage if storage is not None else _EMPTY_IPC_STORAGE
        self.payload: FlatMapView = self.storage.view()
        self.raw_payload: bytes | None = bytes(raw_payload) if raw_payload is not None else None

    @property
    def entries(self) -> Sequence[tuple[Any, Any]]:
        """Returns the sorted AoS (key, value) entries."""
        return self.storage.entries

    @property
    def flat_map_view(self) -> FlatMapView:
        """Returns the non-owning FlatMapView for zero-copy binary search access."""
        return self.payload

    def get_by_key_id(
        self,
        key_id: int,
        scope_kind: int = ScopeKind.FUNCTIONAL,
        data_type: int = DataType.UINT32,
    ) -> int | None:
        """Looks up a value by (scope_kind, data_type, key_id), i.e. pack_key32(...)."""
        return self.payload.find(pack_key32(scope_kind, data_type, key_id))

    def get(self, key: int, default: Any = None) -> Any:
        """Retrieves a value via flat_map_view binary search."""
        val = self.payload.find(key)
        return default if val is None else val

    def __getitem__(self, key: int) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: int) -> bool:
        return key in self.payload

    def __len__(self) -> int:
        return len(self.storage.entries)


# Static service table: ipc_router.md §3.1 -- a ROM-resident constexpr sorted
# array backing a flat_map_view<std::string_view, registry_entry>. URIs are
# used as the flat_map_view key directly (no hashing): std::string_view
# comparison is a bounded, allocation-free lexicographic compare.
_SERVICE_TABLE: list[tuple[str, "ServiceDescriptor"]] = sorted(
    [
        ("fireball://core/coos/0", ServiceDescriptor(Role.CORE_SERVICE)),
        ("fireball://dbg/manager/0", ServiceDescriptor(Role.DEBUGGER)),
        ("fireball://hal/gpio/0", ServiceDescriptor(Role.PLATFORM_HAL)),
    ],
    key=lambda entry: entry[0],
)

# Static ROM arrays owning the service table entries ({META_BinarySearch})
_SERVICE_KEYS: tuple[str, ...] = tuple(uri for uri, _ in _SERVICE_TABLE)
_SERVICE_DESCS: tuple["ServiceDescriptor", ...] = tuple(desc for _, desc in _SERVICE_TABLE)

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

# Each ALLOW edge of that matrix gets its own dedicated CSP channel name (a
# Channel is a strict 1:1 pairing, so distinct senders to the same target
# cannot share one channel). Same 4x4 shape as the role matrix, indexed by
# Role; a DENY edge has no channel (None).
_EDGE_CHANNEL_NAMES: tuple[tuple[str | None, ...], ...] = tuple(
    tuple(
        f"ch_ipc_{Role(sender).name}_to_{Role(target).name}".lower() if allowed else None
        for target, allowed in enumerate(row)
    )
    for sender, row in enumerate(FB_CONF_ROUTER_ROLE_MATRIX)
)


class IpcStatus(Enum):
    """
    Outcome of IPCRouter.send()/recv(). Blocking is never part of this
    vocabulary: send()/recv() are generators that wait out a CSP block
    internally (see their docstrings) and only ever finish with COMPLETED or
    one of Stage 1/2's own rejections -- a caller never has to ask "did this
    block" any more than a normal blocking syscall makes its caller ask that.
    """

    COMPLETED = "COMPLETED"
    ERR_NOT_FOUND = "ERR_NOT_FOUND"
    ERR_PERMISSION_DENIED = "ERR_PERMISSION_DENIED"
    ERR_MSG_TOO_LARGE = "ERR_MSG_TOO_LARGE"


class IPCRouter:
    """
    URI/RBAC front-end (Stage 1 + Stage 2) over the CSP rendezvous engine
    (Stage 3, delegated to scheduler.Channel via _EDGE_CHANNEL_NAMES).
    """

    def __init__(self, scheduler: Scheduler):
        self.scheduler = scheduler
        # Non-owning view borrowing ROM-resident storage arrays (_SERVICE_KEYS, _SERVICE_DESCS)
        self.registry = FlatMapView(_SERVICE_KEYS, _SERVICE_DESCS)

    def find_service(self, uri: str) -> ServiceDescriptor | None:
        """Finds service descriptor via flat_map_view binary search over the URI string."""
        return self.registry.find(uri)

    def channel_id_for_edge(self, sender_role: Role, target_role: Role) -> str | None:
        """
        The dedicated CSP channel name for one specific (sender_role,
        target_role) RBAC edge, or None if that edge is DENY. recv() itself
        never needs this (it selects across every edge into its own role at
        once); it exists for callers that must address one exact edge, such
        as a test harness standing in for a single specific counterpart task.
        """
        return _EDGE_CHANNEL_NAMES[sender_role][target_role]

    def send(self, sender_role: Role, uri: str, message: IPCMessage):
        """
        Stage 1 (URI lookup) + Stage 2 (RBAC), then Stage 3: synchronous CSP
        send on the (sender_role, target_role) edge's dedicated channel.
        Zero-copy: `message` itself is never duplicated, only its `ownership`
        flag and the reference move -- the sender must not touch it again
        once this returns.

        A generator: if nobody is receiving yet, it `yield`s ("BLOCK", None)
        exactly once and, once resumed, the rendezvous has already completed
        -- callers only ever see the final (IpcStatus, target) via `return`.
        IPC is inter-*task* communication, so the caller is always a task:
        install this generator as `scheduler.current_task.coro` and run
        run_until_idle() until it terminates (see system.py's _ipc_send), or
        `yield from` it from within another task's own coroutine (see
        scenario9's client_app_task). There is no separate "blocked" status.
        """
        assert message.ownership == OwnershipState.SENDER_OWNS, (
            "sender must own the message before sending"
        )
        if len(message) > FB_CONF_ROUTER_MAX_KV_PAIRS:
            return (
                IpcStatus.ERR_MSG_TOO_LARGE,
                f"message has {len(message)} KV pairs, exceeds {FB_CONF_ROUTER_MAX_KV_PAIRS}",
            )

        desc = self.find_service(uri)
        if desc is None:
            return (IpcStatus.ERR_NOT_FOUND, f"URI {uri} is not registered")

        channel_id = _EDGE_CHANNEL_NAMES[sender_role][desc.role]
        if channel_id is None:
            return (
                IpcStatus.ERR_PERMISSION_DENIED,
                f"Role {sender_role.name} not allowed to access {desc.role.name}",
            )

        # Revoke sender access now: committed to the handoff whether or not a
        # receiver is already waiting on the other side.
        message.ownership = OwnershipState.IN_FLIGHT
        action, target = self.scheduler.channel_send(channel_id, message)
        if action == "BLOCK":
            yield ("BLOCK", None)
            # Resumed only once _handoff_or_yield() woke us, which only the
            # matching channel_recv() ever does for a blocked sender -- the
            # rendezvous is complete.
        message.ownership = OwnershipState.RECEIVER_OWNS
        return (IpcStatus.COMPLETED, target)

    def recv(self, uri: str):
        """
        Stage 1 in reverse (resolve this service's own role from its URI),
        then a guarded external choice (select) across every incoming RBAC
        edge that role allows -- receiving from whichever permitted sender
        arrives first, without committing to one sender_role upfront. This
        matters for real services: e.g. CORE_SERVICE may legitimately be
        sent to by both RUNTIME and DEBUGGER, and must not pin itself to
        just one of them to receive from the other. A generator like send():
        the only outcomes a caller ever observes are
        (IpcStatus.COMPLETED, message) or a Stage 1/2 rejection.
        """
        desc = self.find_service(uri)
        if desc is None:
            return (IpcStatus.ERR_NOT_FOUND, None)

        channel_ids = [edge for row in _EDGE_CHANNEL_NAMES if (edge := row[desc.role]) is not None]
        if not channel_ids:
            return (IpcStatus.ERR_PERMISSION_DENIED, None)

        receiver = self.scheduler.current_task
        action, target = self.scheduler.channel_select_recv(channel_ids)
        if action == "BLOCK":
            yield ("BLOCK", None)
        # By construction, send() only ever puts an IPCMessage into a channel.
        message: IPCMessage = receiver.received_val
        receiver.received_val = None
        message.ownership = OwnershipState.RECEIVER_OWNS
        return (IpcStatus.COMPLETED, message)
