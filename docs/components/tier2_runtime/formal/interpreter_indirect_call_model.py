"""選択先の型照合と、拒否時に関数本体を実行しない契約を検証する。"""

from typing import Literal, TypedDict

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Formula, Imply, Not

BACKS = ["components/tier2_runtime/interpreter.md", "specs/wasm_instruction_set.md"]


class FormalProperty(TypedDict):
    name: str
    kind: Literal["safety", "liveness"]
    logic: Literal["CTL"]
    formula: Formula
    violation: Formula
    expect: bool


def build_model(*, guards: bool = True) -> Kripke:
    """引数と索引の評価後に、適合する選択先だけが呼ばれることを抽象化する。"""
    states = [
        "start",
        "arguments_evaluated",
        "index_evaluated",
        "match",
        "mismatch",
        "uninitialized",
        "out_of_bounds",
        "called",
        "wrong_target_called",
        "trapped",
    ]
    transitions = [
        ("start", "arguments_evaluated"),
        ("arguments_evaluated", "index_evaluated"),
        ("index_evaluated", "match"),
        ("index_evaluated", "mismatch"),
        ("index_evaluated", "uninitialized"),
        ("index_evaluated", "out_of_bounds"),
        ("match", "called" if guards else "wrong_target_called"),
        ("called", "called"),
        ("wrong_target_called", "wrong_target_called"),
        ("trapped", "trapped"),
    ]
    for rejected in ("mismatch", "uninitialized", "out_of_bounds"):
        transitions.append((rejected, "trapped" if guards else "wrong_target_called"))
    return Kripke(
        S=states,
        S0=["start"],
        R=transitions,
        L={
            "start": set(),
            "arguments_evaluated": {"arguments_evaluated"},
            "index_evaluated": {"arguments_evaluated", "index_evaluated"},
            "match": {"compatible_selected_target", "other_signature_present"},
            "mismatch": {"rejected_target"},
            "uninitialized": {"rejected_target"},
            "out_of_bounds": {"rejected_target"},
            "called": {
                "body_entered",
                "correct_target",
                "signature_checked",
                "arguments_evaluated",
                "index_evaluated",
            },
            "wrong_target_called": {"body_entered", "arguments_evaluated", "index_evaluated"},
            "trapped": {"trap_without_body"},
        },
    )


def properties() -> list[FormalProperty]:
    compatible = AtomicProposition("compatible_selected_target")
    rejected = AtomicProposition("rejected_target")
    body = AtomicProposition("body_entered")
    correct = AtomicProposition("correct_target")
    trap = AtomicProposition("trap_without_body")
    entry_contract = And(
        correct,
        AtomicProposition("signature_checked"),
        AtomicProposition("arguments_evaluated"),
        AtomicProposition("index_evaluated"),
    )
    return [
        {
            "name": "mixed_table_calls_compatible_selected_target",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(compatible, AF(correct))),
            "violation": Imply(compatible, Not(AF(correct))),
            "expect": True,
        },
        {
            "name": "rejected_target_traps_without_entering_body",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(rejected, AF(trap))),
            "violation": Imply(rejected, Not(AF(trap))),
            "expect": True,
        },
        {
            "name": "body_entry_requires_selected_signature_and_evaluated_operands",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(body, entry_contract)),
            "violation": And(body, Not(entry_contract)),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    for guards, model in ((True, build_model(guards=True)), (False, build_model(guards=False))):
        for prop in properties():
            satisfied = model.S0.issubset(modelcheck(model, prop["formula"]))
            assert satisfied == guards, (prop["name"], guards)
        print(f"[PASS] interpreter indirect-call model guards={guards}")
