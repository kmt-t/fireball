"""
docs/components/tier2_runtime/formal/runtime_memory_model.py
メモリマネージャの仮想予約スロット所有権分離・境界安全性を
pyModelChecking の有限 Kripke 構造で検証するモデル。

guards=False では、所有者検査・ページ単位の分離を無効化した反例経路を追加し、各不変条件の変異検査を行う。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["components/tier2_runtime/runtime_memory.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """ページ所有権とアクセス境界の安全性を検証する有限モデル。"""
    states = [
        "s_unallocated",
        "s_owned_a",
        "s_in_flight",
        "s_owned_b",
        "s_released",
        "s_wrong_owner_access",
        "s_mixed_page",
    ]
    initial = {"s_unallocated"}
    transitions = [
        ("s_unallocated", "s_owned_a"),
        # A page may be allocated directly to either task; this explicit
        # nondeterminism keeps the model honest about allocation interleavings.
        ("s_unallocated", "s_owned_b"),
        ("s_owned_a", "s_owned_a"),
        ("s_owned_a", "s_in_flight"),
        # CSP rendezvous does not imply peer arrival; indefinite waiting is valid.
        ("s_in_flight", "s_in_flight"),
        ("s_in_flight", "s_owned_b"),
        ("s_owned_b", "s_released"),
        ("s_released", "s_unallocated"),
        ("s_wrong_owner_access", "s_wrong_owner_access"),
        ("s_mixed_page", "s_mixed_page"),
    ]
    if not guards:
        # 各ガードを外した場合に、対応する性質を破る経路を追加する。
        transitions = [
            *transitions,
            ("s_owned_a", "s_wrong_owner_access"),
            ("s_owned_a", "s_mixed_page"),
        ]

    labels = {
        "s_unallocated": {"unallocated"},
        "s_owned_a": {"owner_a"},
        "s_in_flight": {"transfer_pending"},
        "s_owned_b": {"owner_b", "transfer_complete"},
        "s_released": {"released"},
        "s_wrong_owner_access": {"wrong_owner_access"},
        "s_mixed_page": {"mixed_page"},
    }
    return Kripke(S=states, S0=initial, R=transitions, L=labels)


def properties():
    """安全性2件を返す。転送完了は相手到達を仮定する上位契約である。"""
    wrong_owner_access = AtomicProposition("wrong_owner_access")
    mixed_page = AtomicProposition("mixed_page")
    return [
        {
            "name": "non_owner_access_traps",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(wrong_owner_access)),
            "violation": wrong_owner_access,
            "expect": True,
        },
        {
            "name": "page_never_mixes_owners",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(mixed_page)),
            "violation": mixed_page,
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: Runtime Memory Model (guards=True) ===")
    model = build_model(guards=True)
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        passed = model.S0.issubset(result)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed!"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    print("=== Mutation Testing: Runtime Memory Model (guards=False) ===")
    mutated = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated, prop["formula"])
        refuted = not mutated.S0.issubset(result)
        assert refuted, f"Mutation for {prop['name']} was NOT detected!"
        print(f"  [PASS (Refuted as expected)] {prop['name']}")
