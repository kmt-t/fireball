"""CTL model for loader-resolved Interpreter branch metadata."""

from typing import Literal, TypedDict

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Formula, Not

BACKS = ["components/tier2_runtime/interpreter.md"]


class FormalProperty(TypedDict):
    name: str
    kind: Literal["safety", "liveness"]
    logic: Literal["CTL"]
    formula: Formula
    violation: Formula
    expect: bool


def build_model(*, guards: bool = True) -> Kripke:
    """Model static branch targets, operand restoration, and if fallthrough."""
    states = (
        "s_start",
        "s_branch_edge_loaded",
        "s_branch_state_restored",
        "s_loop_target_selected",
        "s_if_false_selected",
        "s_bad_branch_pc",
        "s_bad_stack_restore",
        "s_bad_if_target",
    )
    transitions = [
        ("s_start", "s_branch_edge_loaded"),
        ("s_branch_edge_loaded", "s_branch_state_restored"),
        ("s_branch_state_restored", "s_loop_target_selected"),
        ("s_loop_target_selected", "s_branch_edge_loaded"),
        ("s_start", "s_if_false_selected"),
        ("s_if_false_selected", "s_start"),
        ("s_bad_branch_pc", "s_bad_branch_pc"),
        ("s_bad_stack_restore", "s_bad_stack_restore"),
        ("s_bad_if_target", "s_bad_if_target"),
    ]
    if not guards:
        transitions.extend(
            [
                ("s_branch_edge_loaded", "s_bad_branch_pc"),
                ("s_branch_edge_loaded", "s_bad_stack_restore"),
                ("s_if_false_selected", "s_bad_if_target"),
            ]
        )
    labels = {
        "s_branch_edge_loaded": {"annotated_edge_selected", "edge_pc_matches"},
        "s_branch_state_restored": {"operand_height_restored", "branch_results_preserved"},
        "s_loop_target_selected": {"loop_body_target_selected"},
        "s_if_false_selected": {"if_else_or_end_target_selected"},
        "s_bad_branch_pc": {"edge_pc_mismatch"},
        "s_bad_stack_restore": {"operand_height_mismatch"},
        "s_bad_if_target": {"if_false_target_mismatch"},
    }
    return Kripke(S=states, S0={"s_start"}, R=transitions, L=labels)


def properties() -> list[FormalProperty]:
    return [
        {
            "name": "branch_index_resolves_to_its_annotated_pc",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("edge_pc_mismatch"))),
            "violation": AtomicProposition("edge_pc_mismatch"),
            "expect": True,
        },
        {
            "name": "branch_restores_operand_height_and_results",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("operand_height_mismatch"))),
            "violation": AtomicProposition("operand_height_mismatch"),
            "expect": True,
        },
        {
            "name": "false_if_uses_its_static_else_or_end_target",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("if_false_target_mismatch"))),
            "violation": AtomicProposition("if_false_target_mismatch"),
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
