#!/bin/bash
# Complete WIT → C++ Workflow
#
# Runs the complete WIT code generation workflow:
# 1. Generate C++ headers from WIT
# 2. Run quality checks
# 3. (Optional) Build test
#
# Usage: ./wit_all.sh [--no-build]

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Starting WIT code generation workflow..."
echo ""

# Step 1: Generate
"$SCRIPT_DIR/wit_gen.sh"
echo ""

# Step 2: Quality checks
"$SCRIPT_DIR/wit_check.sh"
echo ""

# Step 3: Build test (optional)
if [ "$1" != "--no-build" ]; then
    if [ -f "$SCRIPT_DIR/wit_build.sh" ]; then
        "$SCRIPT_DIR/wit_build.sh"
        echo ""
    else
        echo "ℹ️  Build script not found, skipping build test"
        echo ""
    fi
fi

echo "🎉 WIT workflow complete!"
