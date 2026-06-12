# Fireball Validation Entry Points

## Document validation
- `./tools/run_all_tests.sh`: full document validation pipeline.
  - `--quick`: mechanical checks only; with `--llm`, quick LLM mode is used.
  - `--llm`: add semantic audits.
  - `--backend`: choose the LLM backend.
  - `--model`: override the model name.

- `./tools/run_consistency_check.sh`: specification consistency checker.
  - `--llm`: run checklist-based LLM checks.
  - `--gentable`: regenerate the matrix and checklist CSV before LLM checks.
  - `--model`, `--verbose`, `--debug`: pass through to the unified runner.

- `./tools/run_traceability_audit.sh`: traceability audit.
  - Default: mechanical traceability rules only.
  - `--llm`: add semantic trace alignment.
  - `--model`, `--verbose`, `--debug`: pass through to the unified runner.

## Verification
- `./verify/run_all.sh [all|coos|ipc-deadlock|loader-rollback|vmmio]`: run the TLA+/model verification suite.

## Low-level runner
- `python3 tools/scripts/run_audit.py`: unified audit runner used by the wrappers.
  - `--sync`: refresh keyword and glossary data.
  - `--module FILE`: audit one markdown file.
  - `--all`: audit all component specifications.
  - `--pair FILE_A FILE_B`: run pairwise consistency checks.
  - `--hierarchy --tier {1,2,3}`: run hierarchy audits.
  - `--gentable`: generate the consistency checklist CSV.
  - `--llm`: run checklist audits from the generated CSV.
  - `--rule RULE`: narrow to specific mechanical or semantic checks.
  - `--backend`, `--model`, `--max-tokens`: LLM settings.

## Common combinations
- `./tools/run_all_tests.sh --quick`
- `./tools/run_all_tests.sh --llm --backend ollama`
- `./tools/run_consistency_check.sh --gentable`
- `./tools/run_consistency_check.sh --llm --model gpt-oss-120b`
- `./tools/run_traceability_audit.sh --llm --verbose`
- `./verify/run_all.sh coos`
