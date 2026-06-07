#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

tlc -config verify/configs/EventDrivenCOOS_ThreeState.cfg \
  verify/models/EventDrivenCOOS_ThreeState.tla "$@"
