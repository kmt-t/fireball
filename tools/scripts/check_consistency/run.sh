#!/bin/bash
# Check Consistency Runner
# Usage: ./tools/scripts/check_consistency/run.sh [--llm] [--gentable] [--model MODEL] [--verbose] [--debug]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

# Pass all arguments to the Python script
python3 tools/scripts/check_consistency/check_consistency.py "$@"
