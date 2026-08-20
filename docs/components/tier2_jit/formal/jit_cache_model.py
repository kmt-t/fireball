"""
docs/components/tier2_jit/formal/jit_cache_model.py
pyModelChecking による JIT 命令キャッシュフラッシュと実行安全性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, AF, EF, And, Not, Imply, AtomicProposition


def build_jit_cache_model():
    """JIT コンパイル -> キャッシュフラッシュ -> 実行の安全モデル"""
    S = [
        "s_uncompiled",
        "s_compiled_dirty",
        "s_cache_flushed",
        "s_executing"
    ]
    S0 = {"s_uncompiled"}
    R = [
        ("s_uncompiled", "s_compiled_dirty"),
        ("s_compiled_dirty", "s_cache_flushed"),
        ("s_cache_flushed", "s_executing"),
        ("s_executing", "s_uncompiled"),
    ]
    L = {
        "s_uncompiled": {"clean"},
        "s_compiled_dirty": {"dirty"},
        "s_cache_flushed": {"flushed"},
        "s_executing": {"safe_exec"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def verify():
    km = build_jit_cache_model()
    initial_states = km.S0

    # 1. ダーティ状態での直接実行禁止: AG not (dirty and safe_exec)
    phi_safe = AG(Not(And(AtomicProposition("dirty"), AtomicProposition("safe_exec"))))
    sat_safe = modelcheck(km, phi_safe)
    is_safe_satisfied = initial_states.issubset(sat_safe)

    # 2. フラッシュ後に必ず実行可能になること: AG (flushed -> EF safe_exec)
    phi_reach = AG(Imply(AtomicProposition("flushed"), EF(AtomicProposition("safe_exec"))))
    sat_reach = modelcheck(km, phi_reach)
    is_reach_satisfied = initial_states.issubset(sat_reach)

    all_passed = is_safe_satisfied and is_reach_satisfied
    if all_passed:
        print("JIT Cache Safety: PASS")
        return 0
    else:
        print("JIT Cache Safety Verification FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(verify())
