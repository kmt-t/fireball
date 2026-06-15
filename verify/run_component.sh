#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/components.sh"

usage() {
  cat <<'EOF'
Usage: ./verify/run_component.sh <component-name> [tlc-args...]

Component names:
  coos | eventdriven_coos
  ipc-deadlock | ipc_deadlock
  loader-rollback | loader_rollback
  vmmio
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

requested_name="$1"
shift

if ! component_id="$(verify_resolve_component_id "$requested_name")"; then
  echo "[verify] unknown component name: $requested_name" >&2
  usage >&2
  exit 1
fi

model="$(verify_find_artifact verify/models .tla "$component_id")" || {
  echo "[verify] no model matched component: $component_id" >&2
  exit 1
}
config="$(verify_find_artifact verify/configs .cfg "$component_id")" || {
  echo "[verify] no config matched component: $component_id" >&2
  exit 1
}
report="$(verify_find_artifact verify/reports .md "$component_id")" || {
  echo "[verify] no report matched component: $component_id" >&2
  exit 1
}

for path in "$model" "$config" "$report"; do
  if [[ ! -f "$REPO_ROOT/$path" ]]; then
    echo "[verify] missing file: $path" >&2
    exit 1
  fi
done

if ! command -v tlc >/dev/null 2>&1; then
  echo "[verify] tlc not found in PATH" >&2
  exit 127
fi

echo "[verify] component=$component_id"
echo "[verify] model=$model config=$config report=$report"

cd "$REPO_ROOT"
exec tlc -config "$config" "$model" "$@"
