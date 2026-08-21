"""
docs/components/tier2_runtime/formal/vsoc_state_model.py
pyModelChecking による vSoC 実行状態・Safepoint 応答性・Debugger 整合性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, AF, EF, And, Not, Imply, AtomicProposition


def build_vsoc_model():
    """vSoC 実行エンジン・割り込み Safepoint・デバッグフォールバックの Kripke モデル"""
    S = [
        "s_interpreter_run",
        "s_jit_run",
        "s_safepoint_check",
        "s_interrupt_handling",
        "s_debugger_paused"
    ]
    S0 = {"s_interpreter_run"}
    R = [
        ("s_interpreter_run", "s_jit_run"),
        ("s_jit_run", "s_safepoint_check"),
        ("s_safepoint_check", "s_jit_run"),
        ("s_safepoint_check", "s_interrupt_handling"),
        ("s_safepoint_check", "s_debugger_paused"),
        ("s_interrupt_handling", "s_interpreter_run"),
        ("s_debugger_paused", "s_interpreter_run"),
        ("s_interpreter_run", "s_safepoint_check"),
    ]
    L = {
        "s_interpreter_run": {"running", "interp_mode"},
        "s_jit_run": {"running", "jit_mode"},
        "s_safepoint_check": {"safepoint"},
        "s_interrupt_handling": {"handling_irq"},
        "s_debugger_paused": {"paused", "debug_safe"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def verify():
    km = build_vsoc_model()
    initial_states = km.S0

    # 1. 割り込み処理中とJIT直接実行の排他性: AG not (handling_irq and jit_mode)
    phi_safe = AG(Not(And(AtomicProposition("handling_irq"), AtomicProposition("jit_mode"))))
    sat_safe = modelcheck(km, phi_safe)
    is_safe_satisfied = initial_states.issubset(sat_safe)

    # 2. どの状態からでも必ず Safepoint に到達可能 (Liveness): AG EF safepoint
    phi_reach = AG(EF(AtomicProposition("safepoint")))
    sat_reach = modelcheck(km, phi_reach)
    is_reach_satisfied = initial_states.issubset(sat_reach)

    all_passed = is_safe_satisfied and is_reach_satisfied
    if all_passed:
        print("vSoC State Machine & Safepoint Safety: PASS")
        return 0
    else:
        print("vSoC State Machine Verification FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(verify())
