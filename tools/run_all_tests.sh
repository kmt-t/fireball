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
RUN_TESTCHAIN=0
COMPONENT=""
BACKEND="sakura"
MODEL=""
MAX_SUBGRAPHS=10
MAX_SECTIONS=15
MIN_REFERENCES=1
MIN_LENGTH=50
TARGET_TIER=""
EXHAUSTIVE=0
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
  --exhaustive       Run exhaustive assessment & semantic audit (checks all sections/subgraphs).
  --backend B        LLM backend (sakura, ollama, mock - default: sakura)
  --model M          LLM model name
  --max-subgraphs N  Subgraphs to evaluate with the LLM judge (default: 10, 0 for unlimited)
  --max-sections N   Sections to risk-assess (default: 15, 0 for unlimited)
  --min-references N Minimum referencing sections required to audit a subgraph (default: 1)
  --min-length N     Minimum body character length to evaluate (default: 50)
  --tier T           Comma-separated tiers to assess (e.g. '0,1,2')
  --no-strict        Accept a partial risk assessment instead of failing.
  --sync             Record the current spec state as the propagation baseline, then exit.
  --clean            Run a clean audit without the cache DB.
  -h, --help         Show this help

Phases:
  1. assess  - decides what must be verified   (skippable; the stored report is reused)
  2. judge   - semantic audit                  (skippable)
  3. concept - runs docs/**/concepts/*_concept.py (always runs)
  4. check   - quality gates, authoritative    (always runs)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm)              RUN_LLM=1; shift ;;
        --assess)           RUN_ASSESS=1; shift ;;
        --test-chain|--testchain) RUN_TESTCHAIN=1; shift ;;
        --component)        COMPONENT="$2"; shift 2 ;;
        --full)             RUN_ASSESS=1; RUN_LLM=1; MAX_SECTIONS=0; MAX_SUBGRAPHS=0; shift ;;
        --exhaustive)       RUN_ASSESS=1; RUN_LLM=1; EXHAUSTIVE=1; MAX_SECTIONS=0; MAX_SUBGRAPHS=0; shift ;;
        --backend)          BACKEND="$2"; shift 2 ;;
        --model)            MODEL="$2"; shift 2 ;;
        --max-subgraphs)    MAX_SUBGRAPHS="$2"; MAX_SUBGRAPHS_SET=1; shift 2 ;;
        --max-sections)     MAX_SECTIONS="$2"; MAX_SECTIONS_SET=1; shift 2 ;;
        --min-references)   MIN_REFERENCES="$2"; shift 2 ;;
        --min-length)       MIN_LENGTH="$2"; shift 2 ;;
        --tier)             TARGET_TIER="$2"; shift 2 ;;
        --no-strict)        NO_STRICT=1; shift ;;
        --sync)             RUN_SYNC=1; shift ;;
        --clean)            CLEAN_FLAG="--clean"; shift ;;
        -h|--help)          usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

SPEC_INT=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli")

echo "================================================================================"
echo " Fireball Document Verification Pipeline [spec-integrator]"
echo "================================================================================"

# ---------------------------------------------------------------------------
# Phase 0: Python Code Lint & Formatting Gate
# ---------------------------------------------------------------------------
echo ""
echo ">>> [Phase 0] Python Code Linter & Formatter Verification (Ruff)..."
if ! uv run --system-certs --with ruff ruff check experiments tools docs; then
    echo "✖ Python Lint Check: FAILED — run './tools/format_all.sh' to auto-fix."
    exit 1
fi
if ! uv run --system-certs --with ruff ruff format --check experiments tools docs; then
    echo "✖ Python Format Check: FAILED — run './tools/format_all.sh' to auto-format."
    exit 1
fi
echo "✔ Python Linter & Formatter: All checks passed (0 errors)"

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
    echo ">>> [Phase 1/4] Risk Assessment (deciding what must be verified)..."
    ASSESS_ARGS=("${SPEC_INT[@]}" "assess" "--config" "spec-integrator.yaml"
                 "--backend" "$BACKEND" "--max-sections" "$MAX_SECTIONS"
                 "--min-length" "$MIN_LENGTH"
                 "-o" "$RISK_REPORT" "-r" "reports/doc_risk_report.md")
    [ -n "$MODEL" ] && ASSESS_ARGS+=("--model" "$MODEL")
    [ -n "$TARGET_TIER" ] && ASSESS_ARGS+=("--tier" "$TARGET_TIER")
    [ "$EXHAUSTIVE" -eq 1 ] && ASSESS_ARGS+=("--exhaustive")
    [ "$NO_STRICT" -eq 1 ] && ASSESS_ARGS+=("--no-strict")

    if ! uv "${ASSESS_ARGS[@]}"; then
        echo "✖ Risk Assessment: FAILED (incomplete coverage leaves obligations unknown)"
        echo "  Raise --max-sections, or pass --no-strict to accept a partial assessment."
        exit 1
    fi
    echo "✔ Risk Assessment: obligations recorded in $RISK_REPORT"
else
    echo ""
    echo ">>> [Phase 1/4] Skipping Risk Assessment (--assess to run it)"
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
    echo ">>> [Phase 2/4] LLM as a Judge (semantic audit)..."
    JUDGE_ARGS=("${SPEC_INT[@]}" "judge" "--config" "spec-integrator.yaml"
                "--backend" "$BACKEND" "--max-subgraphs" "$MAX_SUBGRAPHS"
                "--min-references" "$MIN_REFERENCES"
                "-o" "$JUDGE_REPORT" "-r" "reports/doc_judge_report.md")
    [ -n "$MODEL" ] && JUDGE_ARGS+=("--model" "$MODEL")
    [ "$EXHAUSTIVE" -eq 1 ] && JUDGE_ARGS+=("--exhaustive")

    if ! uv "${JUDGE_ARGS[@]}"; then
        echo "! LLM as a Judge reported findings — see reports/doc_judge_report.md"
    else
        echo "✔ LLM as a Judge: no semantic failures"
    fi
else
    echo ""
    echo ">>> [Phase 2/4] Skipping LLM as a Judge (--llm to run it)"
fi

# ---------------------------------------------------------------------------
# Design -> Test Spec -> Test Code 3-Tier Traceability & Consistency Judge
# ---------------------------------------------------------------------------
if [ "$RUN_TESTCHAIN" -eq 1 ]; then
    echo ""
    echo ">>> [3-Tier Consistency] LLM Design-to-Test Consistency Judge..."
    CHAIN_ARGS=("${SPEC_INT[@]}" "judge-test-chain" "--config" "spec-integrator.yaml"
                "--backend" "$BACKEND")
    [ -n "$COMPONENT" ] && CHAIN_ARGS+=("--component" "$COMPONENT")
    [ -n "$MODEL" ] && CHAIN_ARGS+=("--model" "$MODEL")
    [ "$EXHAUSTIVE" -eq 1 ] || [ "$RUN_ASSESS" -eq 1 ] && CHAIN_ARGS+=("--all")

    if ! uv "${CHAIN_ARGS[@]}"; then
        echo "! Test Chain Judge reported findings — see reports/test_chain_judge_report.md"
    else
        echo "✔ Test Chain Judge: all audited components are consistent across Spec -> TestSpec -> TestCode"
    fi
fi

# ---------------------------------------------------------------------------
# Phase 3: Concept Code Verification — the reference implementations under
# docs/**/concepts/*_concept.py are not test_*.py, so pytest silently collects
# zero tests from them and no other phase ever imports or executes them. This
# is the only thing that actually runs each one and checks it still works.
# ---------------------------------------------------------------------------
echo ""
echo ">>> [Phase 3/4] Concept Code Verification (running docs/**/concepts/*_concept.py)..."
CONCEPT_FAILED=0
while IFS= read -r -d '' f; do
    if uv run --system-certs --project tools/spec-integrator python "$f"; then
        echo "✔ $f"
    else
        echo "✖ $f FAILED"
        CONCEPT_FAILED=1
    fi
done < <(find docs -name "*_concept.py" -print0)
if [ "$CONCEPT_FAILED" -eq 1 ]; then
    echo "✖ Concept Code Verification: FAILED"
else
    echo "✔ Concept Code Verification: passed"
fi

# Benchmarks: empirical backing for keywords whose requirement_list.md verification
# method is "ベンチマーク" (Benchmark), tagged {VERIFY_BENCHMARK} and checked for
# existence by the Evidence gate below. Running them here (not just checking they
# exist) catches a benchmark that has silently started failing its own assertions.
BENCH_COUNT=0
while IFS= read -r -d '' f; do
    BENCH_COUNT=$((BENCH_COUNT + 1))
    if uv run --system-certs --project tools/spec-integrator python "$f"; then
        echo "✔ $f"
    else
        echo "✖ $f FAILED"
        CONCEPT_FAILED=1
    fi
done < <(find docs -name "*_bench.py" -print0)
if [ "$BENCH_COUNT" -gt 0 ]; then
    echo "✔ Benchmarks: $BENCH_COUNT file(s) ran"
fi

# Dynamic semantic check: actually executes the JIT stencil catalog's machine code
# on a real ARMv8-M Thumb emulator (unicorn) and checks the resulting register state
# against the WASM-specified result, instead of only comparing bytes to a second
# hand-written copy. Needs the `unicorn` package, which is not a spec-integrator
# dependency, so it is invoked separately via `--with`.
for SEM_VERIFIER in \
    "docs/components/tier3_jit/concepts/thumb2_stencil_semantic_verifier.py" \
    "docs/components/tier3_jit/concepts/jit_trace_execution_verifier.py"
do
    if [ -f "$SEM_VERIFIER" ]; then
        if uv run --system-certs --project tools/spec-integrator --with unicorn python "$SEM_VERIFIER"; then
            echo "✔ $SEM_VERIFIER"
        else
            echo "✖ $SEM_VERIFIER FAILED"
            CONCEPT_FAILED=1
        fi
    fi
done

# ---------------------------------------------------------------------------
# Phase 4: Quality Gates — the authoritative verdict
# ---------------------------------------------------------------------------
echo ""
echo ">>> [Phase 4/4] Quality Gates (Format / Traceability / Hierarchy / Formal / WIT / Evidence / Obligation / Consistency)..."
CHECK_ARGS=("${SPEC_INT[@]}" "check" "--config" "spec-integrator.yaml"
            "--report" "$REPORT_PATH" "--graph-json" "$GRAPH_JSON_PATH")
[ -n "$CLEAN_FLAG" ] && CHECK_ARGS+=("$CLEAN_FLAG")

uv "${CHECK_ARGS[@]}"
CHECK_EXIT=$?

echo ""
echo "================================================================================"
if [ "$CONCEPT_FAILED" -eq 1 ] || [ "$CHECK_EXIT" -ne 0 ]; then
    echo " Verification Pipeline Summary: FAILED"
    [ "$CONCEPT_FAILED" -eq 1 ] && echo " Concept code verification failed — see output above."
    [ "$CHECK_EXIT" -ne 0 ] && echo " See $REPORT_PATH for the full list of violations."
    echo "================================================================================"
    exit 1
fi

echo " Verification Pipeline Summary: PASSED"
echo " Reports saved to: $REPORTS_DIR/"
echo "================================================================================"
exit 0
