"""CTL models for Interpreter storage separation and stack restoration."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not

BACKS = [
    "components/tier2_runtime/interpreter.md",
    "components/tier3_plugins/jit_runtime.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """Model independent value regions and loader-resolved branch restoration."""
    states = (
        "s_call_entry",
        "s_call_frame_descriptor",
        "s_local_frame",
        "s_operand_active",
        "s_branch_active",
        "s_branch_restored",
        "s_return",
        "s_result_on_operand",
        "s_stack_aliasing",
        "s_result_lost",
        "s_frame_metadata_aliasing",
        "s_branch_restore_lost",
    )
    transitions = [
        ("s_call_entry", "s_call_frame_descriptor"),
        ("s_call_frame_descriptor", "s_local_frame"),
        ("s_local_frame", "s_operand_active"),
        ("s_local_frame", "s_branch_active"),
        ("s_operand_active", "s_return"),
        ("s_branch_active", "s_branch_restored"),
        ("s_branch_restored", "s_return"),
        ("s_return", "s_result_on_operand"),
        ("s_result_on_operand", "s_call_entry"),
        ("s_stack_aliasing", "s_stack_aliasing"),
        ("s_result_lost", "s_result_lost"),
        ("s_frame_metadata_aliasing", "s_frame_metadata_aliasing"),
        ("s_branch_restore_lost", "s_branch_restore_lost"),
    ]
    if not guards:
        transitions.remove(("s_branch_active", "s_branch_restored"))
        transitions.extend(
            [
                ("s_call_frame_descriptor", "s_frame_metadata_aliasing"),
                ("s_local_frame", "s_stack_aliasing"),
                ("s_return", "s_result_lost"),
                ("s_branch_active", "s_branch_restore_lost"),
            ]
        )
    labels = {
        "s_call_frame_descriptor": {"call_frame_active", "descriptor_separate"},
        "s_local_frame": {"local_stack_active", "call_active"},
        "s_operand_active": {"operand_stack_active", "call_active"},
        "s_branch_active": {"branch_active", "annotated_stack_base_used"},
        "s_branch_restored": {
            "operand_height_restored",
            "branch_results_preserved",
        },
        "s_return": {"local_stack_release", "call_active"},
        "s_result_on_operand": {"result_on_operand_stack"},
        "s_stack_aliasing": {"stack_aliasing"},
        "s_result_lost": {"result_lost"},
        "s_frame_metadata_aliasing": {"frame_metadata_aliasing"},
        "s_branch_restore_lost": {"branch_restore_lost"},
    }
    return Kripke(S=states, S0={"s_call_entry"}, R=transitions, L=labels)


def properties():
    bad_aliasing = AtomicProposition("stack_aliasing")
    bad_frame_aliasing = AtomicProposition("frame_metadata_aliasing")
    call_active = AtomicProposition("call_active")
    result_on_operand = AtomicProposition("result_on_operand_stack")
    branch_active = AtomicProposition("branch_active")
    branch_restored = Imply(
        AtomicProposition("operand_height_restored"),
        AtomicProposition("branch_results_preserved"),
    )
    branch_restore_lost = AtomicProposition("branch_restore_lost")
    return [
        {
            "name": "operand_and_local_value_regions_remain_independent",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_aliasing)),
            "violation": bad_aliasing,
            "expect": True,
        },
        {
            "name": "call_descriptor_is_separate_from_local_values",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad_frame_aliasing)),
            "violation": bad_frame_aliasing,
            "expect": True,
        },
        {
            "name": "call_result_reaches_operand_stack",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(call_active, AF(result_on_operand))),
            "violation": AtomicProposition("result_lost"),
            "expect": True,
        },
        {
            "name": "annotated_branch_restores_operand_height_and_results",
            "kind": "liveness",
            "logic": "CTL",
            "formula": And(
                AG(Imply(branch_active, AF(branch_restored))),
                AG(Not(branch_restore_lost)),
            ),
            "violation": branch_restore_lost,
            "expect": True,
        },
    ]


def build_width_model(*, guards: bool = True) -> Kripke:
    """u8 variable-width rejection and opcode-derived fixed-width advance."""
    states = ("load", "accepted", "rejected", "advanced", "bad_width", "bad_advance")
    edges = [
        ("load", "accepted"),
        ("load", "rejected"),
        ("accepted", "advanced"),
        ("advanced", "advanced"),
        ("rejected", "rejected"),
        ("bad_width", "bad_width"),
        ("bad_advance", "bad_advance"),
    ]
    if not guards:
        edges.extend((("load", "bad_width"), ("accepted", "bad_advance")))
    return Kripke(
        S=states,
        S0={"load"},
        R=edges,
        L={"bad_width": {"oversized_width_accepted"}, "bad_advance": {"non_opcode_advance"}},
    )


def width_properties():
    return (
        AG(Not(AtomicProposition("oversized_width_accepted"))),
        AG(Not(AtomicProposition("non_opcode_advance"))),
    )


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    model = build_model()
    for prop in properties():
        result = modelcheck(model, prop["formula"])
        assert model.S0.issubset(result) == prop["expect"], prop["name"]
    width_model = build_width_model()
    for formula in width_properties():
        assert width_model.S0.issubset(modelcheck(width_model, formula))
    print("[PASS] interpreter stack model guards=True")

    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        assert not mutated_model.S0.issubset(result), f"Mutation survived: {prop['name']}"
    mutated_width_model = build_width_model(guards=False)
    for formula in width_properties():
        result = modelcheck(mutated_width_model, formula)
        assert not mutated_width_model.S0.issubset(result), "Width mutation survived"
    print("[PASS] interpreter stack model guards=False")
