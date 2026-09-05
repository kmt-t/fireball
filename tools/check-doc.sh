#!/bin/bash
# Fireball Document Quality Gate & Verification Runner (Bash)
# Runs static verifications and quality gates for documentation without calling LLMs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

REPORT="reports/doc_report.md"
CLEAN=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Document Quality Check

Usage:
  ./tools/check-doc.sh [OPTIONS] [FILES...]

Options:
  -r, --report <path> Path to generated markdown report (default: reports/doc_report.md).
  --clean             Run clean verification without using cached assessment/graph state.
  -c, --config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, --help          Show this help message.
EOF
    exit 0
}

FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--report) REPORT="$2"; shift 2 ;;
        --clean)     CLEAN="--clean"; shift ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        -h|--help)   usage ;;
        *) FILES+=("$1"); shift ;;
    esac
done

mkdir -p "$(dirname "$REPORT")"

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "check-doc"
          "--config" "$CONFIG" "--report" "$REPORT")
if [[ -n "$CLEAN" ]]; then
    CMD_ARGS+=("$CLEAN")
fi

if [[ ${#FILES[@]} -gt 0 ]]; then
    CMD_ARGS+=("${FILES[@]}")
else
    while IFS= read -r f; do
        CMD_ARGS+=("$f")
    done < <(find docs -type f -name "*.md" | sort)
fi

exec uv "${CMD_ARGS[@]}"