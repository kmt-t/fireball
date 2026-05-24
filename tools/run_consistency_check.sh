#!/bin/bash
# Fireball Check Consistency Runner

set -e

# Parse arguments
ARGS=""
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --llm) ARGS="$ARGS --llm" ;;
        --gentable) ARGS="$ARGS --gentable" ;;
        --model) ARGS="$ARGS --model $2"; shift ;;
        --verbose) ARGS="$ARGS --verbose" ;;
        --debug) ARGS="$ARGS --debug" ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# Resolve script directory and change to repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Run the consistency checker (via unified run_audit.py)
python3 tools/run_audit.py $ARGS
