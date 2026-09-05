#!/bin/bash
# Fireball LLM High-Risk Keyword Island Reviewer (Bash)
# Reviews connected document islands associated with high-risk keywords.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

KEYWORD=""
MIN_RISK=""
CHECK=""
LIST_CHECKS=""
DRY_RUN=""
BACKEND=""
MODEL=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball High-Risk Keyword Island Review (LLM)

Usage:
  ./tools/llm-keyword-review.sh [OPTIONS]

Options:
  --keyword <name>      Target a specific keyword's connected island.
  --min-risk <N>        Minimum risk score filter for keywords (default from config).
  --check <id>          Run only a specific check ID.
  --list-checks         List all available island review checks and exit.
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
        --keyword)      KEYWORD="$2"; shift 2 ;;
        --min-risk)     MIN_RISK="$2"; shift 2 ;;
        --check)        CHECK="$2"; shift 2 ;;
        --list-checks)  LIST_CHECKS="--list-checks"; shift ;;
        --dry-run)      DRY_RUN="--dry-run"; shift ;;
        --backend)      BACKEND="$2"; shift 2 ;;
        --model)        MODEL="$2"; shift 2 ;;
        -c|--config)    CONFIG="$2"; shift 2 ;;
        -h|--help)      usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "llm-keyword-review"
          "--config" "$CONFIG")
if [[ -n "$KEYWORD" ]]; then CMD_ARGS+=("--keyword" "$KEYWORD"); fi
if [[ -n "$MIN_RISK" ]]; then CMD_ARGS+=("--min-risk" "$MIN_RISK"); fi
if [[ -n "$CHECK" ]]; then CMD_ARGS+=("--check" "$CHECK"); fi
if [[ -n "$LIST_CHECKS" ]]; then CMD_ARGS+=("$LIST_CHECKS"); fi
if [[ -n "$DRY_RUN" ]]; then CMD_ARGS+=("$DRY_RUN"); fi
if [[ -n "$BACKEND" ]]; then CMD_ARGS+=("--backend" "$BACKEND"); fi
if [[ -n "$MODEL" ]]; then CMD_ARGS+=("--model" "$MODEL"); fi

exec uv "${CMD_ARGS[@]}"
