"""
docs/components/tier1_core/formal/logging_flush_model.py
pyModelChecking による Logging コンポーネントの
(1) log_event() が呼び出し側を決してブロックしないこと（overwrite-on-full）
(2) 保留中のログは COOS Idle Hook によるフラッシュで必ず出力されること
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, Imply, Not, AtomicProposition

BACKS = ["components/tier1_core/system_logging.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    Logging コンポーネントの変異検査対応保護証明モデル
    - s_idle_empty: システムアイドル、バッファ空 (flushed)
    - s_active_partial: 実行中、バッファに未出力ログあり (pending)
    - s_active_full: 実行中、バッファ満杯 (pending)
    - s_idle_flushing: Idle Hook 起動によるバックグラウンド DMA/割り込みフラッシュ中
    - s_flush_done: フラッシュ完了、バッファ空に戻る (flushed)
    - s_blocked_caller: 違反状態（バッファ満杯時に log_event が呼び出し側をブロックした）
    - s_never_flushed: 違反状態（Idle Hook が配線されておらず、保留ログが永久に出力されない）
    """
    S = [
        "s_idle_empty",
        "s_active_partial",
        "s_active_full",
        "s_idle_flushing",
        "s_flush_done",
        "s_blocked_caller",
        "s_never_flushed",
    ]
    S0 = {"s_idle_empty"}
    R = [
        ("s_idle_empty", "s_active_partial"),
        ("s_active_partial", "s_active_full"),
        ("s_active_partial", "s_idle_flushing"),
        ("s_active_full", "s_idle_flushing"),
        ("s_idle_flushing", "s_flush_done"),
        ("s_flush_done", "s_idle_empty"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_blocked_caller", "s_blocked_caller"),
        ("s_never_flushed", "s_never_flushed"),
    ]

    if not guards:
        # ガード無効時（変異検査）:
        # 1. overwrite-on-full 方針を外すと、満杯時の log_event が呼び出し側をブロックする
        R = R + [("s_active_full", "s_blocked_caller")]
        # 2. Idle Hook 連携を外すと、保留ログが一度もフラッシュされずに終わる経路が生じる
        R = R + [("s_active_partial", "s_never_flushed")]

    L = {
        "s_idle_empty": {"flushed"},
        "s_active_partial": {"pending"},
        "s_active_full": {"pending"},
        "s_idle_flushing": {"flushing"},
        "s_flush_done": {"flushed"},
        "s_blocked_caller": {"blocked"},  # 違反状態
        "s_never_flushed": {"never_flushed"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_blocked = AtomicProposition("blocked")
    bad_never_flushed = AtomicProposition("never_flushed")
    pending = AtomicProposition("pending")
    flushed = AtomicProposition("flushed")
    return [
        {
            "name": "log_event_never_blocks_caller",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_blocked)),
            "violation": bad_blocked,
            "expect": True,  # overwrite-on-full により満杯時もブロック状態は到達不能
        },
        {
            "name": "pending_logs_eventually_flushed",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(pending, AF(flushed))),
            # Idle Hook 未配線のまま保留ログが出力されない違反が到達可能になる
            # ことを guards=False（変異検査）で示す。
            "violation": bad_never_flushed,
            "expect": True,  # Idle Hook 連携により保留ログは必ずいずれ出力される (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
