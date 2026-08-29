"""
docs/components/tier1_core/formal/syscall_trap_model.py
pyModelChecking による fireball_call トラップ状態プロトコルの
(1) ホストハンドラが REG_SYSCALL_RET を設定して完了するまで、ゲストが再開されないこと
(2) トラップは必ずいずれ完了し、ゲスト実行が再開されること
の形式検証（証明・変異検査対応）モデル

2系統のトラップ経路（カテゴリ A・B、例: vMMIO Generic / IPC）を並置することで、
単一経路モデル（インターリーブが存在せず証明が自明になってしまう）を避ける。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AF, Imply, Not, AtomicProposition

BACKS = ["components/tier1_core/system_syscall.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    トラップ状態プロトコルの変異検査対応保護証明モデル（トラップ経路 A・B 対称）
    - s_guest_running: ゲストが通常実行中 (running)
    - s_{a,b}_trap: fireball_call によりトラップ検知、ゲスト PC 静止 (in_trap)
    - s_{a,b}_args: 引数が REG_SYSCALL_* へマッピング済み (in_trap)
    - s_{a,b}_dispatch: ホスト側ハンドラが同期実行中 (in_trap)
    - s_{a,b}_retset: ホストが REG_SYSCALL_RET を設定 (in_trap)
    - s_{a,b}_resumed: ゲスト PC が次命令へ進み実行再開
    - s_premature_resume: 違反状態（ホストハンドラ完了前にゲストが再開してしまう）
    - s_stuck_trap: 違反状態（ホストハンドラが完了せず、ゲストが永久に再開されない）
    """
    S = [
        "s_guest_running",
        "s_a_trap", "s_a_args", "s_a_dispatch", "s_a_retset", "s_a_resumed",
        "s_b_trap", "s_b_args", "s_b_dispatch", "s_b_retset", "s_b_resumed",
        "s_premature_resume",
        "s_stuck_trap",
    ]
    S0 = {"s_guest_running"}
    R = [
        ("s_guest_running", "s_a_trap"),
        ("s_a_trap", "s_a_args"),
        ("s_a_args", "s_a_dispatch"),
        ("s_a_dispatch", "s_a_retset"),
        ("s_a_retset", "s_a_resumed"),
        ("s_a_resumed", "s_guest_running"),
        ("s_guest_running", "s_b_trap"),
        ("s_b_trap", "s_b_args"),
        ("s_b_args", "s_b_dispatch"),
        ("s_b_dispatch", "s_b_retset"),
        ("s_b_retset", "s_b_resumed"),
        ("s_b_resumed", "s_guest_running"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_premature_resume", "s_premature_resume"),
        ("s_stuck_trap", "s_stuck_trap"),
    ]

    if not guards:
        # ガード無効時（変異検査）:
        # 1. 「RET 設定 → 復帰」の同期規律を外すと、ハンドラ完了前にゲストが再開してしまう
        R = R + [("s_a_dispatch", "s_premature_resume"), ("s_b_dispatch", "s_premature_resume")]
        # 2. ホストハンドラの完了保証を外すと、トラップが永久に完了しない経路が生じる
        R = R + [("s_a_dispatch", "s_stuck_trap"), ("s_b_dispatch", "s_stuck_trap")]

    L = {
        "s_guest_running": {"running"},
        "s_a_trap": {"in_trap"}, "s_a_args": {"in_trap"},
        "s_a_dispatch": {"in_trap"}, "s_a_retset": {"in_trap"}, "s_a_resumed": {"resumed"},
        "s_b_trap": {"in_trap"}, "s_b_args": {"in_trap"},
        "s_b_dispatch": {"in_trap"}, "s_b_retset": {"in_trap"}, "s_b_resumed": {"resumed"},
        "s_premature_resume": {"premature"},   # 違反状態
        "s_stuck_trap": {"stuck"},              # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_premature = AtomicProposition("premature")
    bad_stuck = AtomicProposition("stuck")
    in_trap = AtomicProposition("in_trap")
    running = AtomicProposition("running")
    return [
        {
            "name": "guest_never_resumes_before_host_return",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_premature)),
            "violation": bad_premature,
            "expect": True,  # RET 設定を経てのみ復帰する規律により、早期再開状態は到達不能
        },
        {
            "name": "every_trap_eventually_resumes_guest",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(in_trap, AF(running))),
            "violation": bad_stuck,
            "expect": True,  # ホストハンドラは必ず完了し、ゲスト実行はいずれ再開される (AF)
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
