#!/bin/bash
# Fireball Traceability Audit Runner

set -e

# Parse arguments
ARGS=""
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --llm) ARGS="$ARGS --llm" ;;
        --model) ARGS="$ARGS --model $2"; shift ;;
        --verbose) ARGS="$ARGS --verbose" ;;
        --debug) ARGS="$ARGS --debug" ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# Resolve script directory and change to repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
cd "$REPO_ROOT"

# Run the traceability auditor
python3 tools/audit_traceability/audit_traceability.py $ARGS
