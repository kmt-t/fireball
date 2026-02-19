#!/bin/bash
# WIT Generated Code Quality Check Script
#
# Runs automated quality checks on generated C++ headers.
# Works in both devcontainer and external environments.
# Usage: bash wit_check.sh

set -e

echo "[*] Running quality checks on generated code..."
echo ""

# Targets to check
TARGETS=("$@")

# Use defaults if no targets provided
if [ ${#TARGETS[@]} -eq 0 ]; then
    TARGETS=("inc/gen")
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
if [ ! -t 0 ]; then
    # stdin is a pipe
    cat - | $PYTHON_CMD .agent/skills/project_code_generate/scripts/check_violations.py "${TARGETS[@]}"
else
    $PYTHON_CMD .agent/skills/project_code_generate/scripts/check_violations.py "${TARGETS[@]}"
fi
VIOLATIONS_RESULT=$?

echo ""

# Check 2: Naming conventions
echo "  [2/2] Checking naming conventions..."
if [ ! -t 0 ]; then
    # stdin is a pipe
    cat - | $PYTHON_CMD .agent/skills/project_code_generate/scripts/check_naming.py "${TARGETS[@]}"
else
    $PYTHON_CMD .agent/skills/project_code_generate/scripts/check_naming.py "${TARGETS[@]}"
fi
NAMING_RESULT=$?

echo ""

# Summary
if [ $VIOLATIONS_RESULT -eq 0 ] && [ $NAMING_RESULT -eq 0 ]; then
    echo "[OK] All quality checks passed!"
    exit 0
else
    echo "[ERROR] Quality checks failed"
    exit 1
fi
