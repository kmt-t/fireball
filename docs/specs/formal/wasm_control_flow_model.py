"""
docs/specs/formal/wasm_control_flow_model.py
pyModelChecking による WASM 制御フロー命令（block/loop/if/end/br 系）の
(1) `end` がラベルスタック（control_frame スタック）を空の状態からポップしないこと（アンダーフロー不在）
(2) `br`/`br_if`/`br_table` が現在開いているスコープの深度を超えたラベルへ分岐しないこと
の形式検証（証明・変異検査対応）モデル
ラベルスタック深度を 0/1/2 の3段階に抽象化する（0=フレーム無し、1=1段ネスト、2=2段ネスト）。
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["specs/wasm_instruction_set.md"]


def build_model(*, guards: bool = True) -> Kripke:
    """
    WASM 制御フロー・ラベルスタックの変異検査対応保護証明モデル
    - s_depth0: control_frame スタックが空（ネストなし）
    - s_depth1: block/loop/if により control_frame を1段プッシュ済み
    - s_depth2: さらに1段ネスト（2段プッシュ済み）
    - s_underflow: 違反状態（`end` がスタック空の状態でポップを試みた）
    - s_invalid_branch: 違反状態（`br` が現在開いているスコープ深度を超えるラベルへ分岐した）
    """
    S = ["s_depth0", "s_depth1", "s_depth2", "s_underflow", "s_invalid_branch"]
    S0 = {"s_depth0"}
    R = [
        ("s_depth0", "s_depth1"),  # block/loop/if: プッシュ
        ("s_depth1", "s_depth2"),  # ネストしたblock/loop/if: プッシュ
        ("s_depth1", "s_depth0"),  # end: ポップ
        ("s_depth2", "s_depth1"),  # end: ポップ
        ("s_depth1", "s_depth1"),  # br/br_if: 現在の深度内への有効な分岐
        ("s_depth2", "s_depth2"),  # br/br_if: 現在の深度内への有効な分岐
        # 違反状態の自己ループ（Kripke 構造は全域的でなければならない）
        ("s_underflow", "s_underflow"),
        ("s_invalid_branch", "s_invalid_branch"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. 静的検証（V1-V6 相当）を外すと、スタック空の状態で end がポップを試みうる
        R = R + [("s_depth0", "s_underflow")]
        # 2. ラベル深度の静的検証を外すと、br が開いていないスコープへ分岐しうる
        R = R + [("s_depth1", "s_invalid_branch")]

    L = {
        "s_depth0": {"depth0"},
        "s_depth1": {"depth1"},
        "s_depth2": {"depth2"},
        "s_underflow": {"underflow"},  # 違反状態
        "s_invalid_branch": {"invalid_branch"},  # 違反状態
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_underflow = AtomicProposition("underflow")
    bad_invalid_branch = AtomicProposition("invalid_branch")
    return [
        {
            "name": "end_never_underflows_label_stack",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_underflow)),
            "violation": bad_underflow,
            "expect": True,  # 静的検証により、空スタックからの end ポップは到達不能
        },
        {
            "name": "branch_never_targets_undeclared_label",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_invalid_branch)),
            "violation": bad_invalid_branch,
            "expect": True,  # 静的なラベル深度検証により、範囲外分岐は到達不能
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        print(f"[{'PASS' if passed == prop['expect'] else 'FAIL'}] {prop['name']}")
