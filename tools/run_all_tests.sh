#!/bin/bash
# Fireball Unified Document Audit & Test Runner
# Usage: ./tools/run_all_tests.sh [OPTIONS]
#
# Options:
#   --quick          Mechanical checks only (default, unless --llm is set). If --llm is set, runs quick LLM mode (Tier 1 only).
#   --llm            Run LLM semantic checks (sequentially to prevent rate limits) in addition to mechanical checks.
#   --backend B      LLM backend (gemini, sakura, openrouter, ollama)
#   --model M        LLM model name
#

set -e

# Resolve script directory and change to repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Defaults
RUN_LLM=0
QUICK_MODE=0
BACKEND=""
MODEL=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --quick) QUICK_MODE=1 ;;
        --llm) RUN_LLM=1 ;;
        --backend) BACKEND="$2"; shift ;;
        --model) MODEL="$2"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

echo "================================================================================"
echo " Fireball Unified Document Verification Pipeline"
echo "================================================================================"
if [ "$RUN_LLM" -eq 1 ]; then
    echo " Mode: Mechanical + LLM Semantic Checks"
    echo " Backend: ${BACKEND:-auto}"
    if [ "$QUICK_MODE" -eq 1 ]; then
        echo " LLM Mode: Quick (Module + Tier 1 only)"
    else
        echo " LLM Mode: Full (Module + Tier 1-3)"
    fi
else
    echo " Mode: Mechanical Checks Only (use --llm to enable LLM audits)"
fi
echo "================================================================================"

FAILED=0
START_TIME=$(date +%s)

# Ensure database directory exists
mkdir -p temp

# Helper: Print section divider
print_section() {
    echo -e "\n>>> $1"
}

# ------------------------------------------------------------------------------
# Phase 1: Initialize DB with Keywords and Glossary
# ------------------------------------------------------------------------------
print_section "[Phase 1/5] Synchronizing Keywords and Glossary into SQLite Database..."
python3 tools/scripts/run_audit.py --sync

# ------------------------------------------------------------------------------
# Phase 2: Mechanical Consistency and Format Checks
# ------------------------------------------------------------------------------
print_section "[Phase 2/5] Running Mechanical Formatting, Traceability, and Naming Checks (run_audit.py)..."
AUDIT_ARGS=""
if [ -n "$MODEL" ]; then
    AUDIT_ARGS="$AUDIT_ARGS --model $MODEL"
fi
if [ -n "$BACKEND" ]; then
    AUDIT_ARGS="$AUDIT_ARGS --backend $BACKEND"
fi

if python3 tools/scripts/run_audit.py $AUDIT_ARGS; then
    echo "✔ Mechanical Checks: PASSED"
else
    echo "✖ Mechanical Checks: FAILED"
    FAILED=1
fi

# ------------------------------------------------------------------------------
# Phase 3: Semantic LLM Audits (Optional)
# ------------------------------------------------------------------------------
if [ "$RUN_LLM" -eq 1 ]; then
    print_section "[Phase 3/5] Running Semantic Module Audits (run_audit.py --all)..."
    SEMANTIC_ARGS="--all"
    if [ -n "$BACKEND" ]; then
        SEMANTIC_ARGS="$SEMANTIC_ARGS --backend $BACKEND"
    fi
    if [ -n "$MODEL" ]; then
        SEMANTIC_ARGS="$SEMANTIC_ARGS --model $MODEL"
    fi
    SEMANTIC_ARGS="$SEMANTIC_ARGS --max-tokens ${LLM_MAX_TOKENS:-2048}"

if python3 tools/scripts/run_audit.py $SEMANTIC_ARGS; then
        echo "✔ Semantic Module Audits: PASSED"
    else
        echo "✖ Semantic Module Audits: FAILED"
        FAILED=1
    fi

    print_section "[Phase 4/5] Running Hierarchy Audits (run_audit.py --hierarchy)..."
    HIER_ARGS="--hierarchy"
    if [ -n "$BACKEND" ]; then
        HIER_ARGS="$HIER_ARGS --backend $BACKEND"
    fi
    if [ -n "$MODEL" ]; then
        HIER_ARGS="$HIER_ARGS --model $MODEL"
    fi
    HIER_ARGS="$HIER_ARGS --max-tokens ${LLM_MAX_TOKENS:-2048}"

    # Tier 1
    echo ">>> Tier 1 (Requirements → Core/Interface)..."
if python3 tools/scripts/run_audit.py $HIER_ARGS --tier 1; then
        echo "✔ Tier 1 Hierarchy Audit: PASSED"
    else
        echo "✖ Tier 1 Hierarchy Audit: FAILED"
        FAILED=1
    fi

    if [ "$QUICK_MODE" -eq 1 ]; then
        echo "[Quick Mode] Skipping Tier 2 & 3 hierarchy audits"
    else
        # Tier 2
        echo ">>> Tier 2 (Core/Interface → Runtime/JIT)..."
if python3 tools/scripts/run_audit.py $HIER_ARGS --tier 2; then
            echo "✔ Tier 2 Hierarchy Audit: PASSED"
        else
            echo "✖ Tier 2 Hierarchy Audit: FAILED"
            FAILED=1
        fi

        # Tier 3
        echo ">>> Tier 3 (Runtime/JIT → Platform/HAL)..."
if python3 tools/scripts/run_audit.py $HIER_ARGS --tier 3; then
            echo "✔ Tier 3 Hierarchy Audit: PASSED"
        else
            echo "✖ Tier 3 Hierarchy Audit: FAILED"
            FAILED=1
        fi

        # Phase 5: Consistency Checklist Audit
        if [ "$QUICK_MODE" -eq 1 ]; then
            echo "[Quick Mode] Skipping Phase 5 consistency checklist audit"
        else
            print_section "[Phase 5/5] Running Consistency Checklist Audit (run_audit.py --gentable & --llm)..."
            echo ">>> Generating consistency checklist..."
            GENTABLE_ARGS=""
            if [ -n "$BACKEND" ]; then
                GENTABLE_ARGS="$GENTABLE_ARGS --backend $BACKEND"
            fi
            if [ -n "$MODEL" ]; then
                GENTABLE_ARGS="$GENTABLE_ARGS --model $MODEL"
            fi
            GENTABLE_ARGS="$GENTABLE_ARGS --max-tokens ${LLM_MAX_TOKENS:-2048}"
            python3 tools/scripts/run_audit.py --gentable $GENTABLE_ARGS

            echo ">>> Running consistency checklist audit..."
            LLM_ARGS=""
            if [ -n "$BACKEND" ]; then
                LLM_ARGS="$LLM_ARGS --backend $BACKEND"
            fi
            if [ -n "$MODEL" ]; then
                LLM_ARGS="$LLM_ARGS --model $MODEL"
            fi
            LLM_ARGS="$LLM_ARGS --max-tokens ${LLM_MAX_TOKENS:-2048}"
if python3 tools/scripts/run_audit.py --llm $LLM_ARGS; then
                echo "✔ Consistency Checklist Audit: PASSED"
            else
                echo "✖ Consistency Checklist Audit: FAILED"
                FAILED=1
            fi
        fi
    fi
else
    print_section "[Phase 3-5/5] Skipping Semantic LLM Audits (Use --llm to enable)"
fi

END_TIME=$(date +%s)
# Note: we need to output the elapsed time
ELAPSED=$((END_TIME - START_TIME))

echo -e "\n================================================================================"
echo " Verification Pipeline Summary"
echo "================================================================================"
echo " Elapsed Time: ${ELAPSED}s"
if [ "$FAILED" -eq 0 ]; then
    echo " Result: SUCCESS (All enabled checks passed)"
    exit 0
else
    echo " Result: FAILURE (Some checks failed)"
    exit 1
fi
echo "================================================================================"
