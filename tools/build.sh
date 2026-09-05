#!/bin/bash
# Fireball Specification Database Builder (Bash)
# Builds database and extracts TF-IDF candidate keywords/terms.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CLEAN=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Specification Database Builder

Usage:
  ./tools/build.sh [OPTIONS] [FILES...]

Options:
  --clean        Clear cache DB and rebuild cleanly.
  -c, --config   Path to configuration file (default: spec-integrator.yaml).
  -h, --help     Show this help message.
EOF
    exit 0
}

FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean) CLEAN="--clean"; shift ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) FILES+=("$1"); shift ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "build" "--config" "$CONFIG")
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
