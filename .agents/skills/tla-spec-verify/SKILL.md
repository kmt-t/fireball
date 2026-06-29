---
name: tla-spec-verify
description: Model and verify TLA+ specs, invariants, liveness, deadlocks, ownership, refinement, and runner naming resolution. Use when working on .tla/.cfg files, TLC runs, counterexamples, verification reports, verify/run_*.sh, or when resolving a component name to the matching model/config/report.
---

# TLA+ Spec Verify

## Overview

Use this skill to turn an informal requirement into a finite TLA+ model, run TLC, and decide whether the property holds.

## Naming And Runners

- Treat the component name as the primary handle.
- Normalize names by lowercasing and removing `_` and `-`.
- Resolve the normalized name against `verify/models/`, `verify/configs/`, and `verify/reports/` basenames.
- Use `verify/run_component.sh <component-name>` for one target and `verify/run_all.sh [all|list|<component-name>]` for batch or discovery.
- Run `verify/run_all.sh list` before checking a new target when you need to confirm what the runner can resolve.
- Keep compatibility wrappers thin; do not add per-component TLC command fragments when a shared runner can cover the case.

## Workflow

1. Read the source requirement, the current model, and any existing report.
2. Classify the property:
   - Safety: invariant or type invariant
   - Deadlock: blocked-state or enabledness property
   - Liveness: temporal property, with fairness only when justified
   - Ownership or transfer: conservation, uniqueness, or reachability invariant
3. Choose the smallest finite abstraction that still exercises the property.
4. Resolve the target through the naming rule before editing scripts or invoking TLC.
5. Update or create the model in `verify/models/` and the TLC config in `verify/configs/`.
6. Use the repo's runner in `verify/` instead of invoking TLC directly.
7. Inspect counterexamples, classify the failure, and refine the model or the assumptions.
8. Record the conclusion in `verify/reports/` and preserve traceability to the source docs.

## Modeling Rules

- Keep state finite, explicit, and bounded.
- Separate environment actions from system actions.
- Prefer simple enumerated sets, sequences, and records over encoded integers when clarity is better.
- Encode safety properties as invariants and liveness properties as temporal formulas.
- Add fairness only after confirming the model is otherwise too weak.
- Do not model implementation details that do not affect the property being checked.
- Keep the naming rule deterministic; if multiple artifacts could match the same component name, rename the artifact rather than adding more special cases.
- Treat counterexamples as evidence of one of three cases: a real bug, a missing assumption, or an overly coarse abstraction.

## Repository Integration

- Use `verify/README.md` as the canonical map of runners and directories.
- Treat `verify/components.sh` and `verify/run_component.sh` as the canonical name-resolution and execution contract.
- Align any doc keywords or assumptions with `docs/requires/` and `docs/components/`.
- Keep model, config, and report changes together when the verification intent changes.

## Minimal Skeleton

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

## Paths

- `verify/models/`: `.tla` specs
- `verify/configs/`: `.cfg` files
- `verify/reports/`: result summaries
- `verify/run_*.sh`: canonical runners
