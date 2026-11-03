"""CTL model for the Tier 3 JIT compile admission record of one BasicBlock.

The record holds a strike count and a lifetime count (one byte per block). The model covers the
decision for one candidate block: the strike rule of the eviction, and the rejection rule of the
admission (rules (ii) and (iii) of jit_runtime.md 8.2). The rank rule (iv) is undecided and is not
modeled. The stage limit is fixed to S = 3, the middle stage.
"""

from collections import deque

from pyModelChecking import Kripke
from pyModelChecking.CTL import AF, AG, AX, AtomicProposition, Imply, Not

BACKS = ["components/tier3_plugins/jit_runtime.md"]

STAGE_LIMIT = 3
STRIKE_MAX = 4
NO_HISTORY = "n"
STRIKES = [NO_HISTORY, *range(STRIKE_MAX + 1)]
PHASES = ["idle", "queued", "compiled", "rejected"]


def _state(strike, phase: str) -> str:
    return f"{phase}_{strike}"


def _over_limit(strike) -> bool:
    return strike != NO_HISTORY and strike >= STAGE_LIMIT


def _successors(state: str, *, guards: bool) -> list[str]:
    phase, _, strike_text = state.partition("_")
    strike = NO_HISTORY if strike_text == NO_HISTORY else int(strike_text)
    if phase == "idle":
        return [_state(strike, "queued")]
    if phase == "queued":
        # Rule (i): a candidate below the score threshold is rejected.
        result = [_state(strike, "rejected")]
        if strike == NO_HISTORY:
            # Rule (ii): a block without history is compiled.
            result.append(_state(strike, "compiled"))
        elif _over_limit(strike):
            # Rule (iii): a block whose strikes reach the stage limit is rejected.
            if not guards:
                result.append(_state(strike, "compiled"))
        else:
            # The rank rule (iv) is undecided, so a block below the limit compiles.
            result.append(_state(strike, "compiled"))
        return result
    if phase == "compiled":
        # Eviction: a short life adds a strike up to the limit, a long life resets to zero.
        previous = 0 if strike == NO_HISTORY else strike
        return [_state(min(previous + 1, STAGE_LIMIT), "idle"), _state(0, "idle")]
    return [_state(strike, "rejected")]


def build_model(*, guards: bool = True) -> Kripke:
    start = _state(NO_HISTORY, "idle")
    reachable = {start}
    pending = deque([start])
    edges: list[tuple[str, str]] = []
    while pending:
        state = pending.popleft()
        for successor in _successors(state, guards=guards):
            edges.append((state, successor))
            if successor not in reachable:
                reachable.add(successor)
                pending.append(successor)
    labels = {}
    for state in reachable:
        phase, _, strike_text = state.partition("_")
        strike = NO_HISTORY if strike_text == NO_HISTORY else int(strike_text)
        props = set()
        if _over_limit(strike) and phase == "queued":
            props.add("over_limit_queued")
        if _over_limit(strike) and phase == "compiled":
            props.add("over_limit_compiled")
        if phase == "compiled":
            props.add("compiled")
        if phase == "rejected":
            props.add("rejected")
        labels[state] = props
    return Kripke(S=sorted(reachable), S0={start}, R=edges, L=labels)


def properties():
    compiled = AtomicProposition("compiled")
    over_limit_queued = AtomicProposition("over_limit_queued")
    rejected = AtomicProposition("rejected")
    return [
        {
            "name": "an over-limit queued block is not compiled in the next step",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Imply(over_limit_queued, AX(Not(compiled)))),
            "violation": compiled,
            "expect": True,
        },
        {
            "name": "an over-limit queued block is rejected",
            "kind": "liveness",
            "logic": "CTL",
            "formula": AG(Imply(over_limit_queued, AF(rejected))),
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
