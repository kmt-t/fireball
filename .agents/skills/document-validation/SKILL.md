---
name: document-validation
description: Run Fireball's standard validation scripts for document audits, traceability checks, consistency checks, and verification runs. Use when working on docs/, requirements, component specs, or verify/ scenarios and you need the canonical entry points.
---

# Fireball Validation

Use this skill to choose and run Fireball's canonical validation entry points.

## Pick the right runner
- `./tools/run_all_tests.sh`: run the full document validation pipeline.
- `./tools/run_consistency_check.sh`: run the specification consistency checker.
- `./tools/run_traceability_audit.sh`: run the traceability audit.
- `./verify/run_all.sh`: run the model verification suite.

## Use the low-level runner only when needed
- `python3 tools/scripts/run_audit.py` for fine-grained control over module, pair, hierarchy, checklist, and LLM-backed audits.
- Prefer the wrappers above unless the task needs a specific mode or a narrow rule set.

## Operational notes
- The wrapper scripts change to the repository root before execution.
- `--llm` enables semantic checks; pair it with `--backend`, `--model`, and `--max-tokens` when the default backend is not suitable.
- For consistency audits, run `--gentable` before `--llm` so the checklist CSV exists.
- For traceability audits, the mechanical checks run by default; `--llm` adds semantic alignment review.
- The source of truth for policy and documentation rules is in `.claude/rules/`; the files under `tools/docs/` and `verify/` explain the scripts.

## Reference
See [entrypoints.md](references/entrypoints.md) for a command map and common combinations.
