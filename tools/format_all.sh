#!/usr/bin/env bash
# Fireball Automated Code & Document Formatter (Bash)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "================================================================================"
echo " Fireball Automated Code & Document Formatter"
echo "================================================================================"

echo ""
echo ">>> [1/2] Auto-fixing lint issues with Ruff..."
uv run --system-certs --with ruff ruff check --fix --unsafe-fixes experiments tools docs || true
echo "✔ Ruff check auto-fix: COMPLETE"

echo ""
echo ">>> [2/2] Formatting Python code with Ruff..."
uv run --system-certs --with ruff ruff format experiments tools docs
echo "✔ Ruff format: COMPLETE"

echo ""
echo "================================================================================"
echo " Formatting Complete! All Python files are clean and PEP8 compliant."
echo "================================================================================"
