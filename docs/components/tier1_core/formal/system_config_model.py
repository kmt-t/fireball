"""system_config の静的構成契約を検証する pyModelChecking モデル。

検証対象:
* コンパイル時に確定した構成値が実行時に変更されないこと
* 構成値が定義済みのリソース予算を超えないこと

``guards=False`` では、実行時上書きと予算超過の遷移を意図的に追加し、
安全性特性が反証されることを確認する。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["components/tier1_core/system_config.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """コンパイル時固定とリソース予算の構成契約モデルを構築する。"""
    states = [
        "s_boot",
        "s_configured_memory",
        "s_configured_ipc",
        "s_runtime_override",
        "s_over_budget",
    ]
    initial_states = {"s_boot"}
    relations = [
        ("s_boot", "s_configured_memory"),
        ("s_boot", "s_configured_ipc"),
        ("s_configured_memory", "s_configured_memory"),
        ("s_configured_ipc", "s_configured_ipc"),
        ("s_runtime_override", "s_runtime_override"),
        ("s_over_budget", "s_over_budget"),
    ]
    if not guards:
        relations = [
            *relations,
            ("s_configured_memory", "s_runtime_override"),
            ("s_configured_ipc", "s_runtime_override"),
            ("s_configured_memory", "s_over_budget"),
            ("s_configured_ipc", "s_over_budget"),
        ]

    labels = {
        "s_boot": {"compile_time"},
        "s_configured_memory": {"compile_time", "within_budget"},
        "s_configured_ipc": {"compile_time", "within_budget"},
        "s_runtime_override": {"runtime_override"},
        "s_over_budget": {"over_budget"},
    }
    return Kripke(S=states, S0=initial_states, R=relations, L=labels)


def properties():
    """構成契約の安全性特性を返す。"""
    return [
        {
            "name": "configuration_never_changes_at_runtime",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("runtime_override"))),
            "violation": AtomicProposition("runtime_override"),
            "expect": True,
        },
        {
            "name": "configured_resources_stay_within_budget",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("over_budget"))),
            "violation": AtomicProposition("over_budget"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== System Config Formal Verification (guards=True) ===")
    model = build_model(guards=True)
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        holds = model.S0.issubset(result)
        assert holds == prop["expect"], prop["name"]
        print(f"[PASS] {prop['name']}")

    print("=== System Config Formal Verification (guards=False) ===")
    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        holds = mutated_model.S0.issubset(result)
        assert not holds, f"Mutation for {prop['name']} was not detected"
        print(f"[PASS (Refuted as expected)] {prop['name']}")
