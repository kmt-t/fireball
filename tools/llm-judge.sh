#!/bin/bash
# Fireball Anchored LLM Semantic Judge Runner (Bash)
# Audits all {VERIFY_LLM}-tagged documents (whole-document + cross-document island review)
# and persists anchored verdicts so the Obligation Verifier can discharge OBLIG-JUDGE-* / OBLIG-DOC-JUDGE-*.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

MAX_DOCUMENTS=20
MAX_SUBGRAPHS=20
EXHAUSTIVE=""
CHECK=""
LIST_CHECKS=""
DRY_RUN=""
BACKEND=""
MODEL=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Anchored LLM Semantic Judge

Usage:
  ./tools/llm-judge.sh [OPTIONS]

Options:
  --max-documents <N>   Max tagged documents to audit in whole-document mode (default: 20, 0 for unlimited).
  --max-subgraphs <N>   Max document islands to audit in cluster mode (default: 20, 0 for unlimited).
  -a, --exhaustive      Ignore --max-documents/--max-subgraphs and audit full coverage.
  --check <id>          Run only a specific check ID.
  --list-checks         List all configured single/cluster review checks and exit.
  --dry-run             Display prompts without calling the LLM backend or persisting results.
  --backend <name>      LLM backend override (openrouter, sakura, ollama, mock).
  --model <name>        LLM model name override.
  -c, --config <path>   Path to configuration file (default: spec-integrator.yaml).
  -h, --help            Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-documents)  MAX_DOCUMENTS="$2"; shift 2 ;;
        --max-subgraphs)  MAX_SUBGRAPHS="$2"; shift 2 ;;
        -a|--exhaustive)  EXHAUSTIVE="-a"; shift ;;
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
          "python" "-m" "spec_integrator.cli" "llm-judge"
          "--config" "$CONFIG" "--max-documents" "$MAX_DOCUMENTS"
          "--max-subgraphs" "$MAX_SUBGRAPHS")
if [[ -n "$EXHAUSTIVE" ]]; then CMD_ARGS+=("$EXHAUSTIVE"); fi
if [[ -n "$CHECK" ]]; then CMD_ARGS+=("--check" "$CHECK"); fi
if [[ -n "$LIST_CHECKS" ]]; then CMD_ARGS+=("$LIST_CHECKS"); fi
if [[ -n "$DRY_RUN" ]]; then CMD_ARGS+=("$DRY_RUN"); fi
if [[ -n "$BACKEND" ]]; then CMD_ARGS+=("--backend" "$BACKEND"); fi
if [[ -n "$MODEL" ]]; then CMD_ARGS+=("--model" "$MODEL"); fi

exec uv "${CMD_ARGS[@]}"
