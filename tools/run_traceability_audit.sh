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
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Run the traceability auditor (via unified run_audit.py)
if [[ "$ARGS" == *"--llm"* ]]; then
    # Strip --llm and run semantic trace alignment
    CLEAN_ARGS=$(echo "$ARGS" | sed 's/--llm//g')
    python3 tools/scripts/run_audit.py --rule S-TRACE-ALIGN --all $CLEAN_ARGS
else
    # Run only mechanical trace rules
    python3 tools/scripts/run_audit.py --rule M-TRACE-UNDEFINED --rule M-TRACE-ORPHAN-SEC --rule M-TRACE-UNCOVERED $ARGS
fi
