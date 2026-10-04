"""CTL model for optional Tier 3 JIT hotspot history and boundary analysis."""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, And, AtomicProposition, Imply, Not

BACKS = ["components/tier3_plugins/jit_runtime.md"]


def build_model(*, guards: bool = True) -> Kripke:
    states = [
        "start",
        "interpreter_recorded",
        "analysis_pending",
        "analyzing",
        "analyzed",
        "jit_running",
        "jit_complete",
        "disabled",
        "overflowed",
        "approximate",
        "destroy_request",
        "destroyed",
        "bad_analysis_in_jit",
        "bad_event_sink_coupling",
        "bad_analysis_stall",
        "bad_loss_unmarked",
        "bad_disabled_storage",
        "bad_destroy_retained",
    ]
    transitions = [
        ("start", "interpreter_recorded"),
        ("start", "jit_running"),
        ("start", "disabled"),
        ("start", "overflowed"),
        ("start", "destroy_request"),
        ("interpreter_recorded", "analysis_pending"),
        ("analysis_pending", "analyzing"),
        ("analyzing", "analyzed"),
        ("analyzed", "analyzed"),
        ("jit_running", "jit_complete"),
        ("jit_complete", "jit_complete"),
        ("disabled", "disabled"),
        ("overflowed", "approximate"),
        ("approximate", "approximate"),
        ("destroy_request", "destroyed"),
        ("destroyed", "destroyed"),
    ]
    if not guards:
        transitions.extend(
            [
                ("jit_running", "bad_analysis_in_jit"),
                ("interpreter_recorded", "bad_event_sink_coupling"),
                ("analysis_pending", "bad_analysis_stall"),
                ("overflowed", "bad_loss_unmarked"),
                ("disabled", "bad_disabled_storage"),
                ("destroy_request", "bad_destroy_retained"),
            ]
        )
    transitions.extend(
        [
            ("bad_analysis_in_jit", "bad_analysis_in_jit"),
            ("bad_event_sink_coupling", "bad_event_sink_coupling"),
            ("bad_analysis_stall", "bad_analysis_stall"),
            ("bad_loss_unmarked", "bad_loss_unmarked"),
            ("bad_disabled_storage", "bad_disabled_storage"),
            ("bad_destroy_retained", "bad_destroy_retained"),
        ]
    )
    labels = {
        "start": {"start"},
        "interpreter_recorded": {"history_present", "hotspot_write"},
        "analysis_pending": {"history_present", "analysis_requested"},
        "analyzing": {"history_present", "analysis_running"},
        "analyzed": {"analysis_complete"},
        "jit_running": {"jit_only"},
        "jit_complete": {"jit_only"},
        "disabled": {"disabled"},
        "overflowed": {"history_overwritten"},
        "approximate": {"history_approximate"},
        "destroy_request": {"destroy_requested", "history_present"},
        "destroyed": {"runtime_destroyed"},
        "bad_analysis_in_jit": {"jit_only", "hotspot_work"},
        "bad_event_sink_coupling": {"hotspot_write", "runtime_event_write"},
        "bad_analysis_stall": {"analysis_requested", "analysis_stalled"},
        "bad_loss_unmarked": {"history_overwritten", "history_exact"},
        "bad_disabled_storage": {"disabled", "history_present"},
        "bad_destroy_retained": {"runtime_destroyed", "history_present"},
    }
    return Kripke(S=states, S0={"start"}, R=transitions, L=labels)


def properties():
    jit_only = AtomicProposition("jit_only")
    hotspot_work = AtomicProposition("hotspot_work")
    hotspot_and_event = And(
        AtomicProposition("hotspot_write"),
        AtomicProposition("runtime_event_write"),
    )
    analysis_requested = AtomicProposition("analysis_requested")
    history_approximate = AtomicProposition("history_approximate")
    history_overwritten = AtomicProposition("history_overwritten")
    disabled = AtomicProposition("disabled")
    history_present = AtomicProposition("history_present")
    runtime_destroyed = AtomicProposition("runtime_destroyed")
    return [
        {
            "name": "jit_only_execution_does_not_record_or_analyze_hotspots",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(jit_only, Not(hotspot_work))),
            "violation": AtomicProposition("hotspot_work"),
            "expect": True,
        },
        {
            "name": "hotspot_history_is_independent_from_runtime_event_sink",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(hotspot_and_event)),
            "violation": AtomicProposition("runtime_event_write"),
            "expect": True,
        },
        {
            "name": "pending_interpreter_history_is_analyzed_at_boundary",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(analysis_requested, AF(AtomicProposition("analysis_complete")))),
            "violation": AtomicProposition("analysis_stalled"),
            "expect": True,
        },
        {
            "name": "overwritten_history_is_reported_as_approximate",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(history_overwritten, AF(history_approximate))),
            "violation": AtomicProposition("history_exact"),
            "expect": True,
        },
        {
            "name": "disabled_jit_extension_has_no_history_storage",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(disabled, Not(history_present))),
            "violation": history_present,
            "expect": True,
        },
        {
            "name": "runtime_destruction_releases_history_storage",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(runtime_destroyed, Not(history_present))),
            "violation": history_present,
            "expect": True,
        },
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    normal_model = build_model()
    for prop in properties():
        result = modelcheck(normal_model, prop["formula"])
        passed = normal_model.S0.issubset(result)
        assert passed == prop["expect"], f"Property {prop['name']} failed"
        print(f"[PASS] Formal Verification: {prop['name']}")

    mutated_model = build_model(guards=False)
    for prop in properties():
        result = modelcheck(mutated_model, prop["formula"])
        passed = mutated_model.S0.issubset(result)
        assert not passed, f"Mutation for {prop['name']} was not detected"
        print(f"[PASS] Mutation Testing: {prop['name']} (guards=False)")
