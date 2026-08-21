"""
docs/components/tier1_core/formal/coos_channel_model.py
pyModelChecking による COOS CSP チャネル・連続ハンドオフ離脱・スケジューラ復帰の形式検証モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, And, Not, Imply, AtomicProposition

BACKS = [
    "components/tier1_core/os_coos.md",
    "components/tier1_core/os_scheduler.md",
    "components/tier1_core/system_config_details.md",
]


def build_model() -> Kripke:
    """
    COOS の協調型 CSP チャネルハンドオフとスケジューラ復帰モデル
    - s_main_loop: スケジューラのメインループ（READY キュー巡回中）
    - s_task_running: タスクが協調実行中 (running)
    - s_handoff_1: タスク間 CSP 直接ハンドオフ実行中 (in_handoff_chain)
    - s_handoff_max: 連続ハンドオフ上限 FB_CONF_MAX_CONSECUTIVE_HANDOFFS 到達 (in_handoff_chain, at_max_limit)
    - s_forced_yield: 上限到達による強制 yield・READY キュー末尾投入 (yielding)
    - s_deadlock: 違反状態（送信側・受信側の循環デッドロック）
    - s_double_owned: 違反状態（同一チャネルの二重所有競合）
    """
    S = [
        "s_main_loop",
        "s_task_running",
        "s_handoff_1",
        "s_handoff_max",
        "s_forced_yield",
        "s_deadlock",
        "s_double_owned",
    ]
    S0 = {"s_main_loop"}
    R = [
        # メインループからタスクディスパッチ
        ("s_main_loop", "s_task_running"),
        # タスク実行から直接ハンドオフまたは自発的 yield
        ("s_task_running", "s_handoff_1"),
        ("s_task_running", "s_main_loop"),
        # ハンドオフからタスク実行継続、または連続ハンドオフ上限へ
        ("s_handoff_1", "s_task_running"),
        ("s_handoff_1", "s_handoff_max"),
        # 上限到達時は強制 yield へしか遷移できない（ガード条件）
        ("s_handoff_max", "s_forced_yield"),
        # 強制 yield 後は必ずメインループへ復帰する
        ("s_forced_yield", "s_main_loop"),
        # 障害注入（未保護時のレース・デッドロック反証パス）
        ("s_handoff_1", "s_deadlock"),
        ("s_deadlock", "s_deadlock"),
        ("s_task_running", "s_double_owned"),
        ("s_double_owned", "s_main_loop"),
    ]
    L = {
        "s_main_loop": {"main_loop"},
        "s_task_running": {"running"},
        "s_handoff_1": {"in_handoff_chain", "running"},
        "s_handoff_max": {"in_handoff_chain", "at_max_limit"},
        "s_forced_yield": {"yielding"},
        "s_deadlock": {"deadlock"},  # 違反状態
        "s_double_owned": {"double_owned"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_deadlock = AtomicProposition("deadlock")
    bad_double = AtomicProposition("double_owned")
    return [
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
            "expect": True,  # 上限到達時は必ずメインループに復帰する (AF)
        },
        {
            "name": "deadlock_detectable",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_deadlock)),
            "violation": bad_deadlock,
            "expect": False,  # 未保護時のデッドロック状態が検出可能
        },
        {
            "name": "double_ownership_detectable",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_double)),
            "violation": bad_double,
            "expect": False,  # 未保護時の二重所有状態が検出可能
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model()
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
