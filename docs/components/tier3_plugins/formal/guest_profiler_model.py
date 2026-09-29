"""CTL model for profiler stack overflow and trap unwinding contracts."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier3_plugins/guest_profiler.md"]


def build_model(*, guards: bool = True) -> Kripke:
    states = [
        "idle",
        "parent_open",
        "overflowed_child_open",
        "overflowed_child_exit",
        "parent_closed",
        "trap_received",
        "trap_closed",
        "parent_closed_early",
        "trap_stuck",
    ]
    transitions = [
        ("idle", "parent_open"),
        ("parent_open", "overflowed_child_open"),
        ("parent_open", "parent_closed"),
        ("overflowed_child_open", "overflowed_child_exit"),
        ("overflowed_child_exit", "parent_open"),
        ("parent_open", "trap_received"),
        ("overflowed_child_open", "trap_received"),
        ("trap_received", "trap_closed"),
        ("parent_closed", "parent_closed"),
        ("trap_closed", "trap_closed"),
        ("parent_closed_early", "parent_closed_early"),
        ("trap_stuck", "trap_stuck"),
    ]
    if not guards:
        transitions.extend(
            [
                ("overflowed_child_exit", "parent_closed_early"),
                ("trap_received", "trap_stuck"),
            ]
        )
    labels = {
        "idle": {"idle"},
        "parent_open": {"parent_open"},
        "overflowed_child_open": {"overflowed_child_open"},
        "overflowed_child_exit": {"overflowed_child_exit"},
        "parent_closed": {"parent_closed"},
        "trap_received": {"trap_received"},
        "trap_closed": {"trap_closed", "stack_empty"},
        "parent_closed_early": {"parent_closed_early"},
        "trap_stuck": {"trap_received", "trap_stuck"},
    }
    return Kripke(S=states, S0={"idle"}, R=transitions, L=labels)


def properties():
    early_close = AtomicProposition("parent_closed_early")
    trap_received = AtomicProposition("trap_received")
    stack_empty = AtomicProposition("stack_empty")
    return [
        {
            "name": "overflowed_recursive_exit_preserves_parent_frame",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(early_close)),
            "violation": early_close,
            "expect": True,
        },
        {
            "name": "trap_eventually_closes_tracked_frames",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(trap_received, AF(stack_empty))),
            "violation": AtomicProposition("trap_stuck"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    normal_model = build_model()
    for prop in properties():
        result = modelcheck(normal_model, prop["formula"])
        passed = normal_model.S0.issubset(result)
        assert passed == prop["expect"], f"Property {prop['name']} failed"
        print(f"[PASS] Formal Verification: {prop['name']}")

    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        passed = mutated_model.S0.issubset(result)
        assert not passed, f"Mutation for {prop['name']} was not detected"
        print(f"[PASS] Mutation Testing: {prop['name']} (guards=False)")
