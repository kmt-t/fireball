#!/bin/bash
# Fireball Unified Document Verification Pipeline (Powered by spec-integrator)
# Usage: ./tools/run_all_tests.sh [OPTIONS]
#
# Options:
#   --llm              Run LLM as a Judge semantic audits.
#   --assess           Run Complexity & Risk Assessment.
#   --backend B        LLM backend (sakura, ollama, mock - default: sakura)
#   --model M          LLM model name
#   --max-subgraphs N  Number of subgraphs to evaluate with LLM (default: 10)
#   --max-sections N   Number of sections to assess (default: 15)
#   --clean            Run clean audit without cache DB
#   -h, --help         Show this help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RUN_LLM=0
RUN_ASSESS=0
BACKEND="sakura"
MODEL=""
MAX_SUBGRAPHS=10
MAX_SECTIONS=15
CLEAN_FLAG=""
REPORTS_DIR="reports"
REPORT_PATH="reports/doc_report.md"
GRAPH_JSON_PATH="reports/doc_graph.json"

mkdir -p "$REPORTS_DIR"

usage() {
    cat <<'EOF'
Fireball Document Quality & Verification Pipeline (spec-integrator)

Usage:
  ./tools/run_all_tests.sh [OPTIONS]

Options:
  --llm              Run LLM as a Judge semantic audits.
  --assess           Run Complexity & Risk Assessment.
  --backend B        LLM backend (sakura, ollama, mock - default: sakura)
  --model M          LLM model name
  --max-subgraphs N  Number of subgraphs to evaluate with LLM (default: 10)
  --max-sections N   Number of sections to assess (default: 15)
  --clean            Run clean audit without cache DB
  -h, --help         Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm)
            RUN_LLM=1
            shift
            ;;
        --assess)
            RUN_ASSESS=1
            shift
            ;;
        --backend)
            BACKEND="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --max-subgraphs)
            MAX_SUBGRAPHS="$2"
            shift 2
            ;;
        --max-sections)
            MAX_SECTIONS="$2"
            shift 2
            ;;
        --clean)
            CLEAN_FLAG="--clean"
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            ;;
    esac
done

echo "================================================================================"
echo " Fireball Document Verification Pipeline [spec-integrator]"
echo "================================================================================"

echo ">>> [Phase 1/3] Running Static & Formal Model Verification..."
CHECK_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator" "python" "-m" "spec_integrator.cli" "check" "--config" "spec-integrator.yaml" "--report" "$REPORT_PATH" "--graph-json" "$GRAPH_JSON_PATH")
if [ -n "$CLEAN_FLAG" ]; then
    CHECK_ARGS+=("$CLEAN_FLAG")
fi

uv "${CHECK_ARGS[@]}"

echo "✔ Quality Gates & Formal Verification: PASSED"

if [ "$RUN_ASSESS" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 2/3] Running Content Complexity & Risk Assessment..."
    ASSESS_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator" "python" "-m" "spec_integrator.cli" "assess" "--config" "spec-integrator.yaml" "--backend" "$BACKEND" "--max-sections" "$MAX_SECTIONS" "-o" "reports/doc_risk_report.json" "-r" "reports/doc_risk_report.md")
    if [ -n "$MODEL" ]; then
        ASSESS_ARGS+=("--model" "$MODEL")
    fi
    uv "${ASSESS_ARGS[@]}"
    echo "✔ Content Complexity & Risk Assessment: PASSED"
else
    echo ""
    echo ">>> [Phase 2/3] Skipping Risk Assessment (Use --assess to enable)"
fi

if [ "$RUN_LLM" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 3/3] Running LLM as a Judge Semantic Audits..."
    JUDGE_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator" "python" "-m" "spec_integrator.cli" "judge" "--config" "spec-integrator.yaml" "--backend" "$BACKEND" "--max-subgraphs" "$MAX_SUBGRAPHS" "-o" "reports/doc_judge_report.json")
    if [ -n "$MODEL" ]; then
        JUDGE_ARGS+=("--model" "$MODEL")
    fi
    uv "${JUDGE_ARGS[@]}"
    echo "✔ LLM as a Judge: PASSED"
else
    echo ""
    echo ">>> [Phase 3/3] Skipping LLM as a Judge (Use --llm to enable)"
fi

echo ""
echo "================================================================================"
echo " Verification Pipeline Summary: SUCCESS"
echo " Report saved to: $REPORT_PATH"
echo "================================================================================"
exit 0
