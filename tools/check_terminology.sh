#!/usr/bin/env bash
# Fireball Terminology & Spelling Variance Checker (Bash)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

QUICK_MODE=0
MAX_PAIRS=20
BACKEND=""

usage() {
    cat <<'EOF'
Fireball Terminology & Spelling Variance Checker (Bash)

Usage:
  ./tools/check_terminology.sh [OPTIONS]

Options:
  --quick             Run fast static check only (TF-IDF + Levenshtein + Embedding cache; skips LLM).
  --max-pairs <N>     Maximum number of candidate pairs to judge via LLM (default: 20, 0 for unlimited).
  --backend <name>    LLM backend override (sakura, openrouter, ollama, mock).
  -h, --help          Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)        QUICK_MODE=1; shift ;;
        --max-pairs)    MAX_PAIRS="$2"; shift 2 ;;
        --backend)      BACKEND="$2"; shift 2 ;;
        -h|--help)      usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

echo "================================================================================"
echo " Fireball Terminology & Spelling Variance Checker Pipeline"
echo "================================================================================"

SPEC_INT=(uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli)

# Step 1: Sync TF-IDF Term Database
echo ""
echo ">>> [1/4] Extracting terminology via TF-IDF (sync)..."
"${SPEC_INT[@]}" sync --config spec-integrator.yaml

# Step 2: Index Term Embeddings & Compute Similarities
echo ""
echo ">>> [2/4] Indexing embeddings & computing similarity pairs (term-index)..."
"${SPEC_INT[@]}" term-index --config spec-integrator.yaml

# Step 3: LLM Contextual Variance Judgment
if [ "$QUICK_MODE" -eq 1 ]; then
    echo ""
    echo ">>> [3/4] Skipping LLM contextual judgment (--quick specified)..."
else
    echo ""
    echo ">>> [3/4] Running LLM contextual variance judgment (term-judge, max: $MAX_PAIRS pairs)..."
    JUDGE_ARGS=(term-judge --config spec-integrator.yaml --max-pairs "$MAX_PAIRS")
    if [ -n "$BACKEND" ]; then
        JUDGE_ARGS+=(--backend "$BACKEND")
    fi
    "${SPEC_INT[@]}" "${JUDGE_ARGS[@]}"
fi

# Step 4: Consolidated Terminology Report
echo ""
echo ">>> [4/4] Generating consolidated terminology report..."
"${SPEC_INT[@]}" term-report --config spec-integrator.yaml

echo ""
echo "✔ Terminology check complete."
