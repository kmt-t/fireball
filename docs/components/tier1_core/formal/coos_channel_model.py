"""
docs/components/tier1_core/formal/coos_channel_model.py
pyModelChecking による COOS CSP チャネル・同期ランデブー・デッドロック不在・二重所有不在の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = [
    "components/tier1_core/os_coos.md",
    "components/tier1_core/os_scheduler.md",
    "components/tier1_core/system_config.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    COOS 同期ランデブー CSP 通信とスケジューラ復帰の変異検査対応保護証明モデル
    - s_main_loop: スケジューラのメインループ（READY キュー巡回中）
    - s_task_a_run: タスク A が協調実行中 (running)
    - s_task_b_run: タスク B が協調実行中 (running)
    - s_blocked_tx_a: タスク A が受信側不在でサスペンド (blocked)
    - s_handoff_1: タスク間 CSP 直接ハンドオフ実行中 (in_handoff_chain)
    - s_handoff_max: 連続ハンドオフ上限 FB_CONF_MAX_CONSECUTIVE_HANDOFFS 到達 (at_max_limit)
    - s_forced_yield: 上限到達による強制 yield (yielding)
    - s_deadlock: 違反状態（タスク A と B が互いに待ち合ってサスペンドした循環待ちデッドロック）
    - s_double_owned: 違反状態（同一チャネルの二重所有競合）
    - s_handoff_livelock: 違反状態（上限到達後も強制 yield されず、ハンドオフ連鎖から
      メインループへ復帰しないライブロック）
    """
    S = [
        "s_main_loop",
        "s_task_a_run",
        "s_task_b_run",
        "s_blocked_tx_a",
        "s_handoff_1",
        "s_handoff_max",
        "s_forced_yield",
        "s_deadlock",
        "s_double_owned",
        "s_handoff_livelock",
    ]
    S0 = {"s_main_loop"}
    R = [
        # スケジューラからタスク A / B ディスパッチ
        ("s_main_loop", "s_task_a_run"),
        ("s_main_loop", "s_task_b_run"),
        # タスク A 実行から自発的 yield または相手不在でサスペンド
        ("s_task_a_run", "s_main_loop"),
        ("s_task_a_run", "s_blocked_tx_a"),
        # タスク A からタスク B への直接同期ハンドオフ
        ("s_task_a_run", "s_handoff_1"),
        ("s_handoff_1", "s_task_b_run"),
        ("s_handoff_1", "s_handoff_max"),
        # サスペンド中のタスク A は、タスク B が受信（recv）したときに起床してメインループへ
        ("s_blocked_tx_a", "s_main_loop"),
        # タスク B 実行からメインループへ
        ("s_task_b_run", "s_main_loop"),
        # 上限到達時は強制 yield を経てメインループへ復帰
        ("s_handoff_max", "s_forced_yield"),
        ("s_forced_yield", "s_main_loop"),
        # 違反状態の自己ループ
        ("s_deadlock", "s_deadlock"),
        ("s_double_owned", "s_double_owned"),
        ("s_handoff_livelock", "s_handoff_livelock"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. クライアント・サーバ規律を破り、タスク A がサスペンド待機中にタスク B も A にブロック送信すると循環デッドロック
        R = [*R, ("s_blocked_tx_a", "s_deadlock")]
        # 2. 所有権アトミック剥奪を怠ると、ハンドオフ中に二重所有が発生
        R = [*R, ("s_handoff_1", "s_double_owned")]
        # 3. FB_CONF_MAX_CONSECUTIVE_HANDOFFS の強制 yield を外すと、上限到達後も
        #    ハンドオフ連鎖を続けられてしまい、メインループへ復帰しないライブロックに陥る
        R = [*R, ("s_handoff_max", "s_handoff_livelock")]

    L = {
        "s_main_loop": {"main_loop"},
        "s_task_a_run": {"running"},
        "s_task_b_run": {"running"},
        "s_blocked_tx_a": {"blocked"},
        "s_handoff_1": {"in_handoff_chain", "running"},
        "s_handoff_max": {"in_handoff_chain", "at_max_limit"},
        "s_forced_yield": {"yielding"},
        "s_deadlock": {"deadlock"},  # 違反状態
        "s_double_owned": {"double_owned"},  # 違反状態
        # 違反状態: 上限到達済み (at_max_limit) のままメインループへ到達しない
        "s_handoff_livelock": {"in_handoff_chain", "at_max_limit", "handoff_livelock"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_deadlock = AtomicProposition("deadlock")
    bad_double = AtomicProposition("double_owned")
    return [
        {
            "name": "deadlock_freedom_under_acyclic_topology",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_deadlock)),
            "violation": bad_deadlock,
            "expect": True,  # クライアント・サーバ非循環規律によりデッドロック状態は到達不能
        },
        {
            "name": "double_ownership_freedom_proof",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_double)),
            "violation": bad_double,
            "expect": True,  # 所有権アトミック移譲により二重所有状態は到達不能
        },
        {
            "name": "handoff_recovers_to_main_loop",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("at_max_limit"),
                    AF(AtomicProposition("main_loop")),
                )
            ),
            # 上限到達済みのままメインループへ到達しないライブロック状態が違反。
            # guards=False（強制 yield 撤去）でのみ到達可能になることを変異検査で示す。
            "violation": AtomicProposition("handoff_livelock"),
            "expect": True,  # 上限到達時は必ずメインループに復帰する (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
