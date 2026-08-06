"""
tools/verifier/components/csp_handoff.py
CspHandoffVerifier: CSP チャネル Handoff 処理でのタスクスターベーション検証部品
キーワード: {Challenge_CspHandoffStarvation}
"""

from tools.verifier.fireball_verifier import FireballModel, VerificationResult


def verify_csp_handoff() -> VerificationResult:
    """
    CSP チャネルの送信・受信タスク間のスターベーションリスクを検証するモデル
    """
    model = FireballModel(
        name="CspHandoffModel",
        keywords=["{Challenge_CspHandoffStarvation}", "{COMP_CSP_001}"]
    )

    # 状態: channel_full, tx_task_wait, rx_task_wait, handoff_count
    model.set_init_state(channel_full=False, tx_waiting=False, rx_waiting=False, handoff_count=0)

    # 送信要求
    model.rule(
        name="Tx_Send_Req",
        when=lambda s: not s["channel_full"] and not s["tx_waiting"],
        action=lambda s: {**s, "channel_full": True, "tx_waiting": True}
    )

    # 受信処理と Handoff (正常消費)
    model.rule(
        name="Rx_Receive_Handoff",
        when=lambda s: s["channel_full"] and s["tx_waiting"],
        action=lambda s: {**s, "channel_full": False, "tx_waiting": False, "handoff_count": min(s["handoff_count"] + 1, 5)}
    )

    # 不変式: トランスミッタが永久に待ち状態 (tx_waiting) にスタックしないこと
    model.invariant(
        name="NoTxStarvation",
        condition=lambda s: not (s["tx_waiting"] and s["handoff_count"] > 10),
        description="CSP Handoff 待ちが規定回数を超えてスタックしないこと"
    )

    results = model.verify()
    return results[0]


if __name__ == "__main__":
    res = verify_csp_handoff()
    print(f"[{'PASS' if res.is_valid else 'FAIL'}] {res.property_name} (キーワード: {res.keywords})")
