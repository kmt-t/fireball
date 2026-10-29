#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
if [[ $# -ne 1 ]]; then
    printf 'Usage: %s <prefix>\n' "$0" >&2
    exit 2
fi

cd "$repo_root"
exec uv run --project "$script_dir/spec-integrator" python -m spec_integrator.cli \
    next-id --config spec-integrator.yaml "$1"
