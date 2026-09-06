#!/bin/bash
# Fireball LLM Single Document & High-Risk Island Reviewer (Bash)
# Reviews single document section-by-section and related high-risk keyword islands.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

FILE=""
TAGGED=""
ALL=""
RISK_THRESHOLD=""
CHECK=""
LIST_CHECKS=""
DRY_RUN=""
BACKEND=""
MODEL=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Single Document & High-Risk Island Review (LLM)

Usage:
  ./tools/llm-single-review.sh [OPTIONS]

Options:
  -f, --file <path>     Path to markdown document to review.
  -t, --tagged          Review only documents tagged with {VERIFY_LLM}.
  --all                 Review all documents in the project.
  --risk-threshold <N>  Override high-risk score threshold (default from config).
  --check <id>          Run only a specific check ID.
  --list-checks         List all available single review checks and exit.
  --dry-run             Display prompt without calling LLM backend.
  --backend <name>      LLM backend override (openrouter, sakura, ollama, mock).
  --model <name>        LLM model name override.
  -c, --config <path>   Path to configuration file (default: spec-integrator.yaml).
  -h, --help            Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--file)        FILE="$2"; shift 2 ;;
        -t|--tagged)      TAGGED="--tagged"; shift ;;
        --all)            ALL="--all"; shift ;;
        --risk-threshold) RISK_THRESHOLD="$2"; shift 2 ;;
        --check)          CHECK="$2"; shift 2 ;;
        --list-checks)    LIST_CHECKS="--list-checks"; shift ;;
        --dry-run)        DRY_RUN="--dry-run"; shift ;;
        --backend)        BACKEND="$2"; shift 2 ;;
        --model)          MODEL="$2"; shift 2 ;;
        -c|--config)      CONFIG="$2"; shift 2 ;;
        -h|--help)        usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "llm-single-review"
          "--config" "$CONFIG")
if [[ -n "$FILE" ]]; then CMD_ARGS+=("--file" "$FILE"); fi
if [[ -n "$TAGGED" ]]; then CMD_ARGS+=("$TAGGED"); fi
if [[ -n "$ALL" ]]; then CMD_ARGS+=("$ALL"); fi
if [[ -n "$RISK_THRESHOLD" ]]; then CMD_ARGS+=("--risk-threshold" "$RISK_THRESHOLD"); fi
if [[ -n "$CHECK" ]]; then CMD_ARGS+=("--check" "$CHECK"); fi
if [[ -n "$LIST_CHECKS" ]]; then CMD_ARGS+=("$LIST_CHECKS"); fi
if [[ -n "$DRY_RUN" ]]; then CMD_ARGS+=("$DRY_RUN"); fi
if [[ -n "$BACKEND" ]]; then CMD_ARGS+=("--backend" "$BACKEND"); fi
if [[ -n "$MODEL" ]]; then CMD_ARGS+=("--model" "$MODEL"); fi

exec uv "${CMD_ARGS[@]}"
