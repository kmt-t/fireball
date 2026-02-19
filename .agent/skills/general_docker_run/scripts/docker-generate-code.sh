#!/bin/bash
# docker-codegen.sh: Run code generator inside devcontaine
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
    CMD="bash .agent/skills/code_generator/workflows/run-workflow.sh"
else
    # Execute arbitrary command in code_generator scope
    # Handle renaming of known scripts for backward compatibility or direct calls
    SNAME="$1"
    if [ "$SNAME" == "wit_gen.sh" ]; then SNAME="generate-code.sh"; fi
    if [ "$SNAME" == "wit_check.sh" ]; then SNAME="check-quality.sh"; fi
    if [ "$SNAME" == "wit_build.sh" ]; then SNAME="build-project.sh"; fi

    if [[ "$SNAME" == *.sh ]]; then
         # Check if it's a workflow script
         if [ -f "$PROJECT_ROOT/.agent/skills/code_generator/workflows/$SNAME" ]; then
             CMD="bash .agent/skills/code_generator/workflows/$SNAME ${@:2}"
         elif [ -f "$PROJECT_ROOT/.agent/skills/code_generator/scripts/$SNAME" ]; then
             CMD="bash .agent/skills/code_generator/scripts/$SNAME ${@:2}"
         else
             # Assume direct command
             CMD="$@"
         fi
    else
         CMD="$@"
    fi
fi

# -w //workspaces/fireball sets the working directory inside the containe
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" $CMD

