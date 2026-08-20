#!/bin/bash
# Fireball Unified Document Audit & Test Runner (Graph & LLM Judge Architecture)
# Usage: ./tools/run_all_tests.sh [OPTIONS]
#
# Options:
#   --llm            Run Graph-based LLM as a Judge semantic audits.
#   --backend B      LLM backend (gemini, sakura, openrouter, ollama, mock)
#   --model M        LLM model name
#   --max-subgraphs  Number of subgraphs to evaluate with LLM (default: 10)
#   -h, --help       Show this help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RUN_LLM=0
BACKEND="auto"
MODEL=""
MAX_SUBGRAPHS=10
FAILED=0

usage() {
    cat <<'EOF'
Fireball Unified Document Verification Pipeline (DocGraph & LLM Judge)

Usage:
  ./tools/run_all_tests.sh [OPTIONS]

Options:
  --llm              Run Graph-based LLM as a Judge semantic audits.
  --backend B        LLM backend (gemini, sakura, openrouter, ollama, mock)
  --model M          LLM model name
  --max-subgraphs N  Number of subgraphs to evaluate with LLM (default: 10)
  -h, --help         Show this help
EOF
}

print_section() {
    printf '\n>>> %s\n' "$1"
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --llm)
            RUN_LLM=1
            ;;
        --backend)
            if [[ "$#" -lt 2 ]]; then
                echo "Missing value for --backend"
                exit 1
            fi
            BACKEND="$2"
            shift
            ;;
        --model)
            if [[ "$#" -lt 2 ]]; then
                echo "Missing value for --model"
                exit 1
            fi
            MODEL="$2"
            shift
            ;;
        --max-subgraphs)
            if [[ "$#" -lt 2 ]]; then
                echo "Missing value for --max-subgraphs"
                exit 1
            fi
            MAX_SUBGRAPHS="$2"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown parameter: $1"
            exit 1
            ;;
    esac
    shift
done

echo "================================================================================"
echo " Fireball Unified Document Verification Pipeline (DocGraph Architecture)"
echo "================================================================================"
if [ "$RUN_LLM" -eq 1 ]; then
    echo " Mode: Mechanical Graph Verification + LLM Subgraph Judge Audits"
    echo " Backend: ${BACKEND}"
    echo " Max Subgraphs: ${MAX_SUBGRAPHS}"
else
    echo " Mode: Static Graph Verification Only (Use --llm to enable LLM Judge)"
fi
echo "================================================================================"

START_TIME=$(date +%s)
mkdir -p temp

# Phase 1: Build DocGraph & Run Static Graph Analysis
print_section "[Phase 1/3] Building DocGraph and Verifying Graph Topology..."
if uv run python tools/doc_graph.py docs --connected-only; then
    printf "✔ DocGraph Construction & Topology Check: PASSED\n"
else
    printf "✖ DocGraph Topology Check: FAILED\n"
    FAILED=1
fi

# Phase 2: Extract Candidate Evaluation Subgraphs
print_section "[Phase 2/3] Extracting Requirement-centric Evaluation Subgraphs..."
if uv run python tools/doc_graph.py docs --subgraphs --out temp/subgraphs.json; then
    printf "✔ Subgraph Extraction: PASSED (Saved to temp/subgraphs.json)\n"
else
    printf "✖ Subgraph Extraction: FAILED\n"
    FAILED=1
fi

# Phase 3: LLM as a Judge Semantic Subgraph Audit
if [ "$RUN_LLM" -eq 1 ]; then
    print_section "[Phase 3/3] Running Graph-based LLM as a Judge Audit..."
    JUDGE_ARGS=(--backend "$BACKEND" --max-subgraphs "$MAX_SUBGRAPHS" --out temp/judge_report.json)
    if [ -n "$MODEL" ]; then
        JUDGE_ARGS+=(--model "$MODEL")
    fi

    if uv run python tools/doc_judge.py docs "${JUDGE_ARGS[@]}"; then
        printf "✔ LLM Subgraph Judge Audit: PASSED\n"
    else
        printf "✖ LLM Subgraph Judge Audit: FAILED\n"
        FAILED=1
    fi
else
    print_section "[Phase 3/3] Skipping LLM Subgraph Judge Audits (Use --llm to enable)"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo -e "\n================================================================================"
echo " Verification Pipeline Summary"
echo "================================================================================"
echo " Elapsed Time: ${ELAPSED}s"
if [ "$FAILED" -eq 0 ]; then
    echo " Result: SUCCESS (All enabled checks passed)"
    exit 0
fi

echo " Result: FAILURE (Some checks failed)"
exit 1
