"""
docs/components/tier2_runtime/formal/syscall_trap_model.py
pyModelChecking による fireball_call host-call 状態プロトコルの
(1) ホストハンドラが戻り値を返して完了するまで、ゲストが再開されないこと
(2) ホストハンドラが戻り値を確定した後は、ゲスト実行へ戻ること
の形式検証（証明・変異検査対応）モデル

2系統のトラップ経路（カテゴリ A・B、例: vMMIO Generic / IPC）を並置することで、
単一経路モデル（インターリーブが存在せず証明が自明になってしまう）を避ける。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = ["components/tier2_runtime/runtime_syscall.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    トラップ状態プロトコルの変異検査対応保護証明モデル（トラップ経路 A・B 対称）
    - s_guest_running: ゲストが通常実行中 (running)
    - s_{a,b}_trap: fireball_call import の解決、ゲスト PC 静止 (in_trap)
    - s_{a,b}_args: 引数が host-call シグネチャへ渡された状態 (in_trap)
    - s_{a,b}_dispatch: ホスト側ハンドラが同期実行中 (in_trap)
    - s_b_waiting: IPC ハンドラが相手を待ち、未到着なら無期限に待機する状態
    - s_{a,b}_retset: ホストハンドラが戻り値を返した状態 (in_trap)
    - s_{a,b}_resumed: ゲスト PC が次命令へ進み実行再開
    - s_premature_resume: 違反状態（ホストハンドラ完了前にゲストが再開してしまう）
    - s_stuck_after_return: 違反状態（戻り値確定後にゲストが永久に再開されない）
    """
    S = [
        "s_guest_running",
        "s_a_trap",
        "s_a_args",
        "s_a_dispatch",
        "s_a_retset",
        "s_a_resumed",
        "s_b_trap",
        "s_b_args",
        "s_b_dispatch",
        "s_b_waiting",
        "s_b_retset",
        "s_b_resumed",
        "s_premature_resume",
        "s_stuck_after_return",
        "s_yield_call",
        "s_yield_pending",
        "s_yield_boundary",
        "s_yield_handoff",
        "s_yield_resumed",
        "s_yield_done",
        "s_yield_early_handoff",
        "s_yield_stuck",
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
        ("s_b_dispatch", "s_b_waiting"),
        ("s_b_waiting", "s_b_waiting"),  # 相手到着は保証されず、待機は無期限になり得る
        ("s_b_waiting", "s_b_retset"),  # matching peer 到着後にのみハンドラが完了する
        ("s_b_retset", "s_b_resumed"),
        ("s_b_resumed", "s_guest_running"),
        # SYS_YIELD records a request in the synchronous host call and transfers
        # control only after Runtime has reached a resumable boundary.
        ("s_guest_running", "s_yield_call"),
        ("s_yield_call", "s_yield_pending"),
        ("s_yield_pending", "s_yield_boundary"),
        ("s_yield_boundary", "s_yield_handoff"),
        ("s_yield_handoff", "s_yield_resumed"),
        ("s_yield_resumed", "s_guest_running"),
        ("s_yield_resumed", "s_yield_done"),
        ("s_yield_done", "s_yield_done"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_premature_resume", "s_premature_resume"),
        ("s_stuck_after_return", "s_stuck_after_return"),
        ("s_yield_early_handoff", "s_yield_early_handoff"),
        ("s_yield_stuck", "s_yield_stuck"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 「ホスト戻り値 → 復帰」の同期規律を外すと、ハンドラ完了前にゲストが再開してしまう
        R = [*R, ("s_a_dispatch", "s_premature_resume"), ("s_b_dispatch", "s_premature_resume")]
        # 2. 戻り値確定後の復帰ガードを外すと、完了済み呼び出しが復帰しない経路が生じる
        R = [*R, ("s_a_retset", "s_stuck_after_return"), ("s_b_retset", "s_stuck_after_return")]
        # SYS_YIELD の境界ガードまたは再開保証を外す変異を追加する。
        R = [
            *R,
            ("s_yield_pending", "s_yield_early_handoff"),
            ("s_yield_handoff", "s_yield_stuck"),
        ]

    L = {
        "s_guest_running": {"running"},
        "s_a_trap": {"in_trap"},
        "s_a_args": {"in_trap"},
        "s_a_dispatch": {"in_trap"},
        "s_a_retset": {"in_trap", "host_returned"},
        "s_a_resumed": {"resumed"},
        "s_b_trap": {"in_trap"},
        "s_b_args": {"in_trap"},
        "s_b_dispatch": {"in_trap"},
        "s_b_waiting": {"in_trap", "waiting_peer"},
        "s_b_retset": {"in_trap", "host_returned"},
        "s_b_resumed": {"resumed"},
        "s_premature_resume": {"premature"},  # 違反状態
        "s_stuck_after_return": {"host_returned", "stuck"},  # 違反状態
        "s_yield_call": {"yield_call"},
        "s_yield_pending": {"yield_pending"},
        "s_yield_boundary": {"safe_boundary"},
        "s_yield_handoff": {"yield_handoff"},
        "s_yield_resumed": {"yield_resumed"},
        "s_yield_done": {"yield_done"},
        "s_yield_early_handoff": {"unsafe_handoff"},
        "s_yield_stuck": {"yield_handoff", "yield_stuck"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_premature = AtomicProposition("premature")
    host_returned = AtomicProposition("host_returned")
    running = AtomicProposition("running")
    unsafe_handoff = AtomicProposition("unsafe_handoff")
    yield_handoff = AtomicProposition("yield_handoff")
    yield_resumed = AtomicProposition("yield_resumed")
    return [
        {
            "name": "guest_never_resumes_before_host_return",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_premature)),
            "violation": bad_premature,
            "expect": True,  # ホストハンドラの戻り値を経てのみ復帰するため、早期再開状態は到達不能
        },
        {
            "name": "completed_handler_eventually_resumes_guest",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(host_returned, AF(running))),
            "violation": AtomicProposition("stuck"),
            "expect": True,  # 完了後の復帰だけを検証し、IPC相手の到着や有限待機は仮定しない
        },
        {
            "name": "sys_yield_handoff_waits_for_safe_runtime_boundary",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(unsafe_handoff)),
            "violation": unsafe_handoff,
            "expect": True,
        },
        {
            "name": "sys_yielded_guest_eventually_resumes",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(yield_handoff, AF(yield_resumed))),
            "violation": AtomicProposition("yield_stuck"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: Syscall Trap Model (guards=True) ===")
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Property {prop['name']} verification failed!"
        print(f"  [{'PASS' if passed else 'FAIL'}] {prop['name']}")

    print("=== Mutation Testing: Syscall Trap Model (guards=False) ===")
    km_mut = build_model(guards=False)
    for prop in properties():
        res_mut = modelcheck(km_mut, prop["formula"])
        violated = not km_mut.S0.issubset(res_mut)
        assert violated, f"Mutation for {prop['name']} was NOT detected!"
        print(f"  [PASS (Refuted as expected)] {prop['name']}")
