"""QA計測構成のトレース実行、計数対象外操作、飽和、退役記録の抽象モデル。"""

from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, AtomicProposition, Not

BACKS = ["components/tier3_plugins/benchmarks/jit_runtime_bench_spec.md"]
State = tuple[int, int, str, int, int, int]


def build_model(*, guards: bool = True) -> Kripke:
    """2 bodyの直線chainをモデル化する。カウンタ上限2はuint32飽和の抽象である。"""
    initial: State = (0, 0, "initial", 0, 0, 0)
    # Physical counter/report combinations exist in the state space even when
    # guarded transitions cannot reach them from the initial state.
    states: list[State] = [
        initial,
        (1, 0, "execute", 0, 0, 0),
        (1, 0, "lookup", 0, 0, 0),
        (0, 2, "execute", 2, 2, 0),
        (1, 1, "retire", 1, 1, 0),
    ]
    seen = set(states)
    transitions: list[tuple[State, State]] = []
    labels: dict[State, set[str]] = {}
    index = 0
    while index < len(states):
        state = states[index]
        index += 1
        source, target, action, previous_source, previous_target, recorded = state
        violations: set[str] = set()
        if action == "execute":
            if target != min(previous_target + 1, 2):
                violations.add("missed_chain")
            if source != min(previous_source + 1, 2):
                violations.add("wrapped")
        if action in ("lookup", "promote", "snapshot", "fallback"):
            if source != previous_source or target != previous_target:
                violations.add("non_execution_counted")
        if action == "retire" and recorded != previous_source:
            violations.add("lost_record")
        labels[state] = violations
        if violations:
            transitions.append((state, state))
            continue
        for operation in (
            "execute",
            "lookup",
            "promote",
            "snapshot",
            "fallback",
            "reset",
            "retire",
        ):
            next_source, next_target, report = source, target, 0
            if operation == "execute":
                next_source = min(source + 1, 2)
                next_target = min(target + 1, 2)
            elif operation == "reset":
                next_source, next_target = 0, 0
            elif operation == "retire":
                report = source
            following: State = (next_source, next_target, operation, source, target, report)
            successors = [following]
            if not guards:
                if operation == "execute":
                    successors.append((next_source, target, operation, source, target, report))
                    successors.append(
                        ((source + 1) % 3, next_target, operation, source, target, report)
                    )
                elif operation == "lookup":
                    successors.append(
                        (min(source + 1, 2), target, operation, source, target, report)
                    )
                elif operation == "retire":
                    successors.append((source, target, operation, source, target, 0))
            for following in successors:
                transitions.append((state, following))
                if following not in seen:
                    seen.add(following)
                    states.append(following)
    return Kripke(
        S=[str(state) for state in states],
        S0={str(initial)},
        R=[(str(source), str(target)) for source, target in transitions],
        L={str(state): values for state, values in labels.items()},
    )


def properties():
    return [
        {
            "name": name,
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(AtomicProposition(name))),
            "violation": AtomicProposition(name),
            "expect": True,
        }
        for name in ("missed_chain", "non_execution_counted", "wrapped", "lost_record")
    ]


if __name__ == "__main__":
    from pyModelChecking.CTL import modelcheck

    for guards, model in ((True, build_model()), (False, build_model(guards=False))):
        for prop in properties():
            holds = model.S0.issubset(modelcheck(model, prop["formula"]))
            assert holds == guards, f"{prop['name']}: guards={guards}"
            print(f"[PASS] {prop['name']}: guards={guards}")
