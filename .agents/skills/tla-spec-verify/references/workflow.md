# TLA+ Verification Workflow

## 1. Scope

- Extract the target behavior and the exact property to prove.
- List the assumptions that may be abstracted away.
- Keep the property narrow enough that TLC can finish on a finite model.

## 2. Build the model

- Model only the state relevant to the property.
- Use a finite universe.
- Define `Init`, `Next`, and `Spec` clearly.
- Add `TypeOK` and any property-specific invariants.
- Keep environment nondeterminism separate from system actions.

## 3. Run TLC

- Use the matching `verify/run_*.sh` script.
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
