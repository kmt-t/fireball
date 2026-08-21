"""
docs/components/tier1_interface/formal/csp_handoff_model.py
pyModelChecking による IPC CSP チャネル所有権移譲と二重所有不在の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = ["components/tier1_interface/ipc_router.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    CSP チャネル所有権移譲モデル（Revoke/Grant による二重所有防止の証明・変異検査対応）
    - s_sender_holds: 送信者が所有 (sender_owns)
    - s_in_flight: 所有権剥奪・キュー搬送中 (in_flight)
    - s_receiver_holds: 受信者が所有権取得 (receiver_owns)
    - s_both_owns: 違反状態（二重所有の競合状態）
    - s_dropped: ドロップハンドラによる安全回収 (idle, dropped)
    """
    S = [
        "s_sender_holds",
        "s_in_flight",
        "s_receiver_holds",
        "s_both_owns",
        "s_dropped",
    ]
    S0 = {"s_sender_holds"}
    R = [
        # 正常フロー: 送信開始で Revoke して in_flight へ
        ("s_sender_holds", "s_in_flight"),
        # 受信者がデキューして Grant
        ("s_in_flight", "s_receiver_holds"),
        # 異常系: 受信者消滅でドロップハンドラ回収
        ("s_in_flight", "s_dropped"),
        # 受信者処理完了 ➔ 送信者へ
        ("s_receiver_holds", "s_sender_holds"),
        # ドロップ回収後 ➔ 送信者へ
        ("s_dropped", "s_sender_holds"),
        # 違反状態の自己ループ
        ("s_both_owns", "s_both_owns"),
    ]

    if not guards:
        # ガード無効時（変異検査）:
        # 送信時に Revoke によるアトミック剥奪を行わず、受信者に直接 Grant すると二重所有が発生
        R = R + [("s_sender_holds", "s_both_owns")]

    L = {
        "s_sender_holds": {"sender_owns"},
        "s_in_flight": {"in_flight"},
        "s_receiver_holds": {"receiver_owns"},
        "s_both_owns": {"sender_owns", "receiver_owns"},  # 違反状態
        "s_dropped": {"idle", "dropped"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad = And(AtomicProposition("sender_owns"), AtomicProposition("receiver_owns"))
    return [
        {
            "name": "double_ownership_freedom_proof",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": True,  # Revoke/Grant プロトコルにより二重所有状態は到達不能
        },
        {
            "name": "in_flight_resolves_definitively",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("in_flight"),
                    AF(Not(AtomicProposition("in_flight"))),
                )
            ),
            "expect": True,  # どの経路を通っても必ず in_flight 状態から離脱して解決する (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
