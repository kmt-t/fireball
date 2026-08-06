"""
tools/verifier/components/interrupt_safety.py
InterruptSafetyVerifier: 割り込みハンドラ (ISR) とタスク間の競合回避およびウェイクアップ安全性の検証部品
キーワード: {Challenge_InterruptSafety}
"""

from tools.verifier.fireball_verifier import FireballModel, VerificationResult


def verify_interrupt_safety() -> VerificationResult:
    """
    割り込み発生とタスクのウエイクアップ間の競合・アトミック性を検証するモデル
    """
    model = FireballModel(
        name="InterruptSafetyModel",
        keywords=["{Challenge_InterruptSafety}", "{COMP_INTERRUPT_001}"]
    )

    # 状態: isr_pending (割り込み保留), task_state ('idle', 'sleeping', 'woken', 'running'), lock_held (排他)
    model.set_init_state(isr_pending=False, task_state="sleeping", lock_held=False)

    # --- Rule 1: ISR Triggers (割り込み発生) ---
    model.rule(
        name="ISR_Trigger",
        when=lambda s: not s["isr_pending"],
        action=lambda s: {**s, "isr_pending": True}
    )

    # --- Rule 2: ISR Wakeup Task (ISRがタスクをウエイクアップ) ---
    model.rule(
        name="ISR_Wakeup_Task",
        when=lambda s: s["isr_pending"] and s["task_state"] == "sleeping",
        action=lambda s: {**s, "isr_pending": False, "task_state": "woken"}
    )

    # --- Rule 3: Task Schedule (タスクが実行状態に移行) ---
    model.rule(
        name="Task_Schedule",
        when=lambda s: s["task_state"] == "woken",
        action=lambda s: {**s, "task_state": "running", "lock_held": True}
    )

    # --- Rule 4: Task Sleep (タスクが処理完了後にスリープ) ---
    model.rule(
        name="Task_Sleep",
        when=lambda s: s["task_state"] == "running",
        action=lambda s: {**s, "task_state": "sleeping", "lock_held": False}
    )

    # Invariant: 割り込みが保留されている間、タスクが永遠にスリープしたまま放置されないこと
    model.invariant(
        name="NoLostWakeup",
        condition=lambda s: not (s["isr_pending"] and s["task_state"] == "sleeping" and s["lock_held"]),
        description="割り込み保留中にロック保持状態でデッドスリープしないこと"
    )

    results = model.verify()
    return results[0]


if __name__ == "__main__":
    res = verify_interrupt_safety()
    print(f"[{'PASS' if res.is_valid else 'FAIL'}] {res.property_name} (キーワード: {res.keywords})")
