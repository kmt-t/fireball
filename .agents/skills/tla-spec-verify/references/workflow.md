# TLA+ Verification Workflow

## 1. Scope

- Extract the target behavior and the exact property to prove.
- List the assumptions that may be abstracted away.
- Keep the property narrow enough that TLC can finish on a finite model.

## 1.1 Name Resolution

- Treat the component name as the stable input.
- Normalize the input by lowercasing and removing `_` and `-`.
- Resolve the normalized name against the basenames in `verify/models/`, `verify/configs/`, and `verify/reports/`.
- Prefer one canonical component id per verification target.
- Keep `verify/run_component.sh` as the single-target runner and `verify/run_all.sh` as the batch/list entry point.
- If two artifacts could plausibly match the same name, stop and rename the artifact instead of extending the matching logic.

## 2. Build the model

- Model only the state relevant to the property.
- Use a finite universe.
- Define `Init`, `Next`, and `Spec` clearly.
- Add `TypeOK` and any property-specific invariants.
- Keep environment nondeterminism separate from system actions.

## 3. Run TLC

- Use the matching `verify/run_component.sh <component-name>` script or the compatibility `verify/run_*.sh` wrapper.
- Use `verify/run_all.sh list` to confirm what the runner can resolve before checking a new target.
- Keep constants and bounds as tight as possible while still covering the scenario.
- Prefer one minimal run that reproduces the concern, then widen only if needed.

## 4. Read results

- No counterexample: confirm the property matches the intended scope.
- Counterexample: decide whether to fix the model, strengthen assumptions, or weaken the claim.
- Liveness failure: check fairness, stuttering, and environment nondeterminism first.

## 5. Traceability

- Tie each invariant and assumption back to a requirement or design keyword.
- Record any deliberate abstraction gap in the report.
- Keep model, config, and report changes aligned with the source docs.
- Keep report and filename naming aligned with the canonical component name.

## Minimal skeleton

```tla
---- MODULE Example ----
EXTENDS Naturals, TLC

CONSTANTS ...
VARIABLES ...

TypeOK == ...
Init == ...
Next == ...
Spec == Init /\ [][Next]_vars

THEOREM Spec => []TypeOK
====
```

## Fireball paths

- `verify/models/`: `.tla` specs
- `verify/configs/`: `.cfg` files
- `verify/reports/`: result summaries
- `verify/run_*.sh`: canonical runners
