#!/bin/bash
# Traceability Audit Runner
# Usage: ./tools/scripts/traceability_audit/run.sh [--llm] [--model MODEL] [--verbose] [--debug]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT"

# Pass all arguments to the Python script
python3 tools/scripts/traceability_audit/traceability_audit.py "$@"
