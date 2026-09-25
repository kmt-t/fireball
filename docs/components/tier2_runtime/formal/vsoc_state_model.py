"""
docs/components/tier2_runtime/formal/vsoc_state_model.py
pyModelChecking model for cooperative JIT LOOP backedge yielding and IRQ/JIT safety.
"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not

BACKS = [
    "components/tier2_runtime/runtime_vsoc.md",
    "components/tier3_executer/interpreter.md",
]


def build_model(*, guards: bool = True) -> Kripke:
    """Model bounded native LOOP exits and interrupt delivery at COOS boundaries.

    The two backedge-count states abstract any finite configured threshold. A
    taken LOOP edge advances the count; threshold arrival returns through the
    C++ interpreter handler and exposes a COOS boundary. The native helper does
    not inspect pending interrupt events itself.
    """
    S = [
        "s_interpreter_run",
        "s_jit_run",
        "s_loop_backedge_count_1",
        "s_loop_backedge_count_2",
        "s_coos_boundary",
        "s_interrupt_handling",
        "s_debugger_paused",
        "s_bad_irq_jit",
        "s_yield_starved",
    ]
    S0 = {"s_interpreter_run"}
    R = [
        ("s_interpreter_run", "s_jit_run"),
        ("s_interpreter_run", "s_coos_boundary"),
        ("s_jit_run", "s_loop_backedge_count_1"),
        ("s_loop_backedge_count_1", "s_loop_backedge_count_2"),
        # The second abstract count reaches the finite configured threshold.
        ("s_loop_backedge_count_2", "s_coos_boundary"),
        # COOS may resume the guest or dispatch an event after the boundary.
        ("s_coos_boundary", "s_interpreter_run"),
        ("s_coos_boundary", "s_interrupt_handling"),
        ("s_interrupt_handling", "s_interpreter_run"),
        # A configured debugger can pause the interpreter at a debug boundary.
        ("s_interpreter_run", "s_debugger_paused"),
        ("s_debugger_paused", "s_interpreter_run"),
        ("s_bad_irq_jit", "s_bad_irq_jit"),
        ("s_yield_starved", "s_yield_starved"),
    ]
    if not guards:
        # Mutation 1: permit interrupt handling to start during native JIT.
        R = [*R, ("s_jit_run", "s_bad_irq_jit")]
        # Mutation 2: remove the finite-count route back to the COOS boundary.
        R = [
            edge
            for edge in R
            if edge != ("s_loop_backedge_count_2", "s_coos_boundary")
        ]
        R = [*R, ("s_loop_backedge_count_2", "s_yield_starved")]

    L = {
        "s_interpreter_run": {"running", "interp_mode"},
        "s_jit_run": {"running", "jit_mode"},
        "s_loop_backedge_count_1": {"running", "jit_mode", "loop_counter"},
        "s_loop_backedge_count_2": {"running", "jit_mode", "loop_counter"},
        "s_coos_boundary": {"coos_boundary"},
        "s_interrupt_handling": {"handling_irq"},
        "s_debugger_paused": {"paused", "debug_safe"},
        "s_bad_irq_jit": {"handling_irq", "jit_mode"},
        "s_yield_starved": {"running", "jit_mode", "yield_starved"},
    }
    return Kripke(S=S, S0=S0, R=R, L=L)


def properties():
    bad = And(AtomicProposition("handling_irq"), AtomicProposition("jit_mode"))
    return [
        {
            "name": "irq_jit_race_freedom_proof",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": True,
        },
        {
            "name": "jit_backedge_yields_to_coos",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(
                Imply(
                    AtomicProposition("jit_mode"),
                    AF(AtomicProposition("coos_boundary")),
                )
            ),
            "violation": AtomicProposition("yield_starved"),
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    km = build_model(guards=True)
    for prop in properties():
        res = modelcheck(km, prop["formula"])
        passed = km.S0.issubset(res)
        if passed != prop["expect"]:
            raise AssertionError(f"Normal model property failed: {prop['name']}")
        print(f"[PASS] {prop['name']} (guards=True)")

    mutation_km = build_model(guards=False)
    for prop in properties():
        res = modelcheck(mutation_km, prop["formula"])
        passed = mutation_km.S0.issubset(res)
        if passed == prop["expect"]:
            raise AssertionError(f"Mutation did not invalidate property: {prop['name']}")
        print(f"[PASS] {prop['name']} mutation rejected (guards=False)")
