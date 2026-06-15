#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/components.sh"

run_one() {
  "$SCRIPT_DIR/run_component.sh" "$@"
}

case "${1:-all}" in
  all)
    for component_id in "${VERIFY_COMPONENT_IDS[@]}"; do
      run_one "$component_id"
    done
    ;;
  list)
    printf '%-18s %s\n' "component" "model"
    for component_id in "${VERIFY_COMPONENT_IDS[@]}"; do
      model="$(verify_find_artifact verify/models .tla "$component_id")"
      printf '%-18s %s\n' "$component_id" "${model##*/}"
    done
    ;;
  *)
    run_one "$@"
    ;;
esac
