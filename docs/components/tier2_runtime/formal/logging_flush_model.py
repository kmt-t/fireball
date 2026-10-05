"""
docs/components/tier2_runtime/formal/logging_flush_model.py
pyModelChecking による Logging コンポーネントの
(1) log_event() が呼び出し側を決してブロックしないこと（overwrite-on-full, GOTCHA-LOG-02）
(2) 保留中のログは COOS Idle Hook によるフラッシュで必ず出力されること
(3) 同期書込みバッチ完了後に割り込みを確認し、次バッチを保留して協調復帰すること（GOTCHA-LOG-03）
(4) 同一辞書の引数数に従う転送長と復号境界、および未知ID・欠損入力の拒否
(5) printkの物理出力前にレコードを復号すること
(6) Base64の作業領域上限と途中パディングの禁止
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier2_runtime/runtime_logging.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    Logging コンポーネントの変異検査対応保護証明モデル
    - s_idle_empty: システムアイドル、バッファ空 (flushed)
    - s_active_partial: 実行中、バッファに未出力ログあり (pending)
    - s_active_full: 実行中、バッファ満杯 (pending)
    - s_idle_flushing: Idle Hook 起動による同期書込みバッチの処理中
    - s_flush_done: フラッシュ完了、バッファ空に戻る (flushed)
    - s_irq_preempt: GOTCHA-LOG-03: 同期書込みバッチ後に割込を確認して協調復帰 (irq_pending)
    - s_irq_handled: 割込ハンドラ処理完了、フラッシュ再開待ち (irq_handled)
    - s_blocked_caller: 違反状態（バッファ満杯時に log_event が呼び出し側をブロックした）
    - s_never_flushed: 違反状態（Idle Hook が配線されておらず、保留ログが永久に出力されない）
    - s_irq_blocked: 違反状態（バッチ後に協調復帰せず割込処理が進まない）
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
        ("s_idle_flushing", "s_printk_decoded"),
        ("s_flush_done", "s_idle_empty"),
        # GOTCHA-LOG-03: バッチ後の割込確認による協調復帰と再開
        ("s_printk_decoded", "s_irq_preempt"),
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
        # 3. interrupt_pending 検査を外すと、割込発生中(s_irq_preempt)でもハンドラ実行に
        # 進めずブロックされたまま停留する経路が生じる（irq_pending の当該状態自体から
        # 分岐させないと、Imply(irq_pending, AF(irq_handled)) の前提が発火しない）
        R = [*R, ("s_irq_preempt", "s_irq_blocked")]

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
    # 同一ビルドの辞書を共有する前提で、各引数数の送受信境界を列挙する。
    # 変異時は辞書と異なる引数数の送信も許し、次レコード位置のずれを検出する。
    for expected_count in range(5):
        for sent_count in range(5):
            state = f"s_record_{expected_count}_{sent_count}"
            S.append(state)
            if not guards or sent_count == expected_count:
                R.extend((("s_idle_empty", state), (state, "s_idle_empty")))
            else:
                R.append((state, state))
            sent_size = 4 + 4 * sent_count
            next_record_offset = 4 + 4 * expected_count
            L[state] = {"boundary_error"} if sent_size != next_record_offset else {"record_aligned"}
    for invalid_input in ("unknown_id", "short_header", "short_arguments"):
        state = f"s_record_{invalid_input}"
        S.append(state)
        R.extend((("s_idle_empty", state), (state, "s_idle_empty")))
        L[state] = {"rejected"} if guards else {"boundary_error"}
    # 出力境界の抽象化。テキスト内容とUTF-8符号化はTEST-LOG-17で直接検査する。
    S.extend(("s_printk_decoded", "s_printk_raw_output"))
    L["s_printk_decoded"] = {"decoded_output"}
    L["s_printk_raw_output"] = {"raw_output"}
    R.append(("s_printk_decoded", "s_flush_done"))
    R.append(("s_printk_raw_output", "s_printk_raw_output"))
    if not guards:
        R.append(("s_idle_flushing", "s_printk_raw_output"))
    # 20出力バイトに収まるチャンクと最終グループの端数を抽象化する。
    # 文字集合と入力ビットの符号化はTEST-LOG-18で直接検査する。
    for input_size in range(17):
        for final_chunk in (False, True):
            state = f"s_base64_{input_size}_{int(final_chunk)}"
            S.append(state)
            encoded_size = 4 * ((input_size + 2) // 3)
            valid = encoded_size <= 20 and (final_chunk or input_size % 3 == 0)
            L[state] = {"base64_chunk_valid"} if valid else {"base64_chunk_error"}
            if guards and not valid:
                R.append((state, state))
            else:
                R.extend((("s_idle_empty", state), (state, "s_idle_empty")))
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_boundary = AtomicProposition("boundary_error")
    bad_blocked = AtomicProposition("blocked")
    bad_never_flushed = AtomicProposition("never_flushed")
    bad_irq_blocked = AtomicProposition("irq_blocked")
    pending = AtomicProposition("pending")
    flushed = AtomicProposition("flushed")
    irq_pending = AtomicProposition("irq_pending")
    irq_handled = AtomicProposition("irq_handled")
    return [
        {
            "name": "base64_bounded_chunks_without_intermediate_padding",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("base64_chunk_error"))),
            "violation": AtomicProposition("base64_chunk_error"),
            "expect": True,
        },
        {
            "name": "printk_decodes_before_physical_output",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("raw_output"))),
            "violation": AtomicProposition("raw_output"),
            "expect": True,
        },
        {
            "name": "dictionary_sized_record_boundary",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_boundary)),
            "violation": bad_boundary,
            "expect": True,
        },
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
            "expect": True,  # GOTCHA-LOG-03: バッチ後の割込確認で協調復帰する。実時間上限は検証しない
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: Logging Flush Model (guards=True) ===")
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed!"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    print("=== Mutation Testing: Logging Flush Model (guards=False) ===")
    km_mut = build_model(guards=False)
    for prop in properties():
        res_mut = modelcheck(km_mut, prop["formula"])
        violated = not km_mut.S0.issubset(res_mut)
        assert violated, f"Mutation for {prop['name']} was NOT detected!"
        print(f"  [PASS (Refuted as expected)] {prop['name']}")
