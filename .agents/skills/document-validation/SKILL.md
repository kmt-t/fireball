---
name: document-validation
description: Run the repository's standard validation scripts for document audits, traceability checks, consistency checks, and verification runs. Use when working on docs/, requirements, component specs, or verify/ scenarios and you need the canonical entry points.
---

# Document Validation

Use this skill to choose and run the repository's canonical validation entry points.

## Runner Map
- `./tools/run_all_tests.sh`: run the full document validation pipeline.
  - `--llm`: run matrix-based LLM semantic audits.
  - `--backend`: choose the LLM backend.
  - `--model`: override the model name.
- `./verify/run_all.sh`: run the model verification suite.

## Low-level runner
- `python3 .agents/skills/document-validation/scripts/run_audit.py` for fine-grained control over module, pair, hierarchy, checklist, and LLM-backed audits.
- Prefer the wrappers above unless the task needs a specific mode or a narrow rule set.

## Operational notes
- The wrapper scripts change to the repository root before execution.
- `--llm` enables semantic checks; pair it with `--backend`, `--model`, and `--max-tokens` when the default backend is not suitable.
- Run `run_audit.py --gentable` to regenerate the review matrix and document tiers CSV.
- The source of truth for policy and documentation rules is in `.claude/rules/`; the files under `tools/docs/` and `verify/` explain the scripts.
