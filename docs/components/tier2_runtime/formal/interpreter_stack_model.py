"""
docs/components/tier2_runtime/formal/interpreter_stack_model.py
pyModelChecking による Interpreter の OperandStack・LocalStack・control_frame
独立性および関数復帰時の結果値保持の形式検証（証明・変異検査対応）モデル
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = [
    "components/tier2_runtime/runtime_interpreter.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """
    Interpreter の3本の独立スタックと関数復帰を抽象化した保護証明モデル。
    - s_call_entry: 関数呼び出し境界への入口
    - s_local_frame: LocalStack に call_frame とローカル領域を確保
    - s_operand_first: OperandStack を先に使う実行順序
    - s_control_after_operand: OperandStack の評価後に control_frame を更新
    - s_control_first: control_frame を先に更新する実行順序
    - s_operand_after_control: control_frame の更新後に OperandStack を使う実行順序
    - s_return: LocalStack のフレームを解放して関数復帰
    - s_result_on_operand: 結果値が OperandStack に保持された状態
    - s_stack_aliasing: 違反状態（3本のスタック領域が共有される）
    - s_result_lost: 違反状態（関数復帰時に結果値が失われる）
    """
    S = [
        "s_call_entry",
        "s_local_frame",
        "s_operand_first",
        "s_control_after_operand",
        "s_control_first",
        "s_operand_after_control",
        "s_return",
        "s_result_on_operand",
        "s_stack_aliasing",
        "s_result_lost",
    ]
    S0 = {"s_call_entry"}
    R = [
        # 関数呼び出しから、LocalStack・OperandStack・control_frame を独立に使用
        ("s_call_entry", "s_local_frame"),
        # 独立したスタック操作の順序を2通りモデル化する。
        # これにより、単一の一本道ではなく、許容される実行インターリーブを検査できる。
        ("s_local_frame", "s_operand_first"),
        ("s_local_frame", "s_control_first"),
        ("s_operand_first", "s_control_after_operand"),
        ("s_control_after_operand", "s_return"),
        ("s_control_first", "s_operand_after_control"),
        ("s_operand_after_control", "s_return"),
        # LocalStack のフレームを解放しても結果値は OperandStack に残る
        ("s_return", "s_result_on_operand"),
        ("s_result_on_operand", "s_call_entry"),
        # 違反状態の自己ループ
        ("s_stack_aliasing", "s_stack_aliasing"),
        ("s_result_lost", "s_result_lost"),
    ]
    if not guards:
        # ガード無効時（変異検査）:
        # 1. LocalStack と OperandStack/control_frame の領域分離を怠る
        R = [*R, ("s_local_frame", "s_stack_aliasing")]
        # 2. 関数復帰時に結果値を OperandStack へ保持しない
        R = [*R, ("s_return", "s_result_lost")]

    L = {
        "s_call_entry": {"call_boundary"},
        "s_local_frame": {"local_stack_active", "call_active"},
        "s_operand_first": {"operand_stack_active", "call_active"},
        "s_control_after_operand": {"control_frame_active", "call_active"},
        "s_control_first": {"control_frame_active", "call_active"},
        "s_operand_after_control": {"operand_stack_active", "call_active"},
        "s_return": {"local_stack_release", "call_active"},
        "s_result_on_operand": {"result_on_operand_stack"},
        "s_stack_aliasing": {"stack_aliasing"},
        "s_result_lost": {"result_lost"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad_aliasing = AtomicProposition("stack_aliasing")
    call_active = AtomicProposition("call_active")
    result_on_operand = AtomicProposition("result_on_operand_stack")
    return [
        {
            "name": "interpreter_stacks_remain_independent",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_aliasing)),
            "violation": bad_aliasing,
            "expect": True,  # LocalStack・OperandStack・control_frame は物理的に分離される
        },
        {
            "name": "call_result_reaches_operand_stack",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(call_active, AF(result_on_operand))),
            "violation": AtomicProposition("result_lost"),
            "expect": True,  # 関数復帰後の結果値はOperandStackへ保持される
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    print("=== Formal Verification: Interpreter Stack Model (guards=True) ===")
    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        assert passed == prop["expect"], f"Proof failed for {prop['name']}"
        print(f"[PASS] {prop['name']} (guards=True)")

    print("=== Mutation Testing: Interpreter Stack Model (guards=False) ===")
    km_mut = build_model(guards=False)
    for prop in properties():
        res = modelcheck(km_mut, prop["formula"])
        passed = km_mut.S0.issubset(res)
        assert not passed, (
            f"Mutation check failed: {prop['name']} was not refuted under guards=False!"
        )
        print(f"[PASS] {prop['name']} mutation rejected (guards=False)")
