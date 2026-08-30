"""

experiments/pysim/ipc_router.py



Fireball IPC Router & Zero-Copy Ownership Handoff.

- Stage 1: Static URI Lookup to Service Descriptor via FlatMapView (binary search)

- Stage 2: Bitmask Role-Based Access Control (RBAC)

- Stage 3: Zero-Copy Ownership Handoff (Revoke -> Enqueue -> Grant)

- Fault Recovery: Queue Full Rollback & Drop Handler on Target Fault

"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1] if "tests" in str(Path(__file__)) or "scenarios" in str(Path(__file__)) else Path(__file__).resolve().parent
_REPO_ROOT = _PYSIM_DIR.parents[1]

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms',
           _REPO_ROOT / 'docs' / 'components' / 'tier1_core' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier2_runtime' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_jit' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_platform' / 'concepts']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys

from pathlib import Path



from typing import Any

from system_containers import FlatMapView





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

        # Stage 1: Static registry (URI -> Service Descriptor)

        entries = sorted({

            "fireball://core/coos/0": {"role": "CORE_SERVICE", "channel_id": "ch_coos", "max_queue": 2},

            "fireball://hal/gpio/0": {"role": "PLATFORM_HAL", "channel_id": "ch_gpio", "max_queue": 2},

            "fireball://dbg/manager/0": {"role": "DEBUGGER", "channel_id": "ch_dbg", "max_queue": 1},

        }.items())

        self.registry = FlatMapView([uri for uri, _ in entries], [desc for _, desc in entries])



        # Stage 2: Role-based Access Control Matrix

        self.role_matrix: dict[tuple[str, str], bool] = {

            ("CLIENT_APP", "CORE_SERVICE"): True,

            ("CLIENT_APP", "PLATFORM_HAL"): True,

            ("CLIENT_APP", "DEBUGGER"): False,

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



    def route_message(self, sender_role: str, uri: str, message: IPCMessage) -> tuple[str, str]:

        """

        Executes the 3-stage IPC routing pipeline.

        Returns (status_code, detail_message).

        """

        assert message.ownership == OwnershipState.SENDER_OWNS, "Sender must own resource before routing"



        # Stage 1: URI Lookup (binary search over the sorted registry)

        entry = self.registry.find(uri)

        if not entry:

            return ("ERR_NOT_FOUND", f"URI not registered: {uri}")



        target_role = entry["role"]

        channel_id = entry["channel_id"]

        max_queue = entry["max_queue"]



        # Stage 2: Access Control Check

        allowed = self.role_matrix.get((sender_role, target_role), False)

        if not allowed:

            return ("ERR_PERMISSION_DENIED", f"Role {sender_role} not allowed to access {target_role}")



        # Stage 3: Zero-Copy Ownership Handoff

        target_queue = self.queues[channel_id]



        # Check queue capacity (Rollback on full)

        if len(target_queue) >= max_queue:

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

        assert message.ownership == OwnershipState.IN_FLIGHT, "Message must be in-flight before grant"



        # 3. Grant receiver ownership

        message.ownership = OwnershipState.RECEIVER_OWNS

        return message



    def trigger_drop_handler(self, channel_id: str) -> list[str]:

        """Fault Recovery: Target service was killed/faulted."""

        reclaimed_resources: list[str] = []

        queue = self.queues.get(channel_id, [])



        while queue:

            msg = queue.pop(0)

            msg.ownership = OwnershipState.RECLAIMED_BY_DROP

            reclaimed_resources.append(msg.resource_id)



        return reclaimed_resources
