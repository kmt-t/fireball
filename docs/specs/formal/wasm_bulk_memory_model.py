"""CTL model for selected WASM 0xFC memory and saturating-conversion rules."""

from typing import Literal, TypedDict

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Formula, Imply, Not, modelcheck

BACKS = [
    "specs/wasm_instruction_set.md",
    "components/tier2_runtime/interpreter.md",
    "components/tier2_runtime/runtime_vsoc.md",
]


class FormalProperty(TypedDict):
    name: str
    kind: Literal["safety", "liveness"]
    logic: Literal["CTL"]
    formula: Formula
    violation: Formula
    expect: bool


def build_model(*, guards: bool = True) -> Kripke:
    """Build normal transitions or add one missing-guard mutation per rule."""
    states = [
        "s_start",
        "s_oob_copy_request",
        "s_oob_copy_trap",
        "s_overlap_copy_request",
        "s_linear_disjoint_request",
        "s_overlap_cpu_done",
        "s_overlap_dma",
        "s_dma_copy_request",
        "s_dma_pending",
        "s_dma_complete",
        "s_dma_visible",
        "s_guest_resume",
        "s_resume_while_pending",
        "s_resume_before_dma_visible",
        "s_nan_conversion",
        "s_positive_overflow_conversion",
        "s_signed_underflow_conversion",
        "s_unsigned_underflow_conversion",
        "s_finite_conversion",
        "s_result_zero",
        "s_result_max",
        "s_result_signed_min",
        "s_result_finite",
        "s_wrong_conversion_result",
        "s_conversion_trap",
        "s_partial_memory_write",
    ]
    transitions = [
        ("s_start", "s_oob_copy_request"),
        ("s_start", "s_overlap_copy_request"),
        ("s_start", "s_linear_disjoint_request"),
        ("s_start", "s_dma_copy_request"),
        ("s_start", "s_nan_conversion"),
        ("s_start", "s_positive_overflow_conversion"),
        ("s_start", "s_signed_underflow_conversion"),
        ("s_start", "s_unsigned_underflow_conversion"),
        ("s_start", "s_finite_conversion"),
        ("s_oob_copy_request", "s_oob_copy_trap"),
        ("s_overlap_copy_request", "s_overlap_cpu_done"),
        ("s_linear_disjoint_request", "s_overlap_cpu_done"),
        ("s_dma_copy_request", "s_dma_complete"),  # Synchronous completion is permitted.
        ("s_dma_copy_request", "s_dma_pending"),  # Deferred completion uses the wait path.
        ("s_dma_pending", "s_dma_pending"),  # External completion is not guaranteed.
        ("s_dma_pending", "s_dma_complete"),
        ("s_dma_complete", "s_dma_visible"),
        ("s_dma_visible", "s_guest_resume"),
        ("s_nan_conversion", "s_result_zero"),
        ("s_positive_overflow_conversion", "s_result_max"),
        ("s_signed_underflow_conversion", "s_result_signed_min"),
        ("s_unsigned_underflow_conversion", "s_result_zero"),
        ("s_finite_conversion", "s_result_finite"),
    ]
    terminal_states = [
        "s_oob_copy_trap",
        "s_overlap_cpu_done",
        "s_guest_resume",
        "s_result_zero",
        "s_result_max",
        "s_result_signed_min",
        "s_result_finite",
        "s_wrong_conversion_result",
        "s_conversion_trap",
        "s_partial_memory_write",
        "s_overlap_dma",
        "s_resume_while_pending",
        "s_resume_before_dma_visible",
    ]
    transitions.extend((state, state) for state in terminal_states)

    if not guards:
        transitions.extend(
            [
                ("s_oob_copy_request", "s_partial_memory_write"),
                ("s_overlap_copy_request", "s_overlap_dma"),
                ("s_linear_disjoint_request", "s_overlap_dma"),
                ("s_dma_pending", "s_resume_while_pending"),
                ("s_dma_complete", "s_resume_before_dma_visible"),
                ("s_nan_conversion", "s_wrong_conversion_result"),
                ("s_positive_overflow_conversion", "s_wrong_conversion_result"),
                ("s_signed_underflow_conversion", "s_wrong_conversion_result"),
                ("s_unsigned_underflow_conversion", "s_wrong_conversion_result"),
                ("s_finite_conversion", "s_conversion_trap"),
            ]
        )

    labels = {
        "s_start": {"start"},
        "s_oob_copy_request": {"copy_request"},
        "s_oob_copy_trap": {"trap", "memory_unchanged"},
        "s_overlap_copy_request": {"linear_copy_request"},
        "s_linear_disjoint_request": {"linear_copy_request"},
        "s_overlap_cpu_done": {"linear_copy", "cpu_copy", "copy_complete"},
        "s_overlap_dma": {"linear_copy", "dma_used"},
        "s_dma_copy_request": {"vmmio_copy_request"},
        "s_dma_pending": {"dma_pending"},
        "s_dma_complete": {"dma_complete", "dma_idle"},
        "s_dma_visible": {"dma_complete", "dma_idle", "memory_visible"},
        "s_guest_resume": {"guest_resumed"},
        "s_resume_while_pending": {"resume_while_dma_pending"},
        "s_resume_before_dma_visible": {"resume_before_dma_visible"},
        "s_nan_conversion": {"conversion", "nan_input"},
        "s_positive_overflow_conversion": {
            "conversion",
            "positive_overflow_input",
        },
        "s_signed_underflow_conversion": {
            "conversion",
            "signed_underflow_input",
        },
        "s_unsigned_underflow_conversion": {
            "conversion",
            "unsigned_underflow_input",
        },
        "s_finite_conversion": {"conversion", "finite_input"},
        "s_result_zero": {"conversion_complete", "result_zero"},
        "s_result_max": {"conversion_complete", "result_max"},
        "s_result_signed_min": {"conversion_complete", "result_signed_min"},
        "s_result_finite": {"conversion_complete", "result_finite"},
        "s_wrong_conversion_result": {"wrong_conversion_result"},
        "s_conversion_trap": {"conversion_trap"},
        "s_partial_memory_write": {"partial_memory_write"},
    }
    return Kripke(S=states, S0={"s_start"}, R=transitions, L=labels)


def properties() -> list[FormalProperty]:
    linear_copy = AtomicProposition("linear_copy")
    nan_input = AtomicProposition("nan_input")
    positive_overflow = AtomicProposition("positive_overflow_input")
    signed_underflow = AtomicProposition("signed_underflow_input")
    unsigned_underflow = AtomicProposition("unsigned_underflow_input")
    finite_input = AtomicProposition("finite_input")
    return [
        {
            "name": "oob_copy_never_partially_mutates",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("partial_memory_write"))),
            "violation": AtomicProposition("partial_memory_write"),
            "expect": True,
        },
        {
            "name": "linear_copy_never_uses_dma",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(And(linear_copy, AtomicProposition("dma_used")))),
            "violation": And(linear_copy, AtomicProposition("dma_used")),
            "expect": True,
        },
        {
            "name": "guest_never_resumes_while_dma_pending",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("resume_while_dma_pending"))),
            "violation": AtomicProposition("resume_while_dma_pending"),
            "expect": True,
        },
        {
            "name": "guest_never_resumes_before_dma_visible",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("resume_before_dma_visible"))),
            "violation": AtomicProposition("resume_before_dma_visible"),
            "expect": True,
        },
        {
            "name": "nan_saturates_to_zero",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(nan_input, AF(AtomicProposition("result_zero")))),
            "violation": AtomicProposition("wrong_conversion_result"),
            "expect": True,
        },
        {
            "name": "positive_overflow_saturates_to_maximum",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(positive_overflow, AF(AtomicProposition("result_max")))),
            "violation": AtomicProposition("wrong_conversion_result"),
            "expect": True,
        },
        {
            "name": "signed_underflow_saturates_to_minimum",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(signed_underflow, AF(AtomicProposition("result_signed_min")))),
            "violation": AtomicProposition("wrong_conversion_result"),
            "expect": True,
        },
        {
            "name": "unsigned_underflow_saturates_to_zero",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(unsigned_underflow, AF(AtomicProposition("result_zero")))),
            "violation": AtomicProposition("wrong_conversion_result"),
            "expect": True,
        },
        {
            "name": "finite_saturating_conversion_completes",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(finite_input, AF(AtomicProposition("result_finite")))),
            "violation": AtomicProposition("conversion_trap"),
            "expect": True,
        },
        {
            "name": "saturating_conversion_never_traps",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("conversion_trap"))),
            "violation": AtomicProposition("conversion_trap"),
            "expect": True,
        },
    ]


def verify() -> None:
    normal = build_model(guards=True)
    mutated = build_model(guards=False)
    for property_item in properties():
        result = modelcheck(normal, property_item["formula"])
        assert normal.S0.issubset(result), (
            f"Property {property_item['name']} failed in the guarded model"
        )

        mutated_result = modelcheck(mutated, property_item["formula"])
        assert not mutated.S0.issubset(mutated_result), (
            f"Mutation for {property_item['name']} was not detected"
        )


if __name__ == "__main__":
    verify()
