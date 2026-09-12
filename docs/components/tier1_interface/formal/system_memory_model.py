"""system_memory の抽象所有権・予算契約を検証するモデル。"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier1_interface/system_memory.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """共有ブロックの所有権移譲と5プール予算契約を構築する。"""
    states = [
        "s_owned_by_a",
        "s_owned_by_a_again",
        "s_in_flight",
        "s_owned_by_b",
        "s_rolled_back_to_a",
        "s_double_owned",
        "s_over_budget",
        "s_stuck_in_flight",
    ]
    initial_states = {"s_owned_by_a"}
    relations = [
        ("s_owned_by_a", "s_in_flight"),
        ("s_owned_by_a", "s_owned_by_a_again"),
        ("s_owned_by_a_again", "s_in_flight"),
        ("s_in_flight", "s_owned_by_b"),
        ("s_in_flight", "s_rolled_back_to_a"),
        ("s_owned_by_b", "s_owned_by_b"),
        ("s_rolled_back_to_a", "s_owned_by_a"),
        ("s_double_owned", "s_double_owned"),
        ("s_over_budget", "s_over_budget"),
        ("s_stuck_in_flight", "s_stuck_in_flight"),
    ]
    if not guards:
        relations = [
            *relations,
            ("s_owned_by_a", "s_double_owned"),
            ("s_owned_by_a_again", "s_over_budget"),
            ("s_in_flight", "s_stuck_in_flight"),
        ]

    labels = {
        "s_owned_by_a": {"owned"},
        "s_owned_by_a_again": {"owned"},
        "s_in_flight": {"in_flight"},
        "s_owned_by_b": {"owned"},
        "s_rolled_back_to_a": {"owned"},
        "s_double_owned": {"double_owned"},
        "s_over_budget": {"over_budget"},
        "s_stuck_in_flight": {"in_flight", "stuck"},
    }
    return Kripke(S=states, S0=initial_states, R=relations, L=labels)


def properties():
    """所有権・予算・移譲完了の契約特性を返す。"""
    return [
        {
            "name": "shared_block_never_has_double_ownership",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("double_owned"))),
            "violation": AtomicProposition("double_owned"),
            "expect": True,
        },
        {
            "name": "all_pools_stay_within_global_budget",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("over_budget"))),
            "violation": AtomicProposition("over_budget"),
            "expect": True,
        },
        {
            "name": "ownership_transfer_or_rollback_eventually_completes",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("in_flight"),
                    AF(Not(AtomicProposition("in_flight"))),
                )
            ),
            "violation": AtomicProposition("stuck"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== System Memory Formal Verification (guards=True) ===")
    model = build_model(guards=True)
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        holds = model.S0.issubset(result)
        assert holds == prop["expect"], prop["name"]
        print(f"[PASS] {prop['name']}")

    print("=== System Memory Mutation Testing (guards=False) ===")
    mutated = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated, prop["formula"])
        holds = mutated.S0.issubset(result)
        assert not holds, f"Mutation for {prop['name']} was not detected"
        print(f"[PASS (Refuted as expected)] {prop['name']}")
