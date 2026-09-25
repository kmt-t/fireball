#!/bin/bash
# Fireball stored LLM finding query (Bash)
# Queries saved review decisions; does not call an LLM API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

MIN_CONFIDENCE="0.70"
CLASSIFICATIONS=()
ALL_OUTCOMES=""
RUN_TYPE=""
LIMIT="200"
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Stored LLM Finding Query

Usage:
  ./tools/llm-findings.sh [OPTIONS]

Options:
  --min-confidence <0..1> Minimum confidence (default: 0.70).
  --classification <id>  Filter by outcome; may be repeated.
  --all-outcomes          Include no_issue and insufficient_context outcomes.
  --run-type <type>       Filter by review command/mode.
  --limit <N>             Maximum rows; 0 prints all matches (default: 200).
  -c, --config <path>      Path to configuration file (default: spec-integrator.yaml).
  -h, --help               Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --min-confidence) MIN_CONFIDENCE="$2"; shift 2 ;;
        --classification) CLASSIFICATIONS+=("$2"); shift 2 ;;
        --all-outcomes) ALL_OUTCOMES="--all-outcomes"; shift ;;
        --run-type) RUN_TYPE="$2"; shift 2 ;;
        --limit) LIMIT="$2"; shift 2 ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "llm-findings"
          "--config" "$CONFIG" "--min-confidence" "$MIN_CONFIDENCE"
          "--limit" "$LIMIT")
for value in "${CLASSIFICATIONS[@]}"; do CMD_ARGS+=("--classification" "$value"); done
if [[ -n "$ALL_OUTCOMES" ]]; then CMD_ARGS+=("$ALL_OUTCOMES"); fi
if [[ -n "$RUN_TYPE" ]]; then CMD_ARGS+=("--run-type" "$RUN_TYPE"); fi

exec uv "${CMD_ARGS[@]}"
