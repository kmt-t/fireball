#!/bin/bash
# Fireball strict concept/test/scenario coverage matrix gate (Bash).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Verification Matrix Check

Usage:
  ./tools/check-verification-matrix.sh [OPTIONS]

Options:
  -c, --config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, --help          Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for $1" >&2
                usage >&2
                exit 2
            fi
            CONFIG="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

exec uv run --system-certs --project tools/spec-integrator \
    python tools/check_verification_matrix.py "$CONFIG"
