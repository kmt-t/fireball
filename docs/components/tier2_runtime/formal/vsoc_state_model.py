"""
docs/components/tier2_runtime/formal/vsoc_state_model.py
pyModelChecking による vSoC 実行状態・Safepoint 応答性・Debugger 整合性の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, EF, And, Not, Imply, AtomicProposition

BACKS = [
    "components/tier2_runtime/runtime_vsoc.md",
    "components/tier1_core/system_config_details.md",
]


def build_model() -> Kripke:
    """
    vSoC 実行エンジン・割り込み Safepoint・デバッグフォールバックの Kripke モデル
    - s_interpreter_run: インタープリタ実行中 (interp_mode, running)
    - s_jit_run: JIT ネイティブ実行中 (jit_mode, running)
    - s_safepoint_check: Safepoint ポーリング確認 (safepoint)
    - s_interrupt_handling: 割り込みイベント処理 (handling_irq)
    - s_debugger_paused: デバッガ一時停止 (paused, debug_safe)
    - s_bad_irq_jit: 違反状態（割り込み処理中に JIT が無同期で直接暴走した状態）
    """
    S = [
        "s_interpreter_run",
        "s_jit_run",
        "s_safepoint_check",
        "s_interrupt_handling",
        "s_debugger_paused",
        "s_bad_irq_jit",
    ]
    S0 = {"s_interpreter_run"}
    R = [
        # インタープリタから Hot 判定で JIT 実行へ
        ("s_interpreter_run", "s_jit_run"),
        # インタープリタ実行中の Safepoint 確認
        ("s_interpreter_run", "s_safepoint_check"),
        # JIT 実行中の Safepoint ポーリング
        ("s_jit_run", "s_safepoint_check"),
        # Safepoint で通常実行継続
        ("s_safepoint_check", "s_jit_run"),
        # Safepoint で割り込み検知 ➔ ハンドラへ
        ("s_safepoint_check", "s_interrupt_handling"),
        # 異常系: 割り込み処理中に JIT が無同期実行を開始するレース
        ("s_interrupt_handling", "s_bad_irq_jit"),
        # Safepoint でブレークポイント検知 ➔ デバッガ停止へ
        ("s_safepoint_check", "s_debugger_paused"),
        # 割り込み完了後 ➔ インタープリタ/スケジューラへ
        ("s_interrupt_handling", "s_interpreter_run"),
        # デバッガ再開 ➔ インタープリタへ
        ("s_debugger_paused", "s_interpreter_run"),
        # 違反状態からの回復
        ("s_bad_irq_jit", "s_interpreter_run"),
    ]
    L = {
        "s_interpreter_run": {"running", "interp_mode"},
        "s_jit_run": {"running", "jit_mode"},
        "s_safepoint_check": {"safepoint"},
        "s_interrupt_handling": {"handling_irq"},
        "s_debugger_paused": {"paused", "debug_safe"},
        "s_bad_irq_jit": {"handling_irq", "jit_mode"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad = And(AtomicProposition("handling_irq"), AtomicProposition("jit_mode"))
    return [
        {
            "name": "irq_jit_race_detectable",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": False,  # レース状態が検出可能であることを実証
        },
        {
            "name": "safepoint_reachable",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(EF(AtomicProposition("safepoint"))),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model()
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
