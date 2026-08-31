"""
docs/components/tier1_interface/formal/csp_handoff_model.py
pyModelChecking による IPC CSP チャネル所有権移譲と二重所有不在の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not

BACKS = ["components/tier1_interface/ipc_router.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    CSP チャネル所有権移譲モデル（Revoke/Rendezvous/Grant による二重所有防止と
    単一待機者制約の証明・変異検査対応）。ipc_router.md はバッファなし同期
    CSP チャネル（`{ADR_RendezvousChannel}`）であり、キューを持たない——した
    がって「キュー満杯」も「Drop Handler によるキュー内滞留リソースの回収」
    も存在しない。ここでモデル化する違反状態は、CSP チャネル固有の危険であ
    る「1 チャネルにつき送信待機者は高々 1 つ」という制約が崩れた場合の
    危険（先に待機していた送信者のメッセージが後続の送信者に上書きされ、
    誰にも受信されないまま永久に迷子になること）に対応する。
    - s_sender_holds: 送信者が所有 (sender_owns)
    - s_in_flight: Revoke 済み・チャネル上でランデブーを試みる (in_flight)
    - s_awaiting_peer: 受信者がまだ到達しておらずブロック中（in_flight のまま
      継続）——channel_send の 2 通りの分岐（相手が既に待機していれば即座に
      Grant、まだなら協調スケジューラ上でブロック）のうち後者に対応する
    - s_receiver_holds: 受信者が所有権取得 (receiver_owns)
    - s_both_owns: 違反状態（二重所有の競合状態）
    - s_orphaned: 違反状態（単一待機者制約が破られ、ブロック中の送信者の
      in-flight メッセージが後続の送信者に上書きされ、誰にも受信されず
      永久に迷子になった状態）
    """
    S = [
        "s_sender_holds",
        "s_in_flight",
        "s_awaiting_peer",
        "s_receiver_holds",
        "s_both_owns",
        "s_orphaned",
    ]
    S0 = {"s_sender_holds"}
    R = [
        # 正常フロー: 送信開始で Revoke して in_flight へ（この時点で送信は完了確約）
        ("s_sender_holds", "s_in_flight"),
        # channel_send の分岐その1: 受信者が既に待機していれば即座にランデブー成立
        ("s_in_flight", "s_receiver_holds"),
        # channel_send の分岐その2: まだ受信者が到達しておらずブロックする
        ("s_in_flight", "s_awaiting_peer"),
        # ブロック中の送信者に受信者が到達しランデブーが成立して Grant
        # （相手タスクが有限時間内に到達するという公正性仮定の下で必ず到達する）
        ("s_awaiting_peer", "s_receiver_holds"),
        # 受信者処理完了 ➔ 送信者へ（次のメッセージに備える）
        ("s_receiver_holds", "s_sender_holds"),
        # 違反状態の自己ループ
        ("s_both_owns", "s_both_owns"),
        ("s_orphaned", "s_orphaned"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 送信時に Revoke によるアトミック剥奪を行わず、受信者に直接 Grant すると二重所有が発生
        R = [*R, ("s_sender_holds", "s_both_owns")]
        # 2. 「1チャネルにつき送信待機は高々1つ」の制約を外すと、ブロック中の
        #    送信者の in-flight メッセージが後続の送信者の送信で上書きされ、
        #    誰にも受信されないまま永久に迷子になる（キューがないため、
        #    キュー内滞留とは異なり回収する場所自体が存在しない）
        R = [*R, ("s_awaiting_peer", "s_orphaned")]

    L = {
        "s_sender_holds": {"sender_owns"},
        "s_in_flight": {"in_flight"},
        "s_awaiting_peer": {"in_flight"},
        "s_receiver_holds": {"receiver_owns"},
        "s_both_owns": {"sender_owns", "receiver_owns"},  # 違反状態
        # 違反状態: in_flight のまま永久に解決しない
        "s_orphaned": {"in_flight", "leaked"},
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
            # in-flight のまま永久に解決しないリーク状態が違反。
            # guards=False（単一待機者制約の撤去）でのみ到達可能になることを変異検査で示す。
            "violation": AtomicProposition("leaked"),
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
