#!/bin/bash
# TLA+ formal verification runner
# Checks system specifications using TLC model checker

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TLA_DIR="$PROJECT_ROOT/tla"
TOOL_JAR="${TLA_JAR:-tla2tools.jar}"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
  cat <<EOF
Usage: $0 [MODULE] [OPTIONS]

Run TLA+ model checker (TLC) on Fireball system specifications.

Modules:
  coos_scheduler         COOS task scheduler
  ipc_handoff           IPC ownership transfer
  hal_coos_irq          Interrupt handling
  interpreter_dispatch  WASM interpreter dispatch
  jit_patching          JIT copy-and-patch
  jit_performance       JIT cache performance
  loader_rollback       WASM loader rollback
  vmmio_tlb             Virtual MMU TLB
  vmmio_vdma            Virtual DMA
  vsoc_engine           vSoC execution engine
  all                   Run all modules (default)

Options:
  -c, --coverage        Generate coverage report
  -d, --depth N         Set search depth (default: unlimited)
  -w, --workers N       Number of workers (default: 1)
  -h, --help            Show this help

Examples:
  $0                              # Check all modules
  $0 coos_scheduler               # Check COOS scheduler only
  $0 coos_scheduler --depth 10    # Limited depth search
  $0 --workers 4                  # Parallel checking with 4 workers

EOF
  exit 1
}

check_prereq() {
  if ! command -v java &> /dev/null; then
    echo -e "${RED}Error: Java not found. Install with: sudo apt install default-jre${NC}"
    exit 1
  fi

  if [ ! -f "$TOOL_JAR" ] && [ ! -f "$TLA_DIR/../tools/$TOOL_JAR" ]; then
    echo -e "${RED}Error: tla2tools.jar not found.${NC}"
    echo "Download from: https://github.com/tlaplus/tlaplus/releases"
    echo "Place in PATH or tools/ directory"
    exit 1
  fi

  if [ ! -f "$TOOL_JAR" ]; then
    TOOL_JAR="$TLA_DIR/../tools/$TOOL_JAR"
  fi
}

run_tlc() {
  local module="$1"
  local depth="${2:-}"
  local workers="${3:-1}"
  local coverage="${4:-}"

  echo -e "${YELLOW}Checking $module...${NC}"

  local cmd="java -cp $TOOL_JAR tlc.TLC"
  cmd="$cmd -modeldir $TLA_DIR"

  if [ -n "$depth" ] && [ "$depth" != "unlimited" ]; then
    cmd="$cmd -maxSetSize $depth"
  fi

  if [ "$workers" -gt 1 ]; then
    cmd="$cmd -workers $workers"
  fi

  if [ -n "$coverage" ]; then
    cmd="$cmd -coverage 1"
  fi

  cmd="$cmd $module"

  if $cmd; then
    echo -e "${GREEN}✓ $module OK${NC}"
    return 0
  else
    echo -e "${RED}✗ $module FAILED${NC}"
    return 1
  fi
}

main() {
  local module="all"
  local depth=""
  local workers="1"
  local coverage=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) usage ;;
      -c|--coverage) coverage="1"; shift ;;
      -d|--depth) depth="$2"; shift 2 ;;
      -w|--workers) workers="$2"; shift 2 ;;
      all|coos_scheduler|ipc_handoff|hal_coos_irq|interpreter_dispatch|jit_patching|jit_performance|loader_rollback|vmmio_tlb|vmmio_vdma|vsoc_engine)
        module="$1"; shift ;;
      *) echo "Unknown option: $1"; usage ;;
    esac
  done

  check_prereq

  local failed=0

  if [ "$module" = "all" ]; then
    for spec in coos_scheduler ipc_handoff hal_coos_irq interpreter_dispatch jit_patching jit_performance loader_rollback vmmio_tlb vmmio_vdma vsoc_engine; do
      if ! run_tlc "$spec" "$depth" "$workers" "$coverage"; then
        ((failed++))
      fi
    done
  else
    run_tlc "$module" "$depth" "$workers" "$coverage" || ((failed++))
  fi

  echo ""
  if [ $failed -eq 0 ]; then
    echo -e "${GREEN}All checks passed${NC}"
    exit 0
  else
    echo -e "${RED}$failed module(s) failed${NC}"
    exit 1
  fi
}

main "$@"
