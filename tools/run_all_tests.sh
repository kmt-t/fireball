#!/bin/bash
# Fireball Unified Document Verification Pipeline (Powered by spec-integrator)
#
# Phase ordering matters. `llm-assess` decides WHAT must be verified and
# `llm-judge` performs the semantic audit; `check` is the gate that consumes both verdicts
# and is the only authoritative result. Running `check` first — as this script
# used to — meant the risk assessment could demand verification that the gate
# had already declared unnecessary.
#
# The only knob this script exposes is the verification level. Backend, model,
# component, and other fine-tuning belong to `spec-integrator` itself — invoke
# it directly (see tools/spec-integrator/README.md) when you need that control.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LEVEL="1"
REPORTS_DIR="reports"
REPORT_PATH="reports/doc_report.md"

usage() {
    cat <<'EOF'
Fireball Document Quality & Verification Pipeline (spec-integrator)

Usage:
  ./tools/run_all_tests.sh [--level <1|2|3|sync>]

Levels:
  1 (default)  Local static gates only. Free, ~5-10s. No LLM calls.
               Phase 0 (lint/fmt) + Phase 3 (concept/bench/semantic) + Phase 4 (check).
               Reuses the stored risk assessment / judge report if present.
  2            Milestone audit. Costs a cloud LLM call, ~30s-1min.
               Level 1 + llm-assess + llm-judge (semantic audit + Design -> Test Spec
               -> Test Code consistency) + the pysim test suite.
  3            Release-gate audit. Costs cloud LLM calls, full coverage, slowest.
               Level 2 with exhaustive assessment/judge coverage across every
               keyword and component, plus a --clean scan.
  sync         Record the current spec state as the propagation baseline, then exit.
               Not a verification level - run this after a spec edit, before Level 1.

  -h, --help   Show this help

Backend, model, tier, and component selection are not exposed here - they are
the same for every level (spec-integrator.yaml's llm_judge.default_backend).
For anything more specific, call spec-integrator directly, e.g.:
  uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli llm-judge --component jit_compiler
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --level)      LEVEL="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

case "$LEVEL" in
    1|2|3|sync) ;;
    *) echo "✖ Invalid --level '$LEVEL'. Use 1, 2, 3, or sync."; exit 1 ;;
esac

mkdir -p "$REPORTS_DIR"

SPEC_INT=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli")

echo "================================================================================"
echo " Fireball Document Verification Pipeline [spec-integrator] - Level $LEVEL"
echo "================================================================================"

# ---------------------------------------------------------------------------
# Phase 0: Python Code Lint & Formatting Gate — runs at every level.
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
if [ "$LEVEL" = "sync" ]; then
    echo ""
    echo ">>> Recording consistency baseline..."
    if ! uv "${SPEC_INT[@]}" sync --config spec-integrator.yaml; then
        echo "✖ Baseline sync: FAILED"
        exit 1
    fi
    echo "  Commit the updated consistency baseline together with the spec changes."
    exit 0
fi

RUN_LLM=0
EXHAUSTIVE=0
MAX_KEYWORDS=15
MAX_SUBGRAPHS=10
MAX_DOCUMENTS=25
if [ "$LEVEL" = "2" ] || [ "$LEVEL" = "3" ]; then
    RUN_LLM=1
fi
if [ "$LEVEL" = "3" ]; then
    EXHAUSTIVE=1
    MAX_KEYWORDS=0
    MAX_SUBGRAPHS=0
    MAX_DOCUMENTS=0
fi

# ---------------------------------------------------------------------------
# Phase 1: Risk Assessment — establishes the verification obligations
# ---------------------------------------------------------------------------
if [ "$RUN_LLM" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 1/4] Risk Assessment (deciding what must be verified)..."
    ASSESS_ARGS=("${SPEC_INT[@]}" "llm-assess" "--config" "spec-integrator.yaml"
                 "--max-keywords" "$MAX_KEYWORDS")
    [ "$EXHAUSTIVE" -eq 1 ] && ASSESS_ARGS+=("--exhaustive")

    if ! uv "${ASSESS_ARGS[@]}"; then
        echo "✖ Risk Assessment: FAILED (incomplete coverage leaves obligations unknown)"
        echo "  Use --level 3 for exhaustive (unlimited-section) coverage."
        exit 1
    fi
    echo "✔ Risk Assessment: obligations recorded in the cache DB"
else
    echo ""
    echo ">>> [Phase 1/4] Skipping Risk Assessment (--level 2 or 3 to run it)"
    echo "    Reusing whatever assessment is already in the cache DB, if any. The gate"
    echo "    will reject it if the docs have changed, and fail if none exists."
fi

# ---------------------------------------------------------------------------
# Phase 2: LLM Semantic Audit — subgraph consistency, whole-document
# self-consistency, AND the Design -> Test Spec -> Test Code traceability
# chain always run together in one pass.
# ---------------------------------------------------------------------------
if [ "$RUN_LLM" -eq 1 ]; then
    echo ""
    echo ">>> [Phase 2/4] LLM as a Judge (subgraph + whole-document + Design -> Test Spec -> Test Code consistency)..."
    JUDGE_ARGS=("${SPEC_INT[@]}" "llm-judge" "--config" "spec-integrator.yaml"
                "--max-subgraphs" "$MAX_SUBGRAPHS" "--max-documents" "$MAX_DOCUMENTS")
    [ "$EXHAUSTIVE" -eq 1 ] && JUDGE_ARGS+=("--exhaustive")

    if ! uv "${JUDGE_ARGS[@]}"; then
        echo "! LLM as a Judge reported findings — see reports/doc_report.md § LLM Judge Verdicts / § Whole-Document LLM Judge Verdicts / § Test Chain Verdicts"
    else
        echo "✔ LLM as a Judge: no semantic failures"
    fi
else
    echo ""
    echo ">>> [Phase 2/4] Skipping LLM as a Judge (--level 2 or 3 to run it)"
fi

# ---------------------------------------------------------------------------
# Terminology Embedding & Similarity Indexing (Sakura AI) — Level 1+
# ---------------------------------------------------------------------------
echo ""
echo ">>> Terminology Embedding & Similarity Indexing (Sakura AI)..."
if ! uv "${SPEC_INT[@]}" term-index --config spec-integrator.yaml; then
    echo "! Terminology indexing reported a warning (non-fatal)"
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
# Python Simulator (pysim) Invariant & Integration Scenarios — free and local,
# but slow enough (~15-20s) to reserve for Level 2+.
# ---------------------------------------------------------------------------
if [ "$RUN_LLM" -eq 1 ]; then
    echo ""
    echo ">>> [pysim] Python Simulator Unit & Scenario Test Suite..."
    echo "  -> Running experiments/pysim/tests/run_all.py..."
    if ! uv run --system-certs --project tools/spec-integrator --with wasmtime python experiments/pysim/tests/run_all.py; then
        echo "✖ pysim Unit Tests: FAILED"
        CONCEPT_FAILED=1
    fi
    echo "  -> Running experiments/pysim/scenarios/run_all.py..."
    if ! uv run --system-certs --project tools/spec-integrator --with wasmtime python experiments/pysim/scenarios/run_all.py; then
        echo "✖ pysim Scenario Tests: FAILED"
        CONCEPT_FAILED=1
    fi
fi

# ---------------------------------------------------------------------------
# Phase 4: Quality Gates — the authoritative verdict
# ---------------------------------------------------------------------------
echo ""
echo ">>> [Phase 4/4] Quality Gates (Format / Traceability / Hierarchy / Formal / WIT / Evidence / Obligation / Consistency)..."
CHECK_ARGS=("${SPEC_INT[@]}" "check" "--config" "spec-integrator.yaml"
            "--report" "$REPORT_PATH")
[ "$EXHAUSTIVE" -eq 1 ] && CHECK_ARGS+=("--clean")

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
