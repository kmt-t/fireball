#!/bin/bash
# Complete WIT → C++ Workflow
#
# Runs the complete WIT code generation workflow:
# 1. Generate C++ headers from WIT
# 2. Run quality checks
# 3. (Optional) Build test
#
# Usage: ./run-workflow.sh [--no-build]

set -e  # Exit on erro

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Starting WIT code generation workflow..."
echo ""

# Step 1: Generate
"$SCRIPT_DIR/generate-code.sh"
echo ""

# Step 2: Quality checks
"$SCRIPT_DIR/check-quality.sh"
echo ""

# Step 3: Build test (optional)
if [ "$1" != "--no-build" ]; then
    if [ -f "$SCRIPT_DIR/build-project.sh" ]; then
        "$SCRIPT_DIR/build-project.sh"
        echo ""
    else
        echo "ℹ️  Build script not found, skipping build test"
        echo ""
    fi
fi

echo "🎉 WIT workflow complete!"
