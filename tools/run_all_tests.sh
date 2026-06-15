#!/bin/bash
# Fireball Unified Document Audit & Test Runner
# Usage: ./tools/run_all_tests.sh [OPTIONS]
#
# Options:
#   --quick          Limit LLM mode to module audits + Tier 1 hierarchy checks.
#   --llm            Run LLM semantic checks in addition to mechanical checks.
#   --backend B      LLM backend (gemini, sakura, openrouter, ollama)
#   --model M        LLM model name
#   -h, --help       Show this help
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RUN_LLM=0
QUICK_MODE=0
BACKEND=""
MODEL=""
LLM_MAX_TOKENS_VALUE="${LLM_MAX_TOKENS:-2048}"
FAILED=0

usage() {
    cat <<'EOF'
Fireball Unified Document Audit & Test Runner

Usage:
  ./tools/run_all_tests.sh [OPTIONS]

Options:
  --quick          Limit LLM mode to module audits + Tier 1 hierarchy checks.
  --llm            Run LLM semantic checks in addition to mechanical checks.
  --backend B      LLM backend (gemini, sakura, openrouter, ollama)
  --model M        LLM model name
  -h, --help       Show this help
EOF
}

print_section() {
    printf '\n>>> %s\n' "$1"
}

run_python_audit() {
    local label="$1"
    shift

    if python3 tools/scripts/run_audit.py "$@"; then
        printf "✔ %s: PASSED\n" "$label"
    else
        printf "✖ %s: FAILED\n" "$label"
        FAILED=1
    fi
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --quick)
            QUICK_MODE=1
            ;;
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
    if [ "$QUICK_MODE" -eq 1 ]; then
        echo " Note: --quick only affects runs with --llm."
    fi
fi
echo "================================================================================"

START_TIME=$(date +%s)
mkdir -p temp

# Phase 1: initialize keyword/glossary data.
print_section "[Phase 1/5] Synchronizing Keywords and Glossary into SQLite Database..."
run_python_audit "Database synchronization" --sync

# Phase 2: mechanical checks.
print_section "[Phase 2/5] Running Mechanical Formatting, Traceability, and Naming Checks..."
run_python_audit "Mechanical Checks"

if [ "$RUN_LLM" -eq 1 ]; then
    # Shared LLM arguments for semantic phases.
    SEMANTIC_ARGS=(--max-tokens "$LLM_MAX_TOKENS_VALUE")
    if [ -n "$BACKEND" ]; then
        SEMANTIC_ARGS+=(--backend "$BACKEND")
    fi
    if [ -n "$MODEL" ]; then
        SEMANTIC_ARGS+=(--model "$MODEL")
    fi

    # Phase 3: semantic module audits.
    print_section "[Phase 3/5] Running Semantic Module Audits (run_audit.py --all)..."
    run_python_audit "Semantic Module Audits" --all "${SEMANTIC_ARGS[@]}"

    # Phase 4: hierarchy audits.
    print_section "[Phase 4/5] Running Hierarchy Audits (run_audit.py --hierarchy)..."
    echo ">>> Tier 1 (Requirements → Core/Interface)..."
    run_python_audit "Tier 1 Hierarchy Audit" --hierarchy --tier 1 "${SEMANTIC_ARGS[@]}"

    if [ "$QUICK_MODE" -eq 1 ]; then
        echo "[Quick Mode] Skipping Tier 2 & 3 hierarchy audits"
        echo "[Quick Mode] Skipping Phase 5 consistency checklist audit"
    else
        echo ">>> Tier 2 (Core/Interface → Runtime/JIT)..."
        run_python_audit "Tier 2 Hierarchy Audit" --hierarchy --tier 2 "${SEMANTIC_ARGS[@]}"

        echo ">>> Tier 3 (Runtime/JIT → Platform/HAL)..."
        run_python_audit "Tier 3 Hierarchy Audit" --hierarchy --tier 3 "${SEMANTIC_ARGS[@]}"

        # Phase 5: consistency checklist audit.
        print_section "[Phase 5/5] Running Consistency Checklist Audit (run_audit.py --gentable & --llm)..."
        echo ">>> Generating consistency checklist..."
        run_python_audit "Consistency Checklist Generation" --gentable "${SEMANTIC_ARGS[@]}"

        echo ">>> Running consistency checklist audit..."
        run_python_audit "Consistency Checklist Audit" --llm "${SEMANTIC_ARGS[@]}"
    fi
else
    print_section "[Phase 3-5/5] Skipping Semantic LLM Audits (Use --llm to enable)"
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
