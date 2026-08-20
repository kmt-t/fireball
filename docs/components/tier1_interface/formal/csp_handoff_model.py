"""
docs/components/tier1_interface/formal/csp_handoff_model.py
pyModelChecking による IPC CSP チャネル所有権移譲とバッファ安全性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, AF, EF, And, Not, Imply, AtomicProposition


def build_csp_handoff_model():
    """送信者(S)から受信者(R)への所有権移譲 CSP チャネル Kripke モデル"""
    S = [
        "s_sender_holds",
        "s_channel_busy",
        "s_receiver_holds",
        "s_idle"
    ]
    S0 = {"s_sender_holds"}
    R = [
        ("s_sender_holds", "s_channel_busy"),
        ("s_channel_busy", "s_receiver_holds"),
        ("s_receiver_holds", "s_idle"),
        ("s_idle", "s_sender_holds"),
    ]
    L = {
        "s_sender_holds": {"sender_owns"},
        "s_channel_busy": {"in_flight"},
        "s_receiver_holds": {"receiver_owns"},
        "s_idle": {"idle"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def verify():
    km = build_csp_handoff_model()
    initial_states = km.S0

    # 1. 排他所有権 (Exclusive Ownership): AG not (sender_owns and receiver_owns)
    phi_exclusive = AG(Not(And(AtomicProposition("sender_owns"), AtomicProposition("receiver_owns"))))
    sat_exclusive = modelcheck(km, phi_exclusive)
    is_exclusive_satisfied = initial_states.issubset(sat_exclusive)

    # 2. 確実な受取 (Liveness of Transfer): AG (in_flight -> AF receiver_owns)
    phi_liveness = AG(Imply(AtomicProposition("in_flight"), AF(AtomicProposition("receiver_owns"))))
    sat_liveness = modelcheck(km, phi_liveness)
    is_liveness_satisfied = initial_states.issubset(sat_liveness)

    all_passed = is_exclusive_satisfied and is_liveness_satisfied
    if all_passed:
        print("CSP Handoff Ownership Safety: PASS")
        return 0
    else:
        print("CSP Handoff Verification FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(verify())
