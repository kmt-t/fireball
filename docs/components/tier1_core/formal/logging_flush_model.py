"""
docs/components/tier1_core/formal/logging_flush_model.py
pyModelChecking による Logging コンポーネントの
(1) log_event() が呼び出し側を決してブロックしないこと（overwrite-on-full, LOG-GOTCHA-02）
(2) 保留中のログは COOS Idle Hook によるフラッシュで必ず出力されること
(3) ログフラッシュループ中に外部割り込みが発生した場合、即座に中断して割り込み応答すること（LOG-GOTCHA-03）
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier1_core/system_logging.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    Logging コンポーネントの変異検査対応保護証明モデル
    - s_idle_empty: システムアイドル、バッファ空 (flushed)
    - s_active_partial: 実行中、バッファに未出力ログあり (pending)
    - s_active_full: 実行中、バッファ満杯 (pending)
    - s_idle_flushing: Idle Hook 起動によるバックグラウンド DMA/割り込みフラッシュ中
    - s_flush_done: フラッシュ完了、バッファ空に戻る (flushed)
    - s_irq_preempt: LOG-GOTCHA-03: フラッシュ中に外部割込が発生し即座に中断 (irq_pending)
    - s_irq_handled: 割込ハンドラ処理完了、フラッシュ再開待ち (irq_handled)
    - s_blocked_caller: 違反状態（バッファ満杯時に log_event が呼び出し側をブロックした）
    - s_never_flushed: 違反状態（Idle Hook が配線されておらず、保留ログが永久に出力されない）
    - s_irq_blocked: 違反状態（フラッシュを中断できず高優先度割込がブロックされた）
    """
    S = [
        "s_idle_empty",
        "s_active_partial",
        "s_active_full",
        "s_idle_flushing",
        "s_flush_done",
        "s_irq_preempt",
        "s_irq_handled",
        "s_blocked_caller",
        "s_never_flushed",
        "s_irq_blocked",
    ]
    S0 = {"s_idle_empty"}
    R = [
        ("s_idle_empty", "s_active_partial"),
        ("s_active_partial", "s_active_full"),
        ("s_active_partial", "s_idle_flushing"),
        ("s_active_full", "s_idle_flushing"),
        ("s_idle_flushing", "s_flush_done"),
        ("s_flush_done", "s_idle_empty"),
        # LOG-GOTCHA-03: 割込によるフラッシュ即時中断と再開
        ("s_idle_flushing", "s_irq_preempt"),
        ("s_irq_preempt", "s_irq_handled"),
        ("s_irq_handled", "s_flush_done"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_blocked_caller", "s_blocked_caller"),
        ("s_never_flushed", "s_never_flushed"),
        ("s_irq_blocked", "s_irq_blocked"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. overwrite-on-full 方針を外すと、満杯時の log_event が呼び出し側をブロックする
        R = [*R, ("s_active_full", "s_blocked_caller")]
        # 2. Idle Hook 連携を外すと、保留ログが一度もフラッシュされずに終わる経路が生じる
        R = [*R, ("s_active_partial", "s_never_flushed")]
        # 3. interrupt_pending 検査を外すと、外部割込がフラッシュ完了まで待たされブロックされる
        R = [*R, ("s_idle_flushing", "s_irq_blocked")]

    L = {
        "s_idle_empty": {"flushed"},
        "s_active_partial": {"pending"},
        "s_active_full": {"pending"},
        "s_idle_flushing": {"flushing"},
        "s_flush_done": {"flushed"},
        "s_irq_preempt": {"irq_pending"},
        "s_irq_handled": {"irq_handled"},
        "s_blocked_caller": {"blocked"},  # 違反状態
        "s_never_flushed": {"never_flushed"},  # 違反状態
        "s_irq_blocked": {"irq_blocked"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_blocked = AtomicProposition("blocked")
    bad_never_flushed = AtomicProposition("never_flushed")
    bad_irq_blocked = AtomicProposition("irq_blocked")
    pending = AtomicProposition("pending")
    flushed = AtomicProposition("flushed")
    irq_pending = AtomicProposition("irq_pending")
    irq_handled = AtomicProposition("irq_handled")
    return [
        {
            "name": "non_blocking_log_event",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_blocked)),
            "violation": bad_blocked,
            "expect": True,  # overwrite-on-full により呼び出し側は決してブロックされない
        },
        {
            "name": "idle_hook_eventual_flush",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(pending, AF(flushed))),
            "violation": bad_never_flushed,
            "expect": True,  # 保留中のログは Idle Hook により必ずいつかフラッシュされる
        },
        {
            "name": "interrupt_preempts_flush_promptly",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(irq_pending, AF(irq_handled))),
            "violation": bad_irq_blocked,
            "expect": True,  # LOG-GOTCHA-03: 外部割込発生時はフラッシュを即座に中断し有界時間内にハンドラを実行
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
