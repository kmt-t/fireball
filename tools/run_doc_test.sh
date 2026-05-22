#!/bin/bash
# Fireball LLM Documentation Audit Runner (Section-Matrix Enhanced)
# Usage: ./tools/run_doc_test.sh [--backend SAKURA|gemini|openrouter|ollama] [--model MODEL_NAME] [--quick]

set -e

# Defaults
BACKEND=""
MODEL=""
MAX_TOKENS=2048
QUICK_MODE=0

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --backend) BACKEND="$2"; shift ;;
        --model) MODEL="$2"; shift ;;
        --max-tokens) MAX_TOKENS="$2"; shift ;;
        --quick) QUICK_MODE=1 ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# Build arguments for Python script
ARGS=""
if [ -n "$BACKEND" ]; then
    ARGS="$ARGS --backend $BACKEND"
fi
if [ -n "$MODEL" ]; then
    ARGS="$ARGS --model $MODEL"
fi
ARGS="$ARGS --max-tokens $MAX_TOKENS"

# Report output directory
REPORT_DIR="docs/tools/audit_reports"
mkdir -p "$REPORT_DIR"

echo "================================================================================"
echo " Fireball LLM Documentation Audit (Section-Matrix Enhanced)"
echo " Backend: ${BACKEND:-auto}  Max Tokens: $MAX_TOKENS"
if [ "$QUICK_MODE" -eq 1 ]; then
    echo " Mode: QUICK (Tier 1 only)"
else
    echo " Mode: FULL (Module + Tier 1-3)"
fi
echo "================================================================================"

# Resolve script directory and change to repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Track overall status
FAILED=0
START_TIME=$(date +%s)

# 1. Module Audit (All Files)
echo -e "\n>>> [1/4] Running Module Audit (All component files)..."
REPORT_FILE="$REPORT_DIR/audit_module_$(date +%Y%m%d_%H%M%S).log"
if python3 tools/test_doc/test_doc_llm.py --all $ARGS 2>&1 | tee "$REPORT_FILE"; then
    echo "✔ Module Audit PASSED"
else
    echo "✖ Module Audit FAILED"
    FAILED=1
fi

# 2. Hierarchy Audit - Tier 1
echo -e "\n>>> [2/4] Running Hierarchy Audit - Tier 1 (Requirements → Core/Interface)..."
echo "    Note: Section-by-section analysis (may take longer due to multiple LLM calls)"
REPORT_FILE="$REPORT_DIR/audit_tier1_$(date +%Y%m%d_%H%M%S).log"
if python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 1 $ARGS 2>&1 | tee "$REPORT_FILE"; then
    echo "✔ Tier 1 Hierarchy Audit PASSED"
else
    echo "✖ Tier 1 Hierarchy Audit FAILED"
    FAILED=1
fi

# Skip Tier 2-3 in quick mode
if [ "$QUICK_MODE" -eq 1 ]; then
    echo -e "\n[Quick Mode] Skipping Tier 2 & 3 audits"
else
    # 3. Hierarchy Audit - Tier 2
    echo -e "\n>>> [3/4] Running Hierarchy Audit - Tier 2 (Core/Interface → Runtime/JIT)..."
    REPORT_FILE="$REPORT_DIR/audit_tier2_$(date +%Y%m%d_%H%M%S).log"
    if python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 2 $ARGS 2>&1 | tee "$REPORT_FILE"; then
        echo "✔ Tier 2 Hierarchy Audit PASSED"
    else
        echo "✖ Tier 2 Hierarchy Audit FAILED"
        FAILED=1
    fi

    # 4. Hierarchy Audit - Tier 3
    echo -e "\n>>> [4/4] Running Hierarchy Audit - Tier 3 (Runtime/JIT → Platform/HAL)..."
    REPORT_FILE="$REPORT_DIR/audit_tier3_$(date +%Y%m%d_%H%M%S).log"
    if python3 tools/test_doc/test_doc_llm.py --hierarchy --tier 3 $ARGS 2>&1 | tee "$REPORT_FILE"; then
        echo "✔ Tier 3 Hierarchy Audit PASSED"
    else
        echo "✖ Tier 3 Hierarchy Audit FAILED"
        FAILED=1
    fi
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "================================================================================"
echo " Audit Summary"
echo "================================================================================"
echo "Reports saved to: $REPORT_DIR"
echo "Elapsed time: ${ELAPSED}s"

if [ "$FAILED" -eq 0 ]; then
    echo ""
    echo "🎉 All LLM Audits Completed Successfully!"
    echo "   ✓ Module audit (policies, traceability, quality)"
    echo "   ✓ Tier 1 hierarchy (Requirements → Core/Interface) [section-by-section]"
    if [ "$QUICK_MODE" -eq 0 ]; then
        echo "   ✓ Tier 2 hierarchy (Core/Interface → Runtime/JIT) [section-by-section]"
        echo "   ✓ Tier 3 hierarchy (Runtime/JIT → Platform/HAL) [section-by-section]"
    fi
    exit 0
else
    echo ""
    echo "✖ Some Audits Failed"
    echo "   Check logs in: $REPORT_DIR"
    exit 1
fi
echo "================================================================================"
