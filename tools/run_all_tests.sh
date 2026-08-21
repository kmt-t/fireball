#!/bin/bash
# Fireball Unified Document Verification Pipeline (Powered by spec-integrator)
#
# Phase ordering matters. `assess` decides WHAT must be verified and `judge`
# performs the semantic audit; `check` is the gate that consumes both verdicts
# and is the only authoritative result. Running `check` first — as this script
# used to — meant the risk assessment could demand verification that the gate
# had already declared unnecessary.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RUN_LLM=0
RUN_ASSESS=0
BACKEND="sakura"
MODEL=""
MAX_SUBGRAPHS=10
MAX_SECTIONS=15
MAX_SECTIONS_SET=0
MAX_SUBGRAPHS_SET=0
NO_STRICT=0
RUN_SYNC=0
CLEAN_FLAG=""
REPORTS_DIR="reports"
REPORT_PATH="reports/doc_report.md"
GRAPH_JSON_PATH="reports/doc_graph.json"
RISK_REPORT="reports/doc_risk_report.json"
JUDGE_REPORT="reports/doc_judge_report.json"

mkdir -p "$REPORTS_DIR"

usage() {
    cat <<'EOF'
Fireball Document Quality & Verification Pipeline (spec-integrator)

Usage:
  ./tools/run_all_tests.sh [OPTIONS]

Options:
  --assess           Run the Complexity & Risk Assessment (establishes obligations).
  --llm              Run the LLM as a Judge semantic audit.
  --full             Run everything with full coverage (implies --assess --llm).
  --backend B        LLM backend (sakura, ollama, mock - default: sakura)
  --model M          LLM model name
  --max-subgraphs N  Subgraphs to evaluate with the LLM judge (default: 10)
  --max-sections N   Sections to risk-assess (default: 15)
  --no-strict        Accept a partial risk assessment instead of failing.
  --sync             Record the current spec state as the propagation baseline, then exit.
  --clean            Run a clean audit without the cache DB.
  -h, --help         Show this help

Phases:
  1. assess  - decides what must be verified   (skippable; the stored report is reused)
  2. judge   - semantic audit                  (skippable)
  3. check   - quality gates, authoritative    (always runs)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm)           RUN_LLM=1; shift ;;
        --assess)        RUN_ASSESS=1; shift ;;
        --full)          RUN_ASSESS=1; RUN_LLM=1; shift ;;
        --backend)       BACKEND="$2"; shift 2 ;;
        --model)         MODEL="$2"; shift 2 ;;
        --max-subgraphs) MAX_SUBGRAPHS="$2"; MAX_SUBGRAPHS_SET=1; shift 2 ;;
        --max-sections)  MAX_SECTIONS="$2"; MAX_SECTIONS_SET=1; shift 2 ;;
        --no-strict)     NO_STRICT=1; shift ;;
        --sync)          RUN_SYNC=1; shift ;;
        --clean)         CLEAN_FLAG="--clean"; shift ;;
        -h|--help)       usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

if [ "$RUN_ASSESS" -eq 1 ] && [ "$RUN_LLM" -eq 1 ]; then
    [ "$MAX_SECTIONS_SET" -eq 0 ] && MAX_SECTIONS=1000
    [ "$MAX_SUBGRAPHS_SET" -eq 0 ] && MAX_SUBGRAPHS=200
fi

SPEC_INT=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli")

echo "================================================================================"
echo " Fireball Document Verification Pipeline [spec-integrator]"
echo "================================================================================"

# ---------------------------------------------------------------------------
# Accept the current specification state as the propagation baseline.
# Deliberately not part of the pipeline: doing it automatically would erase the
# very record that reveals an edit which never reached its dependants.
# ---------------------------------------------------------------------------
if [ "$RUN_SYNC" -eq 1 ]; then
    echo ""
    echo ">>> Recording consistency baseline..."
    if ! uv "${SPEC_INT[@]}" sync --config spec-integrator.yaml; then
        echo "✖ Baseline sync: FAILED"
        exit 1
    fi
    echo "  Commit spec-consistency.lock together with the spec changes."
    exit 0
fi

# ---------------------------------------------------------------------------
# Phase 1: Risk Assessment — establishes the verification obligations
# ---------------------------------------------------------------------------
if [ "$RUN_ASSESS" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 1/3] Risk Assessment (deciding what must be verified)..."
    ASSESS_ARGS=("${SPEC_INT[@]}" "assess" "--config" "spec-integrator.yaml"
                 "--backend" "$BACKEND" "--max-sections" "$MAX_SECTIONS"
                 "-o" "$RISK_REPORT" "-r" "reports/doc_risk_report.md")
    [ -n "$MODEL" ] && ASSESS_ARGS+=("--model" "$MODEL")
    [ "$NO_STRICT" -eq 1 ] && ASSESS_ARGS+=("--no-strict")

    if ! uv "${ASSESS_ARGS[@]}"; then
        echo "✖ Risk Assessment: FAILED (incomplete coverage leaves obligations unknown)"
        echo "  Raise --max-sections, or pass --no-strict to accept a partial assessment."
        exit 1
    fi
    echo "✔ Risk Assessment: obligations recorded in $RISK_REPORT"
else
    echo ""
    echo ">>> [Phase 1/3] Skipping Risk Assessment (--assess to run it)"
    if [ -f "$RISK_REPORT" ]; then
        echo "    Reusing the stored assessment. The gate will reject it if the docs have changed."
    else
        echo "    No stored assessment exists — the Obligation Gate will fail."
    fi
fi

# ---------------------------------------------------------------------------
# Phase 2: LLM Semantic Audit
# ---------------------------------------------------------------------------
if [ "$RUN_LLM" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 2/3] LLM as a Judge (semantic audit)..."
    JUDGE_ARGS=("${SPEC_INT[@]}" "judge" "--config" "spec-integrator.yaml"
                "--backend" "$BACKEND" "--max-subgraphs" "$MAX_SUBGRAPHS"
                "-o" "$JUDGE_REPORT")
    [ -n "$MODEL" ] && JUDGE_ARGS+=("--model" "$MODEL")

    # A FAIL verdict is data for the gate, not a reason to abort the pipeline.
    if uv "${JUDGE_ARGS[@]}"; then
        echo "✔ LLM as a Judge: no semantic failures"
    else
        echo "! LLM as a Judge reported findings — see $JUDGE_REPORT"
    fi
else
    echo ""
    echo ">>> [Phase 2/3] Skipping LLM as a Judge (--llm to run it)"
fi

# ---------------------------------------------------------------------------
# Phase 3: Quality Gates — the authoritative verdict
# ---------------------------------------------------------------------------
echo ""
echo ">>> [Phase 3/3] Quality Gates (Format / Traceability / Hierarchy / Formal / WIT / Evidence / Obligation / Consistency)..."
CHECK_ARGS=("${SPEC_INT[@]}" "check" "--config" "spec-integrator.yaml"
            "--report" "$REPORT_PATH" "--graph-json" "$GRAPH_JSON_PATH")
[ -n "$CLEAN_FLAG" ] && CHECK_ARGS+=("$CLEAN_FLAG")

uv "${CHECK_ARGS[@]}"
CHECK_EXIT=$?

echo ""
echo "================================================================================"
if [ "$CHECK_EXIT" -ne 0 ]; then
    echo " Verification Pipeline Summary: FAILED"
    echo " See $REPORT_PATH for the full list of violations."
    echo "================================================================================"
    exit 1
fi

echo " Verification Pipeline Summary: PASSED"
echo " Reports saved to: $REPORTS_DIR/"
echo "================================================================================"
exit 0
