#!/bin/bash
# docker-generate-wit.sh: Run WIT code generation inside devcontainer
# Usage: ./docker-generate-wit.sh [-a|--all] [wit_file]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$1" == "-a" ] || [ "$1" == "--all" ]; then
    CMD="python3 .agent/skills/project_code_generate/scripts/generate_cpp.py wit/ inc/gen"
else
    WIT_FILE="${1:-wit/types.wit}"
    CMD="python3 .agent/skills/project_code_generate/scripts/generate_cpp.py $WIT_FILE inc/gen"
fi

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD
