#!/bin/bash
# Fireball Content Complexity & Risk Assessment Runner (Bash)
# Scores requirement/design keywords complexity and design risk via LLM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

MAX_KEYWORDS=15
EXHAUSTIVE=""
MIN_REFERENCES=0
BACKEND=""
MODEL=""
CONFIG="spec-integrator.yaml"

usage() {
    cat <<'EOF'
Fireball Keyword Risk Assessment (LLM)

Usage:
  ./tools/risk.sh [OPTIONS]

Options:
  --max-keywords <N>    Maximum keywords to assess (default: 15, 0 for unlimited).
  -a, --exhaustive      Assess all keywords without limit.
  --min-references <N>  Minimum referencing sections required to include a keyword (default: 0).
  --backend <name>      LLM backend override (openrouter, sakura, ollama, mock).
  --model <name>        LLM model name override.
  -c, --config <path>   Path to configuration file (default: spec-integrator.yaml).
  -h, --help            Show this help message.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-keywords)   MAX_KEYWORDS="$2"; shift 2 ;;
        -a|--exhaustive)  EXHAUSTIVE="-a"; shift ;;
        --min-references) MIN_REFERENCES="$2"; shift 2 ;;
        --backend)        BACKEND="$2"; shift 2 ;;
        --model)          MODEL="$2"; shift 2 ;;
        -c|--config)      CONFIG="$2"; shift 2 ;;
        -h|--help)        usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

CMD_ARGS=("run" "--system-certs" "--project" "tools/spec-integrator"
          "python" "-m" "spec_integrator.cli" "risk"
          "--config" "$CONFIG" "--max-keywords" "$MAX_KEYWORDS"
          "--min-references" "$MIN_REFERENCES")
if [[ -n "$EXHAUSTIVE" ]]; then CMD_ARGS+=("$EXHAUSTIVE"); fi
if [[ -n "$BACKEND" ]]; then CMD_ARGS+=("--backend" "$BACKEND"); fi
if [[ -n "$MODEL" ]]; then CMD_ARGS+=("--model" "$MODEL"); fi

exec uv "${CMD_ARGS[@]}"
