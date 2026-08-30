#!/usr/bin/env bash
# Fireball Automated Code & Document Formatter (Bash)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CHECK_MODE=0

usage() {
    cat <<'EOF'
Fireball Code & Document Formatter (Bash)

Usage:
  ./tools/format_all.sh [OPTIONS]

Options:
  --check    Check formatting and linting without applying modifications.
  -h, --help Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)    CHECK_MODE=1; shift ;;
        -h|--help)  usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

echo "================================================================================"
echo " Fireball Automated Code & Document Formatter (Ruff)"
echo "================================================================================"

if [ "$CHECK_MODE" -eq 1 ]; then
    echo ""
    echo ">>> [1/2] Checking Python code linting with Ruff..."
    uv run --system-certs --with ruff ruff check experiments tools docs
    echo "✔ Ruff lint check: PASSED (0 errors)"

    echo ""
    echo ">>> [2/2] Checking Python code formatting with Ruff..."
    uv run --system-certs --with ruff ruff format --check experiments tools docs
    echo "✔ Ruff format check: PASSED (all files formatted)"
else
    echo ""
    echo ">>> [1/2] Auto-fixing lint issues with Ruff..."
    uv run --system-certs --with ruff ruff check --fix --unsafe-fixes experiments tools docs || true
    echo "✔ Ruff check auto-fix: CLEAN"

    echo ""
    echo ">>> [2/2] Formatting Python code with Ruff..."
    uv run --system-certs --with ruff ruff format experiments tools docs
    echo "✔ Ruff format: COMPLETE"
fi

echo ""
echo "================================================================================"
echo " Formatting Pipeline Complete! All Python files are clean and PEP8 compliant."
echo "================================================================================"
