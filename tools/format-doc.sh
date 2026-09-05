#!/bin/bash
# Fireball Document Formatter (Bash)
# Normalizes markdown documents without calling LLMs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Document Formatter

Usage:
  ./tools/format-doc.sh [OPTIONS] [FILES...]

Options:
  -c, --config   Path to configuration file (default: spec-integrator.yaml).
  -h, --help     Show this help message.
EOF
    exit 0
}

FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config) CONFIG="$2"; shift 2 ;;
        -h|--help)   usage ;;
        *) FILES+=("$1"); shift ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "format-doc" "--config" "$CONFIG")

if [[ ${#FILES[@]} -gt 0 ]]; then
    CMD_ARGS+=("${FILES[@]}")
else
    while IFS= read -r f; do
        CMD_ARGS+=("$f")
    done < <(find docs -type f -name "*.md" | sort)
fi

exec uv "${CMD_ARGS[@]}"