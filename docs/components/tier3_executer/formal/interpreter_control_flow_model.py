"""Interpreter の分岐時スタック復元を検証する抽象CTLモデル。"""

from typing import Literal, TypedDict

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Formula, Imply, Not

BACKS = ["components/tier3_executer/interpreter.md"]


class FormalProperty(TypedDict):
    name: str
    kind: Literal["safety", "liveness"]
    logic: Literal["CTL"]
    formula: Formula
    violation: Formula
    expect: bool


def build_model(*, guards: bool = True) -> Kripke:
    """if偽分岐、loop脱出、block脱出の制御スタック効果を抽象化する。"""
    states = [
        "s_start",
        "s_if_false",
        "s_if_false_complete",
        "s_loop_target",
        "s_loop_pruned",
        "s_loop_branch_complete",
        "s_block_target",
        "s_block_pruned",
        "s_block_branch_complete",
        "s_bad_branch_state",
        "s_if_frame_leaked",
    ]
    transitions = [
        ("s_start", "s_if_false"),
        ("s_start", "s_loop_target"),
        ("s_start", "s_block_target"),
        ("s_if_false", "s_if_false_complete"),
        ("s_loop_target", "s_loop_pruned"),
        ("s_loop_pruned", "s_loop_branch_complete"),
        ("s_block_target", "s_block_pruned"),
        ("s_block_pruned", "s_block_branch_complete"),
        ("s_if_false_complete", "s_if_false_complete"),
        ("s_loop_branch_complete", "s_loop_branch_complete"),
        ("s_block_branch_complete", "s_block_branch_complete"),
        ("s_bad_branch_state", "s_bad_branch_state"),
        ("s_if_frame_leaked", "s_if_frame_leaked"),
    ]
    if not guards:
        # 分岐対象までの中間frame/operandを巻き戻さない変異。
        transitions.extend(
            [
                ("s_loop_target", "s_bad_branch_state"),
                ("s_block_target", "s_bad_branch_state"),
                # if条件偽でelseがない場合に、スキップ対象のif frameを残す変異。
                ("s_if_false", "s_if_frame_leaked"),
            ]
        )

    labels = {
        "s_start": {"entry"},
        "s_if_false": {"if_false"},
        "s_if_false_complete": {"if_frame_absent"},
        "s_loop_target": {"loop_branch"},
        "s_loop_pruned": {"operand_height_restored", "branch_arity_preserved"},
        "s_loop_branch_complete": {
            "loop_target_frame_retained",
            "operand_height_restored",
            "branch_arity_preserved",
            "loop_branch_complete",
        },
        "s_block_target": {"block_branch"},
        "s_block_pruned": {"operand_height_restored", "branch_arity_preserved"},
        "s_block_branch_complete": {
            "block_target_frame_removed",
            "operand_height_restored",
            "branch_arity_preserved",
            "block_branch_complete",
        },
        "s_bad_branch_state": {"bad_branch_state"},
        "s_if_frame_leaked": {"if_frame_leaked"},
    }
    return Kripke(S=states, S0={"s_start"}, R=transitions, L=labels)


def properties() -> list[FormalProperty]:
    loop_branch = AtomicProposition("loop_branch")
    block_branch = AtomicProposition("block_branch")
    if_false = AtomicProposition("if_false")
    loop_complete = AtomicProposition("loop_branch_complete")
    block_complete = AtomicProposition("block_branch_complete")
    if_frame_absent = AtomicProposition("if_frame_absent")
    loop_complete_contract = And(
        loop_complete,
        AtomicProposition("loop_target_frame_retained"),
        AtomicProposition("operand_height_restored"),
        AtomicProposition("branch_arity_preserved"),
    )
    block_complete_contract = And(
        block_complete,
        AtomicProposition("block_target_frame_removed"),
        AtomicProposition("operand_height_restored"),
        AtomicProposition("branch_arity_preserved"),
    )
    return [
        {
            "name": "loop_branch_restores_operands_and_retains_target_frame",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(loop_branch, AF(loop_complete_contract))),
            "violation": Imply(loop_branch, Not(AF(loop_complete_contract))),
            "expect": True,
        },
        {
            "name": "block_branch_restores_operands_and_removes_target_frame",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(block_branch, AF(block_complete_contract))),
            "violation": Imply(block_branch, Not(AF(block_complete_contract))),
            "expect": True,
        },
        {
            "name": "false_if_without_else_leaves_no_control_frame",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(if_false, AF(if_frame_absent))),
            "violation": Imply(if_false, Not(AF(if_frame_absent))),
            "expect": True,
        },
        {
            "name": "no_invalid_control_frame_state_is_reachable",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("bad_branch_state"))),
            "violation": AtomicProposition("bad_branch_state"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    model = build_model()
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        assert model.S0.issubset(result) == prop["expect"], prop["name"]
    print("[PASS] interpreter control-flow model guards=True")

    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        assert not mutated_model.S0.issubset(result), f"Mutation survived: {prop['name']}"
    print("[PASS] interpreter control-flow model guards=False")
