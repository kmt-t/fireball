"""
docs/components/tier1_interface/concepts/ipc_router_concept.py
Reference Concept Implementation: IPC Router & Zero-Copy Ownership Handoff
- Stage 1: Static URI Lookup to Service Role via fireball::flat_map_view
  (sorted-array + binary search, imported from flat_view_concept.py rather than
  reimplemented, so this cannot silently drift from the real container vocabulary)
- Stage 2: Role-Based Access Control (RBAC) via a 4x4 constexpr matrix
- Stage 3: Zero-Copy Ownership Handoff (Revoke -> CSP Rendezvous -> Grant)
  over a bufferless synchronous channel per RBAC edge ({ADR_RendezvousChannel})
  -- there is no bounded mailbox here, so no ERR_QUEUE_FULL/Rollback and no
  Drop Handler: a message that never completes a rendezvous never leaves its
  sender's hands (ipc_router.md §5.1's distinction from a buffered mailbox).
"""

import os
import sys
from collections.abc import Sequence
from enum import IntEnum
from typing import Any

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tier1_core", "concepts"),
)
from flat_view_concept import FlatMapView

_EMPTY_ENTRIES: list[tuple[Any, Any]] = []


class Role(IntEnum):
    RUNTIME = 0
    CORE_SERVICE = 1
    PLATFORM_HAL = 2
    DEBUGGER = 3


_ROLE_NAMES = ("RUNTIME", "CORE_SERVICE", "PLATFORM_HAL", "DEBUGGER")


class OwnershipState(IntEnum):
    SENDER_OWNS = 1
    IN_FLIGHT = 2
    RECEIVER_OWNS = 3


class IPCMessage:
    """A message owns its sorted (key, value) entries (AoS) and presents
    them via non-owning FlatMapView (ipc_router.md §3.3) -- no free-form dict payload."""

    def __init__(
        self,
        entries: Sequence[tuple[Any, Any]] | None = None,
    ):
        if entries is not None:
            self._entries = sorted(entries, key=lambda e: e[0])
        else:
            self._entries = _EMPTY_ENTRIES
        self.ownership = OwnershipState.SENDER_OWNS

    def _check_ownership(self) -> None:
        assert self.ownership in (
            OwnershipState.SENDER_OWNS,
            OwnershipState.RECEIVER_OWNS,
        ), f"Cannot access IPCMessage entries while ownership is {self.ownership.name}!"

    @property
    def entries(self) -> list[tuple[Any, Any]]:
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
        self._in_flight = None
        return message


# Stage 1: registry (URI -> role), a sorted array searched via flat_map_view --
# {LowLatencyLookup}/{META_FlatMapIndexed}'s O(log N) claim, backed for real.
_REGISTRY_ENTRIES = sorted(
    [
        ("fireball://core/coos/0", Role.CORE_SERVICE),
        ("fireball://hal/gpio/0", Role.PLATFORM_HAL),
        ("fireball://dbg/manager/0", Role.DEBUGGER),
    ]
)
_REGISTRY = FlatMapView(_REGISTRY_ENTRIES)

# Stage 2: FB_CONF_ROUTER_ROLE_MATRIX (4x4, rows=sender, cols=target); every
# DENY cell is listed explicitly, matching the C++ constexpr array exactly.
_ROLE_MATRIX = (
    (False, True, True, False),  # from RUNTIME
    (False, False, True, False),  # from CORE_SERVICE
    (False, False, False, False),  # from PLATFORM_HAL
    (False, True, True, False),  # from DEBUGGER
)


class IPCRouter:
    def __init__(self):
        # Stage 3: one dedicated CSP channel per ALLOW edge of the RBAC
        # matrix -- a Channel is a strict 1:1 pairing, so distinct senders
        # to the same target role cannot share one.
        self._channels: tuple[tuple["Channel | None", ...], ...] = tuple(
            tuple(Channel() if allowed else None for allowed in row) for row in _ROLE_MATRIX
        )

    def send(self, sender_role: int, uri: str, message: IPCMessage) -> tuple[str, str]:
        """
        3-stage IPC send: URI lookup -> RBAC -> CSP rendezvous handoff.
        Returns (status_code, detail_message).
        """
        assert message.ownership == OwnershipState.SENDER_OWNS, (
            "Sender must own the message before sending"
        )
        # --- Stage 1: URI Lookup (binary search over the sorted registry) ---
        target_role = _REGISTRY.find(uri)
        if target_role is None:
            return ("ERR_NOT_FOUND", f"URI not registered: {uri}")

        # --- Stage 2: Access Control Check ---
        channel = self._channels[sender_role][target_role]
        if channel is None:
            return (
                "ERR_PERMISSION_DENIED",
                f"Forbidden: {_ROLE_NAMES[sender_role]} -> {_ROLE_NAMES[target_role]}",
            )

        # --- Stage 3: Zero-Copy CSP Handoff ---
        # Revoke: commit to the handoff. No queue exists to be full, so this
        # cannot fail the way a bounded mailbox's Enqueue could.
        message.ownership = OwnershipState.IN_FLIGHT
        channel.send(message)
        return (
            "COMPLETED",
            f"{_ROLE_NAMES[sender_role]}->{_ROLE_NAMES[target_role]}: in-flight",
        )

    def receive(self, target_role: int) -> IPCMessage | None:
        """
        Guarded external choice (select): checks every ALLOW edge into
        target_role in order and returns the first one with a message
        ready, never committing to one sender_role upfront -- CORE_SERVICE,
        for example, may legitimately be sent to by both RUNTIME and
        DEBUGGER. Grant happens on whichever edge actually has a message.
        """
        for sender_role in range(len(_ROLE_MATRIX)):
            channel = self._channels[sender_role][target_role]
            if channel is None:
                continue
            message = channel.recv()
            if message is not None:
                message.ownership = OwnershipState.RECEIVER_OWNS
                return message
        return None


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_registry_is_a_real_flat_map_view_not_a_dict():
    """{LowLatencyLookup}/{META_FlatMapIndexed}: Stage 1 URI lookup must actually be the
    sorted-array + binary-search flat_map_view, not a plain dict wearing its name."""
    assert isinstance(_REGISTRY, FlatMapView), (
        "registry must be a real FlatMapView so the O(log N) claim is backed by the actual mechanism"
    )
    assert not isinstance(_REGISTRY, dict)
    assert _REGISTRY.find("fireball://hal/gpio/0") == Role.PLATFORM_HAL
    assert _REGISTRY.find("fireball://nonexistent/service/0") is None


def test_unregistered_uri_is_rejected():
    router = IPCRouter()
    msg = IPCMessage(entries=[(1, 42)])
    status, _ = router.send(Role.RUNTIME, "fireball://nonexistent/service/0", msg)
    assert status == "ERR_NOT_FOUND"
    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_permission_denied():
    router = IPCRouter()
    msg = IPCMessage(entries=[(1, 7)])
    # RUNTIME trying to access Debugger directly (Forbidden)
    status, _ = router.send(Role.RUNTIME, "fireball://dbg/manager/0", msg)
    assert status == "ERR_PERMISSION_DENIED"
    assert msg.ownership == OwnershipState.SENDER_OWNS  # Ownership not modified


def test_successful_zero_copy_handoff():
    router = IPCRouter()
    msg = IPCMessage(entries=[(1, 5)])
    # Step 1: RUNTIME sends to HAL GPIO. Revoke commits the send; Grant
    # only happens once the receiver actually calls receive().
    status, _ = router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg)
    assert status == "COMPLETED"
    assert msg.ownership == OwnershipState.IN_FLIGHT
    # Step 2: PlatformHAL receives message and acquires ownership (Grant)
    received = router.receive(Role.PLATFORM_HAL)
    assert received is msg
    assert received.ownership == OwnershipState.RECEIVER_OWNS


def test_receive_selects_whichever_allowed_sender_is_ready():
    """receive() must not commit to one sender_role upfront: CORE_SERVICE is
    reachable from both RUNTIME and DEBUGGER, and a receiver has to pick up
    whichever of them actually sent, in RBAC row order."""
    router = IPCRouter()
    msg = IPCMessage(entries=[(1, 42)])
    status, _ = router.send(Role.DEBUGGER, "fireball://core/coos/0", msg)
    assert status == "COMPLETED"
    received = router.receive(Role.CORE_SERVICE)
    assert received is msg
    assert received.ownership == OwnershipState.RECEIVER_OWNS
    # The RUNTIME->CORE_SERVICE edge was never touched, so it is still free.
    assert router.receive(Role.CORE_SERVICE) is None


def test_no_queue_full_state_exists():
    """Unlike a bounded mailbox, a CSP channel has no max_queue/ERR_QUEUE_FULL --
    a second send before the first is received is a programming error (one
    waiter per channel), not a recoverable Rollback condition."""
    router = IPCRouter()
    msg1 = IPCMessage(entries=[(1, 1)])
    router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg1)
    msg2 = IPCMessage(entries=[(1, 2)])
    raised = False
    try:
        router.send(Role.RUNTIME, "fireball://hal/gpio/0", msg2)
    except AssertionError:
        raised = True
    assert raised, (
        "a second concurrent sender on the same edge must be a hard error, not ERR_QUEUE_FULL"
    )


if __name__ == "__main__":
    test_registry_is_a_real_flat_map_view_not_a_dict()
    test_unregistered_uri_is_rejected()
    test_permission_denied()
    test_successful_zero_copy_handoff()
    test_receive_selects_whichever_allowed_sender_is_ready()
    test_no_queue_full_state_exists()
    print("[PASS] All IPC Router concept tests passed successfully.")
