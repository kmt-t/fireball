#!/bin/bash
# Fireball Source Code Formatter (Bash)
# Applies formatters (Ruff for Python, clang-format for C++) without calling LLMs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

GROUP="all"
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Source Code Formatter

Usage:
  ./tools/format-src.sh [OPTIONS] [FILES...]

Options:
  -g, --group <name> Source group to format: cpp, python, concepts, formal, pysim, all (default: all).
  -c, --config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, --help          Show this help message.
EOF
    exit 0
}

FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -g|--group)  GROUP="$2"; shift 2 ;;
        -c|--config) CONFIG="$2"; shift 2 ;;
        -h|--help)   usage ;;
        *) FILES+=("$1"); shift ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "format-src"
          "--config" "$CONFIG" "--group" "$GROUP")

if [[ ${#FILES[@]} -gt 0 ]]; then
    CMD_ARGS+=("${FILES[@]}")
else
    COLLECTED=()
    GRP=$(echo "$GROUP" | tr '[:upper:]' '[:lower:]')
    if [[ "$GRP" == "all" || "$GRP" == "cpp" ]]; then
        for d in inc src; do
            if [[ -d "$d" ]]; then
                while IFS= read -r f; do
                    COLLECTED+=("$f")
                done < <(find "$d" -type f \( -name "*.hxx" -o -name "*.cxx" -o -name "*.c" -o -name "*.h" -o -name "*.cpp" \) | sort)
            fi
        done
    fi
    if [[ "$GRP" == "all" || "$GRP" == "python" || "$GRP" == "concepts" ]]; then
        if [[ -d "docs" ]]; then
            while IFS= read -r f; do
                COLLECTED+=("$f")
            done < <(find docs -type f -name "*_concept.py" | sort)
        fi
    fi
    if [[ "$GRP" == "all" || "$GRP" == "python" || "$GRP" == "formal" ]]; then
        if [[ -d "docs" ]]; then
            while IFS= read -r f; do
                COLLECTED+=("$f")
            done < <(find docs -type f \( -name "*_model.py" -o -path "*/formal/*.py" \) | sort)
        fi
    fi
    if [[ "$GRP" == "all" || "$GRP" == "python" || "$GRP" == "pysim" ]]; then
        if [[ -d "experiments/pysim" ]]; then
            while IFS= read -r f; do
                COLLECTED+=("$f")
            done < <(find experiments/pysim -type f -name "*.py" | sort)
        fi
    fi
    if [[ ${#COLLECTED[@]} -gt 0 ]]; then
        CMD_ARGS+=("${COLLECTED[@]}")
    fi
fi

exec uv "${CMD_ARGS[@]}"