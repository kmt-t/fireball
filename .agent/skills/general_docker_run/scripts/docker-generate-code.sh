#!/bin/bash
# docker-generate-code.sh: Run code generator inside devcontainer
# Usage: ./docker-generate-code.sh [subcommand] [args...]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="bash .agent/skills/project_code_generate/scripts/run-codegen.sh $@"

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD

