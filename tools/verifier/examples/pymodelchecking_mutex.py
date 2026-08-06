"""
examples/pymodelchecking_mutex.py
pyModelChecking ライブラリを使用した Kripke 構造定義と CTL モデル検査の例
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import (
    modelcheck,
    AG,
    EF,
    AF,
    And,
    Or,
    Not,
    Imply,
    AtomicProposition,
    Parser,
)


def build_mutex_kripke_model():
    """
    2プロセス (P1, P2) の相互排除 (Mutex) を表す Kripke 構造を構築
    """
    # 1. 状態集合 S
    S = [
        "s_idle_idle",
        "s_wait_idle",
        "s_crit_idle",
        "s_idle_wait",
        "s_wait_wait",
        "s_crit_wait",
        "s_idle_crit",
        "s_wait_crit",
    ]

    # 2. 初期状態 S0
    S0 = {"s_idle_idle"}

    # 3. 状態遷移関係 R (src_state, dst_state)
    R = [
        # --- Process 1 Transitions ---
        ("s_idle_idle", "s_wait_idle"),
        ("s_idle_wait", "s_wait_wait"),
        ("s_idle_crit", "s_wait_crit"),
        ("s_wait_idle", "s_crit_idle"),
        ("s_wait_wait", "s_crit_wait"),  # P1 acquires lock
        ("s_crit_idle", "s_idle_idle"),
        ("s_crit_wait", "s_idle_wait"),
        # --- Process 2 Transitions ---
        ("s_idle_idle", "s_idle_wait"),
        ("s_wait_idle", "s_wait_wait"),
        ("s_crit_idle", "s_crit_wait"),
        ("s_idle_wait", "s_idle_crit"),
        ("s_wait_wait", "s_wait_crit"),  # P2 acquires lock
        ("s_idle_crit", "s_idle_idle"),
        ("s_wait_crit", "s_wait_idle"),
    ]

    # 4. ラベリング関数 L (状態 -> 成立する原子命題/プロパティの集合)
    L = {
        "s_idle_idle": {"p1_idle", "p2_idle"},
        "s_wait_idle": {"p1_wait", "p2_idle"},
        "s_crit_idle": {"p1_crit", "p2_idle"},
        "s_idle_wait": {"p1_idle", "p2_wait"},
        "s_wait_wait": {"p1_wait", "p2_wait"},
        "s_crit_wait": {"p1_crit", "p2_wait"},
        "s_idle_crit": {"p1_idle", "p2_crit"},
        "s_wait_crit": {"p1_wait", "p2_crit"},
    }

    return Kripke(S=S, S0=S0, R=R, L=L)


def verify_with_pymodelchecking():
    km = build_mutex_kripke_model()
    initial_states = km.S0
    parser = Parser()

    print("=== pyModelChecking モデル検査開始 ===")

    # 1. 相互排除 (Mutual Exclusion) の検証: AG not (p1_crit and p2_crit)
    phi_mutex = AG(Not(And(AtomicProposition("p1_crit"), AtomicProposition("p2_crit"))))
    sat_mutex = modelcheck(km, phi_mutex)
    is_mutex_satisfied = initial_states.issubset(sat_mutex)
    print(f"[1] Mutual Exclusion ({phi_mutex}): {is_mutex_satisfied}")

    # 2. 到達可能性 (Reachability) の検証: EF p1_crit
    phi_reach = EF(AtomicProposition("p1_crit"))
    sat_reach = modelcheck(km, phi_reach)
    is_reach_satisfied = initial_states.issubset(sat_reach)
    print(f"[2] Reachability of P1 Critical ({phi_reach}): {is_reach_satisfied}")

    # 3. スターベーションフリー (Liveness) の検証: AG (p1_wait -> AF p1_crit)
    phi_liveness = AG(Imply(AtomicProposition("p1_wait"), AF(AtomicProposition("p1_crit"))))
    sat_liveness = modelcheck(km, phi_liveness)
    is_liveness_satisfied = initial_states.issubset(sat_liveness)
    print(f"[3] Liveness for P1 ({phi_liveness}): {is_liveness_satisfied}")

    return {
        "mutex": is_mutex_satisfied,
        "reachability": is_reach_satisfied,
        "liveness": is_liveness_satisfied,
    }


if __name__ == "__main__":
    verify_with_pymodelchecking()

