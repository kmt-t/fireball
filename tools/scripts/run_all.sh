#!/bin/bash
# Fireball All-in-One Analysis & Validation Runner
# Usage: ./tools/scripts/run_all.sh [--llm] [--model MODEL] [--quick]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Parse arguments
LLM_FLAG=""
MODEL_FLAG=""
QUICK_FLAG=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --llm) LLM_FLAG="--llm" ;;
        --model) MODEL_FLAG="--model $2"; shift ;;
        --quick) QUICK_FLAG="--quick" ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

cd "$REPO_ROOT"

echo "================================================================================"
echo " Fireball All-in-One Analysis & Validation Suite"
echo "================================================================================"
echo ""

# Track overall status
FAILED=0

# 1. Check Consistency
echo ">>> [1/3] Running Check Consistency..."
if ./tools/scripts/check_consistency/run.sh $LLM_FLAG $MODEL_FLAG; then
    echo "✔ Check Consistency PASSED"
else
    echo "✖ Check Consistency FAILED"
    FAILED=1
fi

echo ""

# 2. Traceability Audit
echo ">>> [2/3] Running Traceability Audit..."
if ./tools/scripts/traceability_audit/run.sh $LLM_FLAG $MODEL_FLAG; then
    echo "✔ Traceability Audit PASSED"
else
    echo "✖ Traceability Audit FAILED"
    FAILED=1
fi

echo ""

# 3. Document Test Suite
echo ">>> [3/3] Running Document Test Suite..."
if [ -n "$QUICK_FLAG" ]; then
    if ./tools/run_doc_tests.sh --quick $LLM_FLAG $MODEL_FLAG; then
        echo "✔ Document Test Suite PASSED (Quick Mode)"
    else
        echo "✖ Document Test Suite FAILED"
        FAILED=1
    fi
else
    if ./tools/run_doc_tests.sh $LLM_FLAG $MODEL_FLAG; then
        echo "✔ Document Test Suite PASSED"
    else
        echo "✖ Document Test Suite FAILED"
        FAILED=1
    fi
fi

echo ""
echo "================================================================================"

if [ "$FAILED" -eq 0 ]; then
    echo "🎉 All validations PASSED!"
    echo ""
    echo "Summary:"
    echo "  ✓ Check Consistency (FORMAT, Traceability, Architecture)"
    echo "  ✓ Traceability Audit (S2/S3 detection)"
    echo "  ✓ Document Test (Module + Tier 1-3)"
    echo ""
    exit 0
else
    echo "✖ Some validations FAILED"
    echo "Check logs above for details"
    exit 1
fi
