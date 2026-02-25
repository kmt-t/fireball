#!/bin/bash
# docker-explore-codebase.sh: Run codebase explorer inside devcontainer
# Usage: ./docker-explore-codebase.sh [args...]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="bash .agent/skills/general_codebase_explore/scripts/run-explorer.sh $@"

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD
