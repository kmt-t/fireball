"""
docs/components/tier1_core/formal/mutex_model.py
pyModelChecking による協調型排他制御 (COOS Mutex) の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, EF, And, Not, AtomicProposition

BACKS = [
    "components/tier1_core/os_coos.md",
    "components/tier1_core/os_scheduler.md",
    "components/tier1_core/system_config_details.md",
]


def build_model() -> Kripke:
    """
    COOS の協調型タスク実行と排他制御モデル。
    未保護実行での競合（違反状態 s_both_crit）が到達可能であることを示し、
    排他制御の反証可能性を監査可能にする。
    - s_idle: 両タスク待機
    - s_p1_crit: P1 がクリティカルセクション
    - s_p2_crit: P2 がクリティカルセクション
    - s_both_crit: 違反状態（両タスクが同時にクリティカルセクションに侵入）
    - s_wait: ロック待ち待機状態
    """
    S = [
        "s_idle",
        "s_p1_crit",
        "s_p2_crit",
        "s_both_crit",
        "s_wait",
    ]
    S0 = {"s_idle"}
    R = [
        ("s_idle", "s_p1_crit"),
        ("s_idle", "s_p2_crit"),
        ("s_idle", "s_wait"),
        ("s_p1_crit", "s_both_crit"),  # レース発生時の遷移
        ("s_p1_crit", "s_idle"),
        ("s_p2_crit", "s_idle"),
        ("s_p2_crit", "s_wait"),
        ("s_both_crit", "s_idle"),
        ("s_wait", "s_p1_crit"),
        ("s_wait", "s_idle"),
    ]
    L = {
        "s_idle": {"idle"},
        "s_p1_crit": {"p1_crit"},
        "s_p2_crit": {"p2_crit"},
        "s_both_crit": {"p1_crit", "p2_crit"},  # 違反状態
        "s_wait": {"waiting"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad = And(AtomicProposition("p1_crit"), AtomicProposition("p2_crit"))
    return [
        {
            "name": "unprotected_race_detected",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": False,  # 未保護モデルでは競合状態が検出されることを実証
        },
        {
            "name": "liveness_idle_reachable",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(EF(AtomicProposition("idle"))),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model()
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
