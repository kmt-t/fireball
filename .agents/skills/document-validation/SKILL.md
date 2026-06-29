---
name: document-validation
description: Run the repository's standard validation scripts for document audits, traceability checks, consistency checks, and verification runs. Use when working on docs/, requirements, component specs, or verify/ scenarios and you need the canonical entry points.
---

# Document Validation

Use this skill to choose and run the repository's canonical validation entry points.

## Runner Map
- `./tools/run_all_tests.sh`: run the full document validation pipeline.
  - `--quick`: mechanical checks only; with `--llm`, quick LLM mode is used.
  - `--llm`: add semantic audits.
  - `--backend`: choose the LLM backend.
  - `--model`: override the model name.
- `./tools/run_consistency_check.sh`: run the specification consistency checker.
  - `--llm`: run checklist-based LLM checks.
  - `--gentable`: regenerate the matrix and checklist CSV before LLM checks.
  - `--model`, `--verbose`, `--debug`: pass through to the unified runner.
- `./tools/run_traceability_audit.sh`: run the traceability audit.
  - Default: mechanical traceability rules only.
  - `--llm`: add semantic trace alignment.
  - `--model`, `--verbose`, `--debug`: pass through to the unified runner.
- `./verify/run_all.sh`: run the model verification suite.

## Low-level runner
- `python3 .agents/skills/document-validation/scripts/run_audit.py` for fine-grained control over module, pair, hierarchy, checklist, and LLM-backed audits.
- Prefer the wrappers above unless the task needs a specific mode or a narrow rule set.

## Operational notes
- The wrapper scripts change to the repository root before execution.
- `--llm` enables semantic checks; pair it with `--backend`, `--model`, and `--max-tokens` when the default backend is not suitable.
- For consistency audits, run `--gentable` before `--llm` so the checklist CSV exists.
- For traceability audits, the mechanical checks run by default; `--llm` adds semantic alignment review.
- The source of truth for policy and documentation rules is in `.claude/rules/`; the files under `tools/docs/` and `verify/` explain the scripts.
