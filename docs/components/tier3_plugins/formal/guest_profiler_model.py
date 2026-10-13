"""CTL model for profile log encoding and event ordering."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["components/tier3_plugins/guest_profiler.md"]


def build_model(*, guards: bool = True) -> Kripke:
    states = ["idle", "enter_logged", "exit_logged", "complete", "bad_encoding", "bad_order"]
    transitions = [
        ("idle", "enter_logged"),
        ("enter_logged", "exit_logged"),
        ("exit_logged", "complete"),
        ("complete", "complete"),
        ("bad_encoding", "bad_encoding"),
        ("bad_order", "bad_order"),
    ]
    if not guards:
        transitions.extend((("idle", "bad_encoding"), ("idle", "bad_order")))
    labels = {
        "idle": {"idle"},
        "enter_logged": {"enter_id_0501", "three_arguments", "tick_u32"},
        "exit_logged": {"exit_id_0502", "three_arguments", "tick_u32"},
        "complete": {"complete"},
        "bad_encoding": {"encoding_error"},
        "bad_order": {"ordering_error"},
    }
    return Kripke(S=states, S0={"idle"}, R=transitions, L=labels)


def properties():
    return [
        {
            "name": "function_boundary_encoding_matches_dictionary_contract",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("encoding_error"))),
            "violation": AtomicProposition("encoding_error"),
            "expect": True,
        },
        {
            "name": "function_enter_exit_order_is_preserved",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("ordering_error"))),
            "violation": AtomicProposition("ordering_error"),
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
