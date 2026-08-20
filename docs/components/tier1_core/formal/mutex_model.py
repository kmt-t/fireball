"""
docs/components/tier1_core/formal/mutex_model.py
pyModelChecking による排他制御 (Mutex) とタスクスケジューリング安全性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, EF, And, Not, Imply, AtomicProposition


def build_mutex_kripke_model():
    """2プロセスの相互排除 (Mutex) を表す Kripke 構造を構築"""
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
    S0 = {"s_idle_idle"}
    R = [
        ("s_idle_idle", "s_wait_idle"),
        ("s_idle_wait", "s_wait_wait"),
        ("s_idle_crit", "s_wait_crit"),
        ("s_wait_idle", "s_crit_idle"),
        ("s_wait_wait", "s_crit_wait"),
        ("s_crit_idle", "s_idle_idle"),
        ("s_crit_wait", "s_idle_wait"),
        ("s_idle_idle", "s_idle_wait"),
        ("s_wait_idle", "s_wait_wait"),
        ("s_crit_idle", "s_crit_wait"),
        ("s_idle_wait", "s_idle_crit"),
        ("s_wait_wait", "s_wait_crit"),
        ("s_idle_crit", "s_idle_idle"),
        ("s_wait_crit", "s_wait_idle"),
    ]
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


def verify():
    km = build_mutex_kripke_model()
    initial_states = km.S0

    # 1. 相互排除 (Mutual Exclusion): AG not (p1_crit and p2_crit)
    phi_mutex = AG(Not(And(AtomicProposition("p1_crit"), AtomicProposition("p2_crit"))))
    sat_mutex = modelcheck(km, phi_mutex)
    is_mutex_satisfied = initial_states.issubset(sat_mutex)

    # 2. クリティカルセクションへの到達可能性 (Reachability): EF p1_crit
    phi_reach = EF(AtomicProposition("p1_crit"))
    sat_reach = modelcheck(km, phi_reach)
    is_reach_satisfied = initial_states.issubset(sat_reach)

    # 3. 待機状態からの進入可能性 (Progress): AG (p1_wait -> EF p1_crit)
    phi_progress = AG(Imply(AtomicProposition("p1_wait"), EF(AtomicProposition("p1_crit"))))
    sat_progress = modelcheck(km, phi_progress)
    is_progress_satisfied = initial_states.issubset(sat_progress)

    all_passed = is_mutex_satisfied and is_reach_satisfied and is_progress_satisfied
    if all_passed:
        print("Mutual Exclusion & Progress Safety: PASS")
        return 0
    else:
        print("Verification FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(verify())
