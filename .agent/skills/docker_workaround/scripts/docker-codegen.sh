#!/bin/bash
# docker-codegen.sh: Run code generator inside devcontainer
# Usage: ./docker-codegen.sh [script_name] [args...]
# Default: runs workflows/wit_all.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/.devcontainer/docker-compose.yml"
SERVICE_NAME="fireball-dev"

if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Starting $SERVICE_NAME..."
    docker compose -f "$COMPOSE_FILE" up -d
fi

if [ "$#" -eq 0 ]; then
    CMD="bash .agent/skills/code_generator/workflows/wit_all.sh"
else
    # Execute arbitrary command in code_generator scope
    # If arg is a script name like "wit_check.sh", run that.
    if [[ "$1" == *.sh ]]; then
         # Check if it's a workflow script
         if [ -f "$PROJECT_ROOT/.agent/skills/code_generator/workflows/$1" ]; then
             CMD="bash .agent/skills/code_generator/workflows/$1 ${@:2}"
         elif [ -f "$PROJECT_ROOT/.agent/skills/code_generator/scripts/$1" ]; then
             CMD="bash .agent/skills/code_generator/scripts/$1 ${@:2}"
         else
             # Assume direct command
             CMD="$@"
         fi
    else
         CMD="$@"
    fi
fi

# -w //workspaces/fireball sets the working directory inside the container
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" $CMD

