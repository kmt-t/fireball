#!/bin/bash
# Fireball Unified Document Verification Pipeline (Powered by spec-integrator)
# Usage: ./tools/run_all_tests.sh [OPTIONS]
#
# Options:
#   --llm              Run LLM as a Judge semantic audits.
#   --backend B        LLM backend (sakura, ollama, mock)
#   --model M          LLM model name
#   --max-subgraphs N  Number of subgraphs to evaluate with LLM (default: 10)
#   --clean            Run clean audit without cache DB
#   -h, --help         Show this help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RUN_LLM=0
BACKEND="sakura"
MODEL=""
MAX_SUBGRAPHS=10
CLEAN_FLAG=""
REPORT_PATH="doc_report.md"
GRAPH_JSON_PATH="doc_graph.json"

usage() {
    cat <<'EOF'
Fireball Document Quality & Verification Pipeline (spec-integrator)

Usage:
  ./tools/run_all_tests.sh [OPTIONS]

Options:
  --llm              Run LLM as a Judge semantic audits.
  --backend B        LLM backend (sakura, ollama, mock - default: sakura)
  --model M          LLM model name
  --max-subgraphs N  Number of subgraphs to evaluate with LLM (default: 10)
  --clean            Clear cache DB and run clean verification
  -h, --help         Show this help
EOF
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
        --clean)
            CLEAN_FLAG="--clean"
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
echo " Fireball Document Verification Pipeline [spec-integrator]"
echo "================================================================================"

START_TIME=$(date +%s)

# Phase 1: Static & Formal Verification (Format, Traceability, Hierarchy, Formal)
echo ">>> [Phase 1/2] Running Static & Formal Model Verification..."
CHECK_CMD=(uv run --system-certs --project "tools/spec-integrator" python -m spec_integrator.cli check --config spec-integrator.yaml --report "$REPORT_PATH" --graph-json "$GRAPH_JSON_PATH" $CLEAN_FLAG)
if "${CHECK_CMD[@]}"; then
    echo "✔ Quality Gates & Formal Verification: PASSED"
else
    echo "✖ Quality Gates or Formal Verification: FAILED"
    exit 1
fi

# Phase 2: LLM as a Judge (Optional)
if [ "$RUN_LLM" -eq 1 ]; then
    echo -e "\n>>> [Phase 2/2] Running LLM as a Judge Semantic Audits..."
    JUDGE_CMD=(uv run --system-certs --project "tools/spec-integrator" python -m spec_integrator.cli judge --config spec-integrator.yaml --backend "$BACKEND" --max-subgraphs "$MAX_SUBGRAPHS" -o doc_judge_report.json)
    if [ -n "$MODEL" ]; then
        JUDGE_CMD+=(--model "$MODEL")
    fi
    if "${JUDGE_CMD[@]}"; then
        echo "✔ LLM as a Judge: PASSED"
    else
        echo "✖ LLM as a Judge: FAILED"
        exit 1
    fi
else
    echo -e "\n>>> [Phase 2/2] Skipping LLM as a Judge (Use --llm to enable)"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo "================================================================================"
echo " Verification Pipeline Summary: SUCCESS (${ELAPSED}s)"
echo " Report saved to: $REPORT_PATH"
echo "================================================================================"
exit 0
