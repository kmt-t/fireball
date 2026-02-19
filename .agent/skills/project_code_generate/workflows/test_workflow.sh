#!/bin/bash
# Quick test script for workflow debugging
# Usage: ./test_workflow.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== WIT Workflow Test ==="
echo ""

echo "[1/3] Testing generate-code.sh..."
bash "$SCRIPT_DIR/generate-code.sh"
echo ""

echo "[2/3] Testing check-quality.sh..."
bash "$SCRIPT_DIR/check-quality.sh"
echo ""

echo "[3/3] Complete workflow test..."
bash "$SCRIPT_DIR/run-workflow.sh"
echo ""

echo "=== All tests passed ==="
