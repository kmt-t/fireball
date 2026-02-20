#!/bin/bash
# docker-check-cpp.sh: Run embedded C++ rule check inside devcontainer
# Usage: ./docker-check-cpp.sh [path...]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="python3 .agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py $@"

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD
