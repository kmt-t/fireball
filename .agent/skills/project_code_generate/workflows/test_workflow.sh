#!/bin/bash
# Quick test script for workflow debugging
# Usage: ./test_workflow.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== WIT Workflow Test ==="
echo ""

echo "[1/3] Testing wit_gen.sh..."
bash "$SCRIPT_DIR/wit_gen.sh"
echo ""

echo "[2/3] Testing wit_check.sh..."
bash "$SCRIPT_DIR/wit_check.sh"
echo ""

echo "[3/3] Complete workflow test..."
bash "$SCRIPT_DIR/wit_all.sh"
echo ""

echo "=== All tests passed ==="
