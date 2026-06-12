---
name: tla-spec-verify
description: Model and verify TLA+ specs, invariants, liveness, deadlocks, ownership, and refinement. Use when working on .tla/.cfg files, TLC runs, counterexamples, verification reports, or Fireball's verify/ models and configs.
---

# TLA+ Spec Verify

## Overview

Use this skill to turn an informal requirement into a finite TLA+ model, run TLC, and decide whether the property holds.

## Workflow

1. Read the source requirement, the current model, and any existing report.
2. Classify the property:
   - Safety: invariant or type invariant
   - Deadlock: blocked-state or enabledness property
   - Liveness: temporal property, with fairness only when justified
   - Ownership or transfer: conservation, uniqueness, or reachability invariant
3. Choose the smallest finite abstraction that still exercises the property.
4. Update or create the model in `verify/models/` and the TLC config in `verify/configs/`.
5. Use the repo's runner in `verify/` instead of invoking TLC directly.
6. Inspect counterexamples, classify the failure, and refine the model or the assumptions.
7. Record the conclusion in `verify/reports/` and preserve traceability to the source docs.

## Modeling Rules

- Keep state finite, explicit, and bounded.
- Separate environment actions from system actions.
- Prefer simple enumerated sets, sequences, and records over encoded integers when clarity is better.
- Encode safety properties as invariants and liveness properties as temporal formulas.
- Add fairness only after confirming the model is otherwise too weak.
- Do not model implementation details that do not affect the property being checked.
- Treat counterexamples as evidence of one of three cases: a real bug, a missing assumption, or an overly coarse abstraction.

## Fireball Integration

- Use `verify/README.md` as the canonical map of runners and directories.
- Align any doc keywords or assumptions with `docs/requires/` and `docs/components/`.
- Keep model, config, and report changes together when the verification intent changes.

## Reference

See [workflow.md](references/workflow.md) for a compact checklist and a minimal TLA+ skeleton.
