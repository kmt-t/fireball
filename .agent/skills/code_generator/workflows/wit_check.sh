#!/bin/bash
# WIT Generated Code Quality Check Script
#
# Runs automated quality checks on generated C++ headers.
# Works in both devcontainer and external environments.
# Usage: bash wit_check.sh

set -e

echo "[*] Running quality checks on generated code..."
echo ""

# Use relative path for inc/gen
GEN_DIR="inc/gen"

# Check if generated directory exists
if [ ! -d "$GEN_DIR" ]; then
    echo "[ERROR] Generated directory not found: $GEN_DIR"
    echo "Run ./wit_gen.sh first"
    exit 1
fi

# Detect Python command (python or python3)
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    echo "[ERROR] Python not found"
    exit 1
fi

# Check 1: Coding violations
echo "  [1/2] Checking prohibited patterns..."
$PYTHON_CMD .agent/skills/code_generator/scripts/check_violations.py "$GEN_DIR"
VIOLATIONS_RESULT=$?

echo ""

# Check 2: Naming conventions
echo "  [2/2] Checking naming conventions..."
$PYTHON_CMD .agent/skills/code_generator/scripts/check_naming.py "$GEN_DIR"
NAMING_RESULT=$?

echo ""

# Summary
if [ $VIOLATIONS_RESULT -eq 0 ] && [ $NAMING_RESULT -eq 0 ]; then
    echo "[OK] All quality checks passed!"
    exit 0
else
    echo "[ERROR] Quality checks failed"
    if [ $VIOLATIONS_RESULT -ne 0 ]; then
        echo "   - Coding violations detected"
    fi
    if [ $NAMING_RESULT -ne 0 ]; then
        echo "   - Naming convention violations detected"
    fi
    exit 1
fi
