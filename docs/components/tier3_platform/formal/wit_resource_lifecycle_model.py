"""WIT resource lifecycle, WASI pollables, and COOS-delivered vIRQ properties."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier3_platform/interface_wit.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """Model poll readiness and eligible, accepted virtual-interrupt delivery.

    Accepted interrupts pass registration/signature checks and FIFO admission.
    Progress assumes a cooperative COOS boundary is reached. Invalid delivery
    requests may be dropped; neither FIFO overflow nor uncooperative tasks have
    a delivery guarantee in this abstraction.
    """
    states = [
        "s_idle",
        "s_resource_active",
        "s_op_call",
        "s_op_performed",
        "s_resource_dropped",
        "s_op_call_on_dropped",
        "s_op_rejected",
        "s_operation_pending",
        "s_pollable_ready",
        "s_interrupt_triggered",
        "s_interrupt_queued",
        "s_interrupt_rejected",
        "s_rejected_delivered",
        "s_coos_boundary",
        "s_virq_delivered",
        "s_op_performed_on_dropped",
        "s_notification_lost",
        "s_interrupt_as_pollable",
    ]
    initial = {"s_idle"}
    transitions = [
        ("s_idle", "s_resource_active"),
        ("s_resource_active", "s_op_call"),
        ("s_op_call", "s_op_performed"),
        ("s_op_performed", "s_resource_active"),
        ("s_resource_active", "s_resource_dropped"),
        ("s_resource_dropped", "s_op_call_on_dropped"),
        ("s_op_call_on_dropped", "s_op_rejected"),
        ("s_op_rejected", "s_op_rejected"),
        # WASI operation completion uses a generic pollable.
        ("s_idle", "s_operation_pending"),
        ("s_operation_pending", "s_pollable_ready"),
        ("s_pollable_ready", "s_idle"),
        # vIRQ follows ISR -> COOS FIFO -> cooperative boundary -> vSoC.
        ("s_idle", "s_interrupt_triggered"),
        ("s_interrupt_triggered", "s_interrupt_queued"),
        ("s_interrupt_triggered", "s_interrupt_rejected"),
        ("s_interrupt_rejected", "s_idle"),
        ("s_rejected_delivered", "s_rejected_delivered"),
        ("s_interrupt_queued", "s_coos_boundary"),
        ("s_coos_boundary", "s_virq_delivered"),
        ("s_virq_delivered", "s_idle"),
        # Mutation states are totalized with self-loops.
        ("s_op_performed_on_dropped", "s_op_performed_on_dropped"),
        ("s_notification_lost", "s_notification_lost"),
        ("s_interrupt_as_pollable", "s_interrupt_as_pollable"),
    ]
    if not guards:
        # Drop guard removal permits a use-after-unmap operation.
        transitions.append(("s_op_call_on_dropped", "s_op_performed_on_dropped"))
        # Missing queue delivery loses a triggered interrupt.
        transitions.append(("s_interrupt_queued", "s_notification_lost"))
        transitions.append(("s_interrupt_rejected", "s_rejected_delivered"))
        # A broken routing guard conflates vIRQ delivery with WASI pollable readiness.
        transitions.append(("s_interrupt_triggered", "s_interrupt_as_pollable"))

    labels = {
        "s_idle": {"idle"},
        "s_resource_active": {"active"},
        "s_op_call": {"active"},
        "s_op_performed": {"active"},
        "s_resource_dropped": {"dropped"},
        "s_op_call_on_dropped": {"dropped"},
        "s_op_rejected": {"rejected"},
        "s_operation_pending": {"operation_pending"},
        "s_pollable_ready": {"pollable_ready"},
        "s_interrupt_triggered": {"interrupt_triggered"},
        "s_interrupt_queued": {"interrupt_queued", "accepted_interrupt"},
        "s_interrupt_rejected": {"rejected_interrupt"},
        "s_rejected_delivered": {"rejected_interrupt_delivered"},
        "s_coos_boundary": {"coos_boundary"},
        "s_virq_delivered": {"virq_delivered"},
        "s_op_performed_on_dropped": {"op_on_dropped"},
        "s_notification_lost": {"lost"},
        "s_interrupt_as_pollable": {"interrupt_as_pollable"},
    }
    return Kripke(S=states, S0=initial, R=transitions, L=labels)


def properties():
    return [
        {
            "name": "resource_op_never_succeeds_after_drop",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("op_on_dropped"))),
            "violation": AtomicProposition("op_on_dropped"),
            "expect": True,
        },
        {
            "name": "accepted_interrupt_reaches_virq_after_coos_boundary",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("accepted_interrupt"),
                    AF(AtomicProposition("virq_delivered")),
                )
            ),
            "violation": AtomicProposition("lost"),
            "expect": True,
        },
        {
            "name": "rejected_interrupt_is_not_delivered",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("rejected_interrupt_delivered"))),
            "violation": AtomicProposition("rejected_interrupt_delivered"),
            "expect": True,
        },
        {
            "name": "virtual_interrupt_does_not_make_wasi_pollable_ready",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("interrupt_as_pollable"))),
            "violation": AtomicProposition("interrupt_as_pollable"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    model = build_model(guards=True)
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        passed = model.S0.issubset(result)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        assert not mutated_model.S0.issubset(result), (
            f"Mutation for {prop['name']} was not detected"
        )
        print(f"  [PASS (Refuted as expected)] {prop['name']}")
