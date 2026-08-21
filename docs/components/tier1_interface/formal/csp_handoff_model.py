"""
docs/components/tier1_interface/formal/csp_handoff_model.py
pyModelChecking による IPC CSP チャネル所有権移譲とバッファ安全性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = ["components/tier1_interface/ipc_router.md"]


def build_model() -> Kripke:
    """
    CSP チャネル所有権移譲モデル（未保護時の二重所有レース到達可能性と回復）
    - s_sender_holds: 送信者が所有 (sender_owns)
    - s_in_flight: 所有権剥奪・キュー搬送中 (in_flight)
    - s_receiver_holds: 受信者が所有権取得 (receiver_owns)
    - s_both_owns: 二重所有の競合状態 (sender_owns, receiver_owns)
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
        # 送信開始: Revoke して in_flight へ
        ("s_sender_holds", "s_in_flight"),
        # 正常系: 受信者がデキューして Grant
        ("s_in_flight", "s_receiver_holds"),
        # レース系: 送信側が二重アクセスした場合の違反状態への遷移
        ("s_in_flight", "s_both_owns"),
        # 異常系: 受信者消滅でドロップハンドラ回収
        ("s_in_flight", "s_dropped"),
        # 受信者処理完了 ➔ 送信者へ
        ("s_receiver_holds", "s_sender_holds"),
        # 違反状態からの回復
        ("s_both_owns", "s_sender_holds"),
        # ドロップ回収後 ➔ 送信者へ
        ("s_dropped", "s_sender_holds"),
    ]
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
            "name": "double_ownership_race_detectable",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": False,  # 二重所有レースが検出可能であることを実証
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
    km = build_model()
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
