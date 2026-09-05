#!/bin/bash
# Fireball Terminology & Spelling Variance Checker (Bash)
# Indexes embeddings, links similar terms, judges variance via LLM, and outputs report.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

QUICK=""
MAX_PAIRS=20
THRESHOLD="0.80"
BACKEND=""
MODEL=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Terminology & Spelling Variance Checker (LLM)

Usage:
  ./tools/llm-word.sh [OPTIONS]

Options:
  --quick             Run fast static check only (TF-IDF + Levenshtein + Embedding cache; skips LLM).
  --max-pairs <N>     Maximum number of candidate pairs to judge via LLM (default: 20, 0 for unlimited).
  --threshold <F>     Cosine similarity threshold for linking (default: 0.80).
  --backend <name>    LLM backend override (sakura, openrouter, ollama, mock).
  --model <name>      LLM / Embedding model name override.
  -c, --config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, --help          Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)        QUICK="--quick"; shift ;;
        --max-pairs)    MAX_PAIRS="$2"; shift 2 ;;
        --threshold)    THRESHOLD="$2"; shift 2 ;;
        --backend)      BACKEND="$2"; shift 2 ;;
        --model)        MODEL="$2"; shift 2 ;;
        -c|--config)    CONFIG="$2"; shift 2 ;;
        -h|--help)      usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "llm-word"
          "--config" "$CONFIG" "--threshold" "$THRESHOLD"
          "--max-pairs" "$MAX_PAIRS")
if [[ -n "$QUICK" ]]; then CMD_ARGS+=("$QUICK"); fi
if [[ -n "$BACKEND" ]]; then CMD_ARGS+=("--backend" "$BACKEND"); fi
if [[ -n "$MODEL" ]]; then CMD_ARGS+=("--model" "$MODEL"); fi

exec uv "${CMD_ARGS[@]}"
