#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_one() {
  local script_path="$1"
  echo "[verify] running $(basename "$script_path")"
  "$script_path"
}

case "${1:-all}" in
  all)
    run_one "$SCRIPT_DIR/run_eventdriven_coos.sh"
    run_one "$SCRIPT_DIR/run_ipc_deadlock.sh"
    run_one "$SCRIPT_DIR/run_loader_rollback.sh"
    run_one "$SCRIPT_DIR/run_vmmio.sh"
    ;;
  coos)
    run_one "$SCRIPT_DIR/run_eventdriven_coos.sh"
    ;;
  ipc-deadlock)
    run_one "$SCRIPT_DIR/run_ipc_deadlock.sh"
    ;;
  loader-rollback)
    run_one "$SCRIPT_DIR/run_loader_rollback.sh"
    ;;
  vmmio)
    run_one "$SCRIPT_DIR/run_vmmio.sh"
    ;;
  *)
    echo "Usage: $0 [all|coos|ipc-deadlock|loader-rollback|vmmio]"
    exit 1
    ;;
esac
