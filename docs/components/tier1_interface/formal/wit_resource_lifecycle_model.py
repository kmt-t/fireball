"""
docs/components/tier1_interface/formal/wit_resource_lifecycle_model.py
pyModelChecking による WIT インターフェイスの
(1) `resource`（bus-master/streaming 等）はハンドルが drop された後、決して操作が実行されないこと
(2) ホストがトリガーした仮想割り込みは、対応する `pollable` が必ずいずれ ready になり届くこと
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, Imply, Not, AtomicProposition

BACKS = ["components/tier1_interface/interface_wit.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    WIT リソースライフサイクル・非同期通知の変異検査対応保護証明モデル
    - s_idle: ゲストが待機中（リソース未生成、割り込みなし）
    - s_resource_active: `resource`（bus-master/streaming 等）ハンドルが生成・有効
    - s_op_call / s_op_performed: 有効なハンドルへの操作（`transfer-data` 等）呼び出し・実行
    - s_resource_dropped: ハンドルが drop 済み
    - s_op_call_on_dropped: drop 済みハンドルへの操作呼び出し
    - s_op_rejected: 操作が正しく拒否される（実行されない）
    - s_interrupt_triggered: ホストが仮想割り込みをトリガー
    - s_pollable_ready: 対応する `pollable` が ready 状態になりゲストへ届く
    - s_op_performed_on_dropped: 違反状態（drop 済みハンドルへの操作が実際に実行された）
    - s_notification_lost: 違反状態（トリガーされた割り込みの pollable が ready にならない）
    """
    S = [
        "s_idle",
        "s_resource_active",
        "s_op_call",
        "s_op_performed",
        "s_resource_dropped",
        "s_op_call_on_dropped",
        "s_op_rejected",
        "s_interrupt_triggered",
        "s_pollable_ready",
        "s_op_performed_on_dropped",
        "s_notification_lost",
    ]
    S0 = {"s_idle"}
    R = [
        ("s_idle", "s_resource_active"),
        ("s_resource_active", "s_op_call"),
        ("s_op_call", "s_op_performed"),
        ("s_op_performed", "s_resource_active"),
        ("s_resource_active", "s_resource_dropped"),
        ("s_resource_dropped", "s_op_call_on_dropped"),
        ("s_op_call_on_dropped", "s_op_rejected"),
        ("s_op_rejected", "s_op_rejected"),
        ("s_idle", "s_interrupt_triggered"),
        ("s_interrupt_triggered", "s_pollable_ready"),
        ("s_pollable_ready", "s_idle"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_op_performed_on_dropped", "s_op_performed_on_dropped"),
        ("s_notification_lost", "s_notification_lost"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. drop 済みハンドルの検証を外すと、操作が実際に実行されてしまう
        R = R + [("s_op_call_on_dropped", "s_op_performed_on_dropped")]
        # 2. pollable への ready 通知配送を外すと、割り込みが届かない経路が生じる
        R = R + [("s_interrupt_triggered", "s_notification_lost")]

    L = {
        "s_idle": {"idle"},
        "s_resource_active": {"active"},
        "s_op_call": {"active"},
        "s_op_performed": {"active"},
        "s_resource_dropped": {"dropped"},
        "s_op_call_on_dropped": {"dropped"},
        "s_op_rejected": {"rejected"},
        "s_interrupt_triggered": {"triggered"},
        "s_pollable_ready": {"ready"},
        "s_op_performed_on_dropped": {"op_on_dropped"},  # 違反状態
        "s_notification_lost": {"lost"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_op = AtomicProposition("op_on_dropped")
    bad_lost = AtomicProposition("lost")
    triggered = AtomicProposition("triggered")
    ready = AtomicProposition("ready")
    return [
        {
            "name": "resource_op_never_succeeds_after_drop",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_op)),
            "violation": bad_op,
            "expect": True,  # ハンドル有効性検証により、drop 後の操作実行状態は到達不能
        },
        {
            "name": "triggered_interrupt_always_reaches_pollable_ready",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(triggered, AF(ready))),
            "violation": bad_lost,
            "expect": True,  # 仮想割り込みは必ず対応する pollable の ready 化として配送される (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
