"""HAL抽象契約のIPCルーティングとハンドル転送を検証するモデル。"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["components/tier2_runtime/hal_dispatch.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """HAL要求の事前検査、IPC経路、完了または拒否の契約を構築する。"""
    states = [
        "s_request",
        "s_preflight",
        "s_ipc_routed",
        "s_completed",
        "s_rejected",
        "s_direct_access",
        "s_raw_pointer",
        "s_early_revoke",
    ]
    initial_states = {"s_request"}
    relations = [
        ("s_request", "s_preflight"),
        ("s_preflight", "s_ipc_routed"),
        ("s_preflight", "s_rejected"),
        ("s_ipc_routed", "s_completed"),
        ("s_completed", "s_request"),
        ("s_rejected", "s_request"),
        ("s_direct_access", "s_direct_access"),
        ("s_raw_pointer", "s_raw_pointer"),
        ("s_early_revoke", "s_early_revoke"),
    ]
    if not guards:
        relations = [
            *relations,
            ("s_preflight", "s_direct_access"),
            ("s_preflight", "s_raw_pointer"),
            ("s_preflight", "s_early_revoke"),
        ]

    labels = {
        "s_request": {"pending"},
        "s_preflight": {"pending"},
        "s_ipc_routed": {"ipc_routed"},
        "s_completed": {"completed"},
        "s_rejected": {"rejected"},
        "s_direct_access": {"direct_access"},
        "s_raw_pointer": {"raw_pointer"},
        "s_early_revoke": {"early_revoke"},
    }
    return Kripke(S=states, S0=initial_states, R=relations, L=labels)


def properties():
    """HAL抽象契約の安全性特性を返す。"""
    return [
        {
            "name": "device_access_never_bypasses_ipc_router",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("direct_access"))),
            "violation": AtomicProposition("direct_access"),
            "expect": True,
        },
        {
            "name": "data_transfer_never_uses_raw_pointer",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("raw_pointer"))),
            "violation": AtomicProposition("raw_pointer"),
            "expect": True,
        },
        {
            "name": "preflight_rejection_does_not_revoke_ownership",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("early_revoke"))),
            "violation": AtomicProposition("early_revoke"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== HAL Dispatch Formal Verification (guards=True) ===")
    model = build_model(guards=True)
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        holds = model.S0.issubset(result)
        assert holds == prop["expect"], prop["name"]
        print(f"[PASS] {prop['name']}")

    print("=== HAL Dispatch Mutation Testing (guards=False) ===")
    mutated = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated, prop["formula"])
        holds = mutated.S0.issubset(result)
        assert not holds, f"Mutation for {prop['name']} was not detected"
        print(f"[PASS (Refuted as expected)] {prop['name']}")
