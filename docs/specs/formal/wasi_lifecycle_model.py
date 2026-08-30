"""
docs/specs/formal/wasi_lifecycle_model.py
pyModelChecking による WASI Preview 1 ABI の
(1) クローズ済み／未オープンの fd に対する I/O が決して実際には実行されず EBADF となること
(2) `proc_exit` はゲストへ復帰せず（noreturn）、タスクは必ず TERMINATED へ遷移すること
の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, Not, AtomicProposition

BACKS = ["specs/wasi_preview1_abi.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    WASI Preview 1 ABI ライフサイクルの変異検査対応保護証明モデル
    - s_task_running: ゲストタスクが通常実行中、fd 未オープン
    - s_fd_open: `fd>=3` の IPC チャネル fd がオープン済み
    - s_io_call / s_io_performed: オープン中 fd への `fd_write`/`fd_read` が実行される
    - s_fd_closed: `fd_close` によりチャネルがクローズ済み
    - s_io_call_on_closed: クローズ済み fd への I/O 呼び出し
    - s_io_call_bogus: 一度もオープンされていない fd への I/O 呼び出し
    - s_badf_error: `EBADF` を返却（I/O は実行されない）
    - s_proc_exit_called: `proc_exit` 呼び出し
    - s_task_terminated: タスクが `TERMINATED` へ遷移（スタック回収済み）
    - s_io_performed_on_invalid: 違反状態（クローズ済み/未オープンの fd に対し I/O が実際に実行された）
    - s_phantom_return: 違反状態（`proc_exit` 呼び出し後にゲストコードへ復帰してしまった）
    """
    S = [
        "s_task_running",
        "s_fd_open",
        "s_io_call",
        "s_io_performed",
        "s_fd_closed",
        "s_io_call_on_closed",
        "s_io_call_bogus",
        "s_badf_error",
        "s_proc_exit_called",
        "s_task_terminated",
        "s_io_performed_on_invalid",
        "s_phantom_return",
    ]
    S0 = {"s_task_running"}
    R = [
        ("s_task_running", "s_fd_open"),
        ("s_fd_open", "s_io_call"),
        ("s_io_call", "s_io_performed"),
        ("s_io_performed", "s_fd_open"),
        ("s_fd_open", "s_fd_closed"),
        ("s_fd_closed", "s_io_call_on_closed"),
        ("s_io_call_on_closed", "s_badf_error"),
        ("s_task_running", "s_io_call_bogus"),
        ("s_io_call_bogus", "s_badf_error"),
        ("s_badf_error", "s_badf_error"),
        ("s_task_running", "s_proc_exit_called"),
        ("s_proc_exit_called", "s_task_terminated"),
        ("s_task_terminated", "s_task_terminated"),
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_io_performed_on_invalid", "s_io_performed_on_invalid"),
        ("s_phantom_return", "s_phantom_return"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. fd バリデーション（EBADF チェック）を外すと、無効な fd への I/O が実際に実行されてしまう
        R = R + [
            ("s_io_call_on_closed", "s_io_performed_on_invalid"),
            ("s_io_call_bogus", "s_io_performed_on_invalid"),
        ]
        # 2. noreturn 規約（TERMINATED 遷移とスタック回収）を外すと、proc_exit 後にゲストへ復帰しうる
        R = R + [("s_proc_exit_called", "s_phantom_return")]

    L = {
        "s_task_running": {"running"},
        "s_fd_open": {"open"},
        "s_io_call": {"open"},
        "s_io_performed": {"open"},
        "s_fd_closed": {"closed"},
        "s_io_call_on_closed": {"closed"},
        "s_io_call_bogus": {"bogus"},
        "s_badf_error": {"badf"},
        "s_proc_exit_called": {"exiting"},
        "s_task_terminated": {"terminated"},
        "s_io_performed_on_invalid": {"io_on_invalid"},  # 違反状態
        "s_phantom_return": {"phantom_return"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_io = AtomicProposition("io_on_invalid")
    bad_phantom = AtomicProposition("phantom_return")
    return [
        {
            "name": "io_never_succeeds_on_invalid_fd",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_io)),
            "violation": bad_io,
            "expect": True,  # EBADF 検証により、無効な fd への I/O 実行状態は到達不能
        },
        {
            "name": "proc_exit_never_returns_to_guest",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_phantom)),
            "violation": bad_phantom,
            "expect": True,  # noreturn 規約により、proc_exit 後のゲスト復帰状態は到達不能
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
