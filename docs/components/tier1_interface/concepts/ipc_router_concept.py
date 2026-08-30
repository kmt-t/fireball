"""
docs/components/tier1_interface/concepts/ipc_router_concept.py
Reference Concept Implementation: IPC Router & Zero-Copy Ownership Handoff
- Stage 1: Static URI Lookup to Service Descriptor via fireball::flat_map_view
  (sorted-array + binary search, imported from flat_view_concept.py rather than
  reimplemented, so this cannot silently drift from the real container vocabulary)
- Stage 2: Bitmask Role-Based Access Control (RBAC)
- Stage 3: Zero-Copy Ownership Handoff (Revoke -> Enqueue -> Grant)
- Fault Recovery: Queue Full Rollback & Drop Handler on Target Fault
"""

import os
import sys
from typing import Any

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "tier1_core", "concepts"
    ),
)
from flat_view_concept import FlatMapView  # noqa: E402


class OwnershipState:
    SENDER_OWNS = "SENDER_OWNS"
    IN_FLIGHT = "IN_FLIGHT"
    RECEIVER_OWNS = "RECEIVER_OWNS"
    RECLAIMED_BY_DROP = "RECLAIMED_BY_DROP"


class IPCMessage:
    def __init__(self, resource_id: str, payload: dict[str, Any]):
        self.resource_id = resource_id
        self.payload = payload
        self.ownership = OwnershipState.SENDER_OWNS


class IPCRouter:
    def __init__(self):
        # Stage 1: Static registry (URI -> Service Descriptor), built once at
        # construction time as a sorted array and searched via FlatMapView's
        # binary search -- matching {LowLatencyLookup}/{META_FlatMapIndexed}'s
        # O(log N) claim for real, not just in the design prose.
        entries = sorted(
            {
                "fireball://core/coos/0": {
                    "role": "CORE_SERVICE",
                    "channel_id": "ch_coos",
                    "max_queue": 2,
                },
                "fireball://hal/gpio/0": {
                    "role": "PLATFORM_HAL",
                    "channel_id": "ch_gpio",
                    "max_queue": 2,
                },
                "fireball://dbg/manager/0": {
                    "role": "DEBUGGER",
                    "channel_id": "ch_dbg",
                    "max_queue": 1,
                },
            }.items()
        )
        self.registry = FlatMapView(
            [uri for uri, _ in entries], [desc for _, desc in entries]
        )

        # Stage 2: Role-based Access Control Matrix (sender_role, target_role) -> bool
        self.role_matrix: dict[tuple[str, str], bool] = {
            ("CLIENT_APP", "CORE_SERVICE"): True,
            ("CLIENT_APP", "PLATFORM_HAL"): True,
            (
                "CLIENT_APP",
                "DEBUGGER",
            ): False,  # Client app cannot directly access debugger
            ("CORE_SERVICE", "PLATFORM_HAL"): True,
            ("DEBUGGER", "CORE_SERVICE"): True,
            ("DEBUGGER", "PLATFORM_HAL"): True,
        }

        # Target message queues (channel_id -> [IPCMessage])
        self.queues: dict[str, list[IPCMessage]] = {
            "ch_coos": [],
            "ch_gpio": [],
            "ch_dbg": [],
        }

    def route_message(
        self, sender_role: str, uri: str, message: IPCMessage
    ) -> tuple[str, str]:
        """
        Executes the 3-stage IPC routing pipeline.
        Returns (status_code, detail_message).
        """
        assert message.ownership == OwnershipState.SENDER_OWNS, (
            "Sender must own resource before routing"
        )

        # --- Stage 1: URI Lookup (binary search over the sorted registry) ---
        entry = self.registry.find(uri)
        if not entry:
            return ("ERR_NOT_FOUND", f"URI not registered: {uri}")

        target_role = entry["role"]
        channel_id = entry["channel_id"]
        max_queue = entry["max_queue"]

        # --- Stage 2: Access Control Check ---
        allowed = self.role_matrix.get((sender_role, target_role), False)
        if not allowed:
            return (
                "ERR_PERMISSION_DENIED",
                f"Role {sender_role} not allowed to access {target_role}",
            )

        # --- Stage 3: Zero-Copy Ownership Handoff ---
        target_queue = self.queues[channel_id]

        # Check queue capacity (Rollback on full)
        if len(target_queue) >= max_queue:
            # Rollback: restore ownership to sender immediately
            message.ownership = OwnershipState.SENDER_OWNS
            return ("ERR_QUEUE_FULL", "Queue full, rolled back to sender")

        # 1. Revoke sender ownership -> IN_FLIGHT
        message.ownership = OwnershipState.IN_FLIGHT

        # 2. Enqueue into target queue
        target_queue.append(message)

        return ("OK_ENQUEUED", f"Message in-flight on {channel_id}")

    def receive_message(self, channel_id: str) -> IPCMessage | None:
        """Target service dequeues message and acquires ownership (Grant)."""
        queue = self.queues.get(channel_id)
        if not queue:
            return None

        message = queue.pop(0)
        assert message.ownership == OwnershipState.IN_FLIGHT, (
            "Message must be in-flight before grant"
        )

        # 3. Grant receiver ownership
        message.ownership = OwnershipState.RECEIVER_OWNS
        return message

    def trigger_drop_handler(self, channel_id: str) -> list[str]:
        """
        Fault Recovery: Target service was killed/faulted.
        Drop handler forcibly reclaims all in-flight resources in the queue.
        """
        queue = self.queues.get(channel_id, [])
        reclaimed_ids = []

        while queue:
            msg = queue.pop(0)
            assert msg.ownership == OwnershipState.IN_FLIGHT, (
                "Only in-flight messages can be dropped"
            )
            msg.ownership = OwnershipState.RECLAIMED_BY_DROP
            reclaimed_ids.append(msg.resource_id)

        return reclaimed_ids


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_registry_is_a_real_flat_map_view_not_a_dict():
    """{LowLatencyLookup}/{META_FlatMapIndexed}: Stage 1 URI lookup must actually be the
    sorted-array + binary-search flat_map_view, not a plain dict wearing its name. This
    closes the gap ipc_router.md 4.3.1 used to admit as a known divergence."""
    router = IPCRouter()
    assert isinstance(router.registry, FlatMapView), (
        "registry must be a real FlatMapView so the O(log N) claim is backed by the actual mechanism"
    )
    assert not isinstance(router.registry, dict)

    # Binary search finds a registered URI...
    assert router.registry.find("fireball://hal/gpio/0")["channel_id"] == "ch_gpio"
    # ...and correctly reports absence for one that was never registered.
    assert router.registry.find("fireball://nonexistent/service/0") is None


def test_unregistered_uri_is_rejected():
    router = IPCRouter()
    msg = IPCMessage("shm_buf_x", {"cmd": "NOOP"})
    status, detail = router.route_message(
        "CLIENT_APP", "fireball://nonexistent/service/0", msg
    )
    assert status == "ERR_NOT_FOUND"
    assert msg.ownership == OwnershipState.SENDER_OWNS


def test_successful_zero_copy_handoff():
    router = IPCRouter()
    msg = IPCMessage("shm_buf_1", {"cmd": "SET_GPIO", "pin": 5, "val": 1})

    # Step 1: ClientApp routes message to HAL GPIO
    status, _ = router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg)
    assert status == "OK_ENQUEUED"
    assert msg.ownership == OwnershipState.IN_FLIGHT

    # Step 2: PlatformHAL receives message and acquires ownership
    received = router.receive_message("ch_gpio")
    assert received is not None
    assert received.resource_id == "shm_buf_1"
    assert received.ownership == OwnershipState.RECEIVER_OWNS


def test_permission_denied():
    router = IPCRouter()
    msg = IPCMessage("shm_buf_2", {"cmd": "READ_MEM"})

    # ClientApp trying to access Debugger directly (Forbidden)
    status, _ = router.route_message("CLIENT_APP", "fireball://dbg/manager/0", msg)
    assert status == "ERR_PERMISSION_DENIED"
    assert msg.ownership == OwnershipState.SENDER_OWNS  # Ownership not modified


def test_queue_full_rollback():
    router = IPCRouter()
    msg1 = IPCMessage("buf_1", {"d": 1})
    msg2 = IPCMessage("buf_2", {"d": 2})
    msg3 = IPCMessage("buf_3", {"d": 3})

    assert (
        router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg1)[0]
        == "OK_ENQUEUED"
    )
    assert (
        router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg2)[0]
        == "OK_ENQUEUED"
    )

    # 3rd message exceeds max_queue=2 -> Rollback
    status, _ = router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg3)
    assert status == "ERR_QUEUE_FULL"
    assert msg3.ownership == OwnershipState.SENDER_OWNS


def test_drop_handler_recovery():
    router = IPCRouter()
    msg = IPCMessage("shm_buf_leak_prevent", {"data": 999})
    router.route_message("CLIENT_APP", "fireball://hal/gpio/0", msg)
    assert msg.ownership == OwnershipState.IN_FLIGHT

    # Target service faults before dequeue -> Drop handler cleans up
    reclaimed = router.trigger_drop_handler("ch_gpio")
    assert reclaimed == ["shm_buf_leak_prevent"]
    assert msg.ownership == OwnershipState.RECLAIMED_BY_DROP


if __name__ == "__main__":
    test_registry_is_a_real_flat_map_view_not_a_dict()
    test_unregistered_uri_is_rejected()
    test_successful_zero_copy_handoff()
    test_permission_denied()
    test_queue_full_rollback()
    test_drop_handler_recovery()
    print("[PASS] All IPC Router concept tests passed successfully.")
