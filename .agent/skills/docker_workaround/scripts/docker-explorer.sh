#!/bin/bash
# docker-explorer.sh: Run explorer inside devcontainer
# Usage: ./docker-explorer.sh [args...]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is 4 levels up: .agent/skills/docker_workaround/scripts/ -> ../../../../
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/.devcontainer/docker-compose.yml"
SERVICE_NAME="fireball-dev"

# Check if container is running
if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Starting $SERVICE_NAME..."
    docker compose -f "$COMPOSE_FILE" up -d
fi

# Determine command
if [ "$#" -eq 0 ]; then
    # Interactive mode
    CMD="python3 .agent/skills/explorer/scripts/explorer.py"
elif [ "$1" == "exec" ]; then
    shift
    CMD="$@"
elif [ "$1" == "summary" ]; then
    shift
    CMD="bash .agent/skills/explorer/scripts/explorer-cli summary $@"
else
    CMD="bash .agent/skills/explorer/scripts/explorer-cli $@"
fi

# Run exec
# -w //workspaces/fireball sets the working directory inside the container
# We don't use double quotes around $CMD to allow it to be split into command + args
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" $CMD

