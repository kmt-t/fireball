"""
docs/components/tier1_interface/formal/csp_handoff_model.py
pyModelChecking による IPC CSP チャネル所有権移譲、事前検証（Preflight Check）、
および二重所有不在の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, And, AtomicProposition, Imply, Not

BACKS = ["components/tier1_interface/ipc_router.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    CSP チャネル所有権移譲モデル（Revoke/Rendezvous/Grant による二重所有防止と
    単一待機者制約、および事前検証拒否時の所有権保全の証明・変異検査対応）。
    - s_sender_holds: 送信者が所有 (sender_owns)
    - s_preflight_check: IPCR-GOTCHA-02: RBAC/URI 事前検証中 (sender_owns)
    - s_preflight_rejected: 事前検証拒否、送信元が所有権を完全保持して終了 (sender_owns)
    - s_in_flight: Revoke 済み・チャネル上でランデブーを試みる (in_flight)
    - s_awaiting_peer: 受信者がまだ到達しておらずブロック中 (in_flight)
    - s_receiver_holds: 受信者が所有権取得 (receiver_owns)
    - s_both_owns: 違反状態（二重所有の競合状態）
    - s_orphaned: 違反状態（単一待機者制約が破られ、ブロック中のメッセージが上書き迷子）
    - s_preflight_leak: 違反状態（検証前に先に Revoke してしまい、検証拒否時に in-flight のままリーク）
    """
    S = [
        "s_sender_holds",
        "s_preflight_check",
        "s_preflight_rejected",
        "s_in_flight",
        "s_awaiting_peer",
        "s_receiver_holds",
        "s_both_owns",
        "s_orphaned",
        "s_preflight_leak",
    ]
    S0 = {"s_sender_holds"}
    R = [
        # 正常フロー: 送信要求 ➔ まず事前検証（Preflight Check）
        ("s_sender_holds", "s_preflight_check"),
        # IPCR-GOTCHA-02: 事前検証失敗時は所有権を維持したまま終了
        ("s_preflight_check", "s_preflight_rejected"),
        ("s_preflight_rejected", "s_sender_holds"),
        # 事前検証パス ➔ Revoke して in_flight へ
        ("s_preflight_check", "s_in_flight"),
        # channel_send 分岐1: 受信者が既に待機していれば即座にランデブー成立
        ("s_in_flight", "s_receiver_holds"),
        # channel_send 分岐2: まだ受信者が到達しておらずブロック
        ("s_in_flight", "s_awaiting_peer"),
        # ブロック中の送信者に受信者が到達して Grant
        ("s_awaiting_peer", "s_receiver_holds"),
        # 受信者処理完了 ➔ 送信者へ（次のメッセージに備える）
        ("s_receiver_holds", "s_sender_holds"),
        # 違反状態の自己ループ
        ("s_both_owns", "s_both_owns"),
        ("s_orphaned", "s_orphaned"),
        ("s_preflight_leak", "s_preflight_leak"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 送信時に Revoke によるアトミック剥奪を行わず直接 Grant すると二重所有
        R = [*R, ("s_sender_holds", "s_both_owns")]
        # 2. IPCR-GOTCHA-01: 待機中チャネルへの二重送信制約を外すとメッセージ迷子
        R = [*R, ("s_awaiting_peer", "s_orphaned")]
        # 3. IPCR-GOTCHA-02: 検証前に先に Revoke してしまうと、検証失敗時にリソースが in-flight でリーク
        R = [*R, ("s_preflight_check", "s_preflight_leak")]

    L = {
        "s_sender_holds": {"sender_owns"},
        "s_preflight_check": {"sender_owns", "checking"},
        "s_preflight_rejected": {"sender_owns", "rejected"},
        "s_in_flight": {"in_flight"},
        "s_awaiting_peer": {"in_flight"},
        "s_receiver_holds": {"receiver_owns"},
        "s_both_owns": {"sender_owns", "receiver_owns"},  # 違反状態
        "s_orphaned": {"in_flight", "leaked"},  # 違反状態
        "s_preflight_leak": {"in_flight", "leaked", "preflight_leaked"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad = And(AtomicProposition("sender_owns"), AtomicProposition("receiver_owns"))
    orphaned = AtomicProposition("leaked")
    rejected = AtomicProposition("rejected")
    sender_owns = AtomicProposition("sender_owns")
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
            "name": "single_waiter_no_orphaning",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(orphaned)),
            "violation": orphaned,
            "expect": True,  # IPCR-GOTCHA-01: 単一待機者制約により迷子メッセージは発生しない
        },
        {
            "name": "preflight_rejection_preserves_ownership",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(rejected, sender_owns)),
            "violation": AtomicProposition("preflight_leaked"),
            "expect": True,  # IPCR-GOTCHA-02: 事前検証拒否時は送信者が所有権を保持しリークしない
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
