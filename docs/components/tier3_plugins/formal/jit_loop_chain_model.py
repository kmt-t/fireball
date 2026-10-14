"""CTL model for bounded cyclic JIT chains, loop yielding, and retirement unlinking."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AtomicProposition, Imply, Not

BACKS = [
    "components/tier3_plugins/jit_runtime.md",
    "components/tier3_plugins/jit_compiler.md",
    "components/tier2_runtime/jit_abi.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """Model a finite chain walk, cooperative yield, and safe cycle retirement."""
    states = [
        "cycle_start",
        "cycle_second",
        "cycle_repeat",
        "cycle_done",
        "loop_start",
        "loop_not_taken",
        "loop_one_edge",
        "loop_threshold",
        "loop_yielded",
        "loop_exited",
        "retire_target",
        "unlink_complete",
        "cycle_overrun",
        "yield_lost",
        "untaken_counted",
        "dangling_link",
    ]
    transitions = [
        ("cycle_start", "cycle_second"),
        ("cycle_second", "cycle_repeat"),
        ("cycle_repeat", "cycle_done"),
        ("cycle_done", "loop_start"),
        ("loop_start", "loop_not_taken"),
        ("loop_start", "loop_one_edge"),
        ("loop_not_taken", "loop_exited"),
        ("loop_one_edge", "loop_exited"),
        ("loop_one_edge", "loop_threshold"),
        ("loop_threshold", "loop_yielded"),
        ("loop_yielded", "loop_yielded"),
        ("loop_yielded", "retire_target"),
        ("loop_exited", "retire_target"),
        ("retire_target", "unlink_complete"),
        ("unlink_complete", "unlink_complete"),
        ("cycle_overrun", "cycle_overrun"),
        ("yield_lost", "yield_lost"),
        ("untaken_counted", "untaken_counted"),
        ("dangling_link", "dangling_link"),
    ]
    if not guards:
        transitions.extend(
            [
                ("cycle_repeat", "cycle_overrun"),
                ("loop_threshold", "yield_lost"),
                ("loop_start", "untaken_counted"),
                ("retire_target", "dangling_link"),
            ]
        )
    labels = {
        "cycle_start": {"walk_active"},
        "cycle_second": {"walk_active", "one_body"},
        "cycle_repeat": {"walk_active", "two_bodies"},
        "cycle_done": {"walk_done", "two_bodies"},
        "loop_start": {"loop_running"},
        "loop_not_taken": {"not_taken", "zero_edges"},
        "loop_one_edge": {"one_taken_edge"},
        "loop_threshold": {"threshold_reached", "two_taken_edges"},
        "loop_yielded": {"yielded", "threshold_reached", "two_taken_edges"},
        "loop_exited": {"loop_exited"},
        "retire_target": {"retirement_started"},
        "unlink_complete": {"retirement_complete", "links_resident"},
        "cycle_overrun": {"cycle_overrun"},
        "yield_lost": {"threshold_reached", "yield_lost"},
        "untaken_counted": {"not_taken", "one_taken_edge", "untaken_counted"},
        "dangling_link": {"dangling_link"},
    }
    return Kripke(S=states, S0={"cycle_start"}, R=transitions, L=labels)


def properties() -> list[dict[str, object]]:
    return [
        {
            "name": "cyclic_chain_walk_is_finite",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AF(AtomicProposition("walk_done")),
            "violation": AtomicProposition("cycle_overrun"),
            "expect": True,
        },
        {
            "name": "cyclic_chain_body_count_is_distinct_and_bounded",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("cycle_overrun"))),
            "violation": AtomicProposition("cycle_overrun"),
            "expect": True,
        },
        {
            "name": "loop_threshold_reaches_yield",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("threshold_reached"),
                    AF(AtomicProposition("yielded")),
                )
            ),
            "violation": AtomicProposition("yield_lost"),
            "expect": True,
        },
        {
            "name": "untaken_branch_does_not_count_as_backedge",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("untaken_counted"))),
            "violation": AtomicProposition("untaken_counted"),
            "expect": True,
        },
        {
            "name": "retired_cycle_target_has_no_resident_inbound_link",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition("dangling_link"))),
            "violation": AtomicProposition("dangling_link"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    for guards, model in ((True, build_model()), (False, build_model(guards=False))):
        for prop in properties():
            holds = model.S0.issubset(modelcheck(model, prop["formula"]))
            assert holds == guards, f"{prop['name']}: guards={guards}"
            print(f"[PASS] {prop['name']}: guards={guards}")
