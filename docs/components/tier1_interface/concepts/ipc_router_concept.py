"""
docs/components/tier1_interface/concepts/ipc_router_concept.py
Reference Concept Implementation: IPC Router & Zero-Copy Ownership Handoff
Implementation Invariants & Gotchas:
- GOTCHA-IPCR-01: Duplicate send on an already-waiting CSP channel triggers an assertion
  error, stopping illegal concurrent access (no queue exists in pure CSP).
- GOTCHA-IPCR-02: Preflight validation failure preserves sender ownership; permissions
and destination must be fully verified before revoking resource ownership.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from enum import IntEnum

from docs.components.tier1_core.concepts.flat_view_concept import FlatMapView

BACKS = ["components/tier1_interface/ipc_router.md"]

_EMPTY_ENTRIES: list[tuple[int, int]] = []
IPC_RESPONSE_PENDING = 0xFFFF_FFFF


class Role(IntEnum):
    """HAL_* roles identify device kinds; URI entries identify instances."""

    RUNTIME = 0
    CORE_SERVICE = 1
    HAL_UART = 2
    HAL_STDOUT = 3
    HAL_GPIO = 4
    HAL_TIMER = 5
    HAL_I2C = 6
    HAL_SPI = 7
    DEBUGGER = 8


_ROLE_NAMES = (
    "RUNTIME",
    "CORE_SERVICE",
    "HAL_UART",
    "HAL_STDOUT",
    "HAL_GPIO",
    "HAL_TIMER",
    "HAL_I2C",
    "HAL_SPI",
    "DEBUGGER",
)


class OwnershipState(IntEnum):
    SENDER_OWNS = 1
    IN_FLIGHT = 2
    RECEIVER_OWNS = 3


class IPCMessage:
    """A message owns its sorted (key, value) entries and presents
    them via non-owning FlatMapView (ipc_router.md §3.3) -- no free-form dict payload."""

    def __init__(
        self,
        entries: Sequence[tuple[int, int]] | None = None,
    ):
        if entries is not None:
            self._entries = sorted(entries, key=lambda e: e[0])
        else:
            self._entries = _EMPTY_ENTRIES
        self.ownership = OwnershipState.SENDER_OWNS
        self._sender_id = IPC_RESPONSE_PENDING
        self.response_code = IPC_RESPONSE_PENDING
        self.reply_channel: Channel | None = None

    def stamp_sender(self, sender_id: int) -> None:
        """The scheduler supplies the authenticated TCB identity."""
        assert sender_id > 0
        assert self.ownership == OwnershipState.SENDER_OWNS
        assert self.reply_channel is None
        self._sender_id = sender_id
        self.response_code = IPC_RESPONSE_PENDING
        self.ownership = OwnershipState.IN_FLIGHT

    @property
    def sender_id(self) -> int:
        self._check_ownership()
        assert self._sender_id != IPC_RESPONSE_PENDING
        return self._sender_id

    def set_response_code(self, response_code: int) -> None:
        self._check_ownership()
        assert self.ownership == OwnershipState.RECEIVER_OWNS
        assert 0 <= response_code < IPC_RESPONSE_PENDING
        self.response_code = response_code

    def _check_ownership(self) -> None:
        assert self.ownership in (
            OwnershipState.SENDER_OWNS,
            OwnershipState.RECEIVER_OWNS,
        ), f"Cannot access IPCMessage entries while ownership is {self.ownership.name}!"

    @property
    def entries(self) -> list[tuple[int, int]]:
        self._check_ownership()
        return self._entries

    @property
    def payload(self) -> FlatMapView:
        self._check_ownership()
        return FlatMapView(self._entries)

    def __len__(self) -> int:
        self._check_ownership()
        return len(self._entries)


class Channel:
    """
    Bufferless synchronous CSP rendezvous ({ADR_RendezvousChannel}): a
    single in-flight slot, never a bounded queue -- so there is no "queue
    full" state to roll back from. A real cooperative scheduler additionally
    suspends the caller here until the counterpart arrives; this concept
    stays a plain sequential demonstration of the rendezvous *result*, not
    the scheduler integration (see core/scheduler.py's Channel for that).
    """

    def __init__(self):
        self._in_flight: IPCMessage | None = None

    def send(self, message: IPCMessage) -> None:
        assert self._in_flight is None, (
            "one waiter per channel: a second sender must wait for the first handoff"
        )
        self._in_flight = message

    def recv(self) -> IPCMessage | None:
        message = self._in_flight
        if message is None or message.ownership != OwnershipState.IN_FLIGHT:
            return None
        return message

    def reply(self, message: IPCMessage) -> None:
        assert self._in_flight is message
        assert message.ownership == OwnershipState.RECEIVER_OWNS
        assert message.response_code != IPC_RESPONSE_PENDING
        message.ownership = OwnershipState.SENDER_OWNS
        self._in_flight = None


# Stage 1: registry (URI -> role), a sorted array searched via flat_map_view --
# {LowLatencyLookup}/{META_FlatMapIndexed}'s O(log N) claim, backed for real.
# URI -> Role is many-to-one, not 1:1: "fireball://hal/uart/0" and
# "fireball://hal/stdout/0" each keep their own dedicated Role/channel
# so two same-type instances never collide on one channel.
_REGISTRY_ENTRIES = sorted(
    [
        ("fireball://core/coos/0", Role.CORE_SERVICE),
        ("fireball://dbg/manager/0", Role.DEBUGGER),
        ("fireball://hal/gpio/0", Role.HAL_GPIO),
        ("fireball://hal/i2c/0", Role.HAL_I2C),
        ("fireball://hal/spi/0", Role.HAL_SPI),
        ("fireball://hal/timer/0", Role.HAL_TIMER),
        ("fireball://hal/uart/0", Role.HAL_UART),
        ("fireball://hal/uart/1", Role.HAL_UART),
        ("fireball://hal/stdout/0", Role.HAL_STDOUT),
    ]
)
_REGISTRY = FlatMapView(_REGISTRY_ENTRIES)

# Stage 2: FB_CONF_ROUTER_ROLE_MATRIX (9x9, rows=sender, cols=target); every
# DENY cell is listed explicitly, matching the C++ constexpr array exactly.
# Every HAL_* role is a leaf (all-DENY row) -- endpoint instances never
# initiate an IPC send themselves (ipc_router.md "全 DENY 行・列の意味").
_HAL_ROLES = (
    Role.HAL_UART,
    Role.HAL_STDOUT,
    Role.HAL_GPIO,
    Role.HAL_TIMER,
    Role.HAL_I2C,
    Role.HAL_SPI,
)


def _role_row(allowed_targets: frozenset) -> tuple:
    return tuple(role in allowed_targets for role in Role)


_ROLE_MATRIX = (
    _role_row(frozenset({Role.CORE_SERVICE, *_HAL_ROLES})),  # from RUNTIME
    _role_row(frozenset(_HAL_ROLES)),  # from CORE_SERVICE
    _role_row(frozenset()),  # from HAL_UART (leaf)
    _role_row(frozenset()),  # from HAL_STDOUT (leaf)
    _role_row(frozenset()),  # from HAL_GPIO (leaf)
    _role_row(frozenset()),  # from HAL_TIMER (leaf)
    _role_row(frozenset()),  # from HAL_I2C (leaf)
    _role_row(frozenset()),  # from HAL_SPI (leaf)
    _role_row(frozenset({Role.CORE_SERVICE, *_HAL_ROLES})),  # from DEBUGGER
)


class IPCRouter:
    def __init__(
        self,
        current_task_role: Role = Role.RUNTIME,
        current_task_id: int = 1,
        current_service_handle: int | None = None,
    ):
        self.current_task_role = current_task_role
        self.current_task_id = current_task_id
        self.current_service_handle = current_service_handle
        # Stage 3: one dedicated CSP channel per (sender role, service URI).
        # RBAC is role-based, while the channel carries the specific URI
        # instance identity so same-kind devices cannot cross-consume traffic.
        self._channels: tuple[tuple["Channel | None", ...], ...] = tuple(
            tuple(
                Channel() if _ROLE_MATRIX[sender_role][descriptor] else None
                for sender_role in range(len(Role))
            )
            for _uri, descriptor in _REGISTRY_ENTRIES
        )

    def service_handle(self, uri: str) -> int | None:
        """Resolve a URI to its stable index in the sorted service table."""
        index = bisect_left(_REGISTRY_ENTRIES, uri, key=lambda entry: entry[0])
        if index == len(_REGISTRY_ENTRIES) or _REGISTRY_ENTRIES[index][0] != uri:
            return None
        return index

    def lookup(self, uri: str) -> tuple[str, Channel | None]:
        """
        Stage 1 URI lookup + Stage 2 RBAC check.
        Role is strictly derived from caller's TCB context (current_task_role),
        preventing self-reported role spoofing. Returns pre-authorized Channel object.
        """
        service_handle = self.service_handle(uri)
        if service_handle is None:
            return ("ERR_NOT_FOUND", None)
        target_role = _REGISTRY_ENTRIES[service_handle][1]

        if not _ROLE_MATRIX[self.current_task_role][target_role]:
            return ("ERR_PERMISSION_DENIED", None)

        channel = self._channels[service_handle][self.current_task_role]
        assert channel is not None
        return ("COMPLETED", channel)

    def send(self, channel: Channel, message: IPCMessage) -> tuple[str, str]:
        """
        Stage 3: Zero-Copy CSP Handoff directly on pre-authorized Channel object.
        URI is eliminated from this hot transfer path. Caller's TCB role is verified.
        """
        allowed = any(
            service_channels[self.current_task_role] is channel
            for service_channels in self._channels
        )
        if not allowed:
            return ("ERR_PERMISSION_DENIED", "Role not authorized on this channel")

        assert message.ownership == OwnershipState.SENDER_OWNS, (
            "Sender must own the message before sending"
        )

        # The production scheduler stamps the TCB identity at the CSP send boundary.
        message.stamp_sender(self.current_task_id)
        channel.send(message)
        message.reply_channel = channel
        return ("COMPLETED", message)

    def receive(self) -> IPCMessage | None:
        """
        Guarded external choice (select): checks every ALLOW edge into
        current_task_role in order and returns the first one with a message
        ready, never committing to one sender_role upfront -- CORE_SERVICE,
        for example, may legitimately be sent to by both RUNTIME and
        DEBUGGER. Grant happens on whichever edge actually has a message.
        """
        target_role = self.current_task_role
        service_handles: range | tuple[int, ...]
        if self.current_service_handle is not None:
            assert 0 <= self.current_service_handle < len(_REGISTRY_ENTRIES)
            assert _REGISTRY_ENTRIES[self.current_service_handle][1] == target_role
            service_handles = (self.current_service_handle,)
        else:
            service_handles = tuple(
                handle
                for handle, (_uri, descriptor) in enumerate(_REGISTRY_ENTRIES)
                if descriptor == target_role
            )
        for service_handle in service_handles:
            for sender_role in range(len(Role)):
                channel = self._channels[service_handle][sender_role]
                if channel is None:
                    continue
                message = channel.recv()
                if message is not None:
                    message.ownership = OwnershipState.RECEIVER_OWNS
                    message.reply_channel = channel
                    return message
        return None

    def reply(self, message: IPCMessage, response_code: int) -> str:
        assert message.ownership == OwnershipState.RECEIVER_OWNS
        assert message.reply_channel is not None
        message.set_response_code(response_code)
        message.reply_channel.reply(message)
        message.reply_channel = None
        return "COMPLETED"


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_registry_is_a_real_flat_map_view_not_a_dict() -> None:
    """{LowLatencyLookup}/{META_FlatMapIndexed}: Stage 1 URI lookup must actually be the
    sorted-array + binary-search flat_map_view, not a plain dict wearing its name."""
    assert isinstance(_REGISTRY, FlatMapView), (
        "registry must be a real FlatMapView so the O(log N) claim is backed by the actual mechanism"
    )
    assert not isinstance(_REGISTRY, dict)
    assert _REGISTRY.find("fireball://hal/gpio/0") == Role.HAL_GPIO
    assert _REGISTRY.find("fireball://nonexistent/service/0") is None


def test_same_role_uri_instances_use_distinct_channels() -> None:
    """URI identity selects the channel; equal roles do not collapse instances."""
    router = IPCRouter(current_task_role=Role.RUNTIME)
    uri0 = "fireball://hal/uart/0"
    uri1 = "fireball://hal/uart/1"
    handle0 = router.service_handle(uri0)
    handle1 = router.service_handle(uri1)
    assert handle0 is not None and handle1 is not None and handle0 != handle1
    status0, channel0 = router.lookup(uri0)
    status1, channel1 = router.lookup(uri1)
    assert status0 == status1 == "COMPLETED"
    assert channel0 is not None and channel1 is not None and channel0 is not channel1

    message = IPCMessage(entries=[(1, 17)])
    assert router.send(channel0, message)[0] == "COMPLETED"
    router.current_task_role = Role.HAL_UART
    router.current_service_handle = handle1
    assert router.receive() is None
    assert message.ownership == OwnershipState.IN_FLIGHT

    router.current_service_handle = handle0
    assert router.receive() is message
    assert message.ownership == OwnershipState.RECEIVER_OWNS
    assert router.reply(message, response_code=0) == "COMPLETED"
    assert message.ownership == OwnershipState.SENDER_OWNS


def test_unregistered_uri_is_rejected() -> None:
    router = IPCRouter(current_task_role=Role.RUNTIME)
    msg = IPCMessage(entries=[(1, 42)])
    status, ch = router.lookup("fireball://nonexistent/service/0")
    assert status == "ERR_NOT_FOUND"
    assert ch is None
    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_permission_denied() -> None:
    router = IPCRouter(current_task_role=Role.RUNTIME)
    msg = IPCMessage(entries=[(1, 7)])
    # RUNTIME trying to access Debugger directly (Forbidden by RBAC)
    status, ch = router.lookup("fireball://dbg/manager/0")
    assert status == "ERR_PERMISSION_DENIED"
    assert ch is None
    assert msg.ownership == OwnershipState.SENDER_OWNS  # Ownership not modified

    # Spoofing attempt: HAL_GPIO cannot send even if holding an authorized channel from RUNTIME
    status_ok, ch_runtime = router.lookup("fireball://hal/gpio/0")
    assert status_ok == "COMPLETED" and ch_runtime is not None

    router.current_task_role = Role.HAL_GPIO
    status_spoof, _ = router.send(ch_runtime, msg)
    assert status_spoof == "ERR_PERMISSION_DENIED"
    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_successful_zero_copy_handoff() -> None:
    router = IPCRouter(current_task_role=Role.RUNTIME)
    msg = IPCMessage(entries=[(1, 5)])
    # Step 1: RUNTIME resolves destination to Channel and sends.
    status, ch = router.lookup("fireball://hal/gpio/0")
    assert status == "COMPLETED" and ch is not None

    send_status, _ = router.send(ch, msg)
    assert send_status == "COMPLETED"
    assert msg.ownership == OwnershipState.IN_FLIGHT

    # Step 2: HAL_GPIO receives message and acquires ownership (Grant)
    router.current_task_role = Role.HAL_GPIO
    received = router.receive()
    assert received is msg
    assert received.ownership == OwnershipState.RECEIVER_OWNS


def test_receive_selects_whichever_allowed_sender_is_ready() -> None:
    """receive() must not commit to one sender_role upfront: CORE_SERVICE is
    reachable from both RUNTIME and DEBUGGER, and a receiver has to pick up
    whichever of them actually sent, in RBAC row order."""
    router = IPCRouter(current_task_role=Role.DEBUGGER)
    msg = IPCMessage(entries=[(1, 42)])
    status, ch = router.lookup("fireball://core/coos/0")
    assert status == "COMPLETED" and ch is not None

    send_status, _ = router.send(ch, msg)
    assert send_status == "COMPLETED"

    router.current_task_role = Role.CORE_SERVICE
    received = router.receive()
    assert received is msg
    assert received.ownership == OwnershipState.RECEIVER_OWNS
    # The RUNTIME->CORE_SERVICE edge was never touched, so it is still free.
    assert router.receive() is None


def test_no_queue_full_state_exists() -> None:
    """Unlike a bounded mailbox, a CSP channel has no max_queue/ERR_QUEUE_FULL --
    a second send before the first is received is a programming error (one
    waiter per channel), not a recoverable Rollback condition."""
    router = IPCRouter(current_task_role=Role.RUNTIME)
    status, ch = router.lookup("fireball://hal/gpio/0")
    assert status == "COMPLETED" and ch is not None

    msg1 = IPCMessage(entries=[(1, 1)])
    router.send(ch, msg1)
    msg2 = IPCMessage(entries=[(1, 2)])
    raised = False
    try:
        router.send(ch, msg2)
    except AssertionError:
        raised = True
    assert raised, (
        "a second concurrent sender on the same edge must be a hard error, not ERR_QUEUE_FULL"
    )


if __name__ == "__main__":
    test_registry_is_a_real_flat_map_view_not_a_dict()
    test_unregistered_uri_is_rejected()
    test_permission_denied()
    test_same_role_uri_instances_use_distinct_channels()
    test_successful_zero_copy_handoff()
    test_receive_selects_whichever_allowed_sender_is_ready()
    test_no_queue_full_state_exists()
    print("[PASS] All IPC Router concept tests passed successfully.")
