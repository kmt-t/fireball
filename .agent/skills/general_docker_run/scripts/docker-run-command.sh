#!/bin/bash
# docker-cmd.sh: Run arbitrary command inside devcontaine
# Usage: ./docker-cmd.sh [command] [args...]
# Example: ./docker-cmd.sh find src -name "*.cxx"
# Example: ./docker-cmd.sh make test

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/.devcontainer/docker-compose.yml"
SERVICE_NAME="fireball-dev"

# Ensure container is running
if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Starting $SERVICE_NAME..." >&2
    docker compose -f "$COMPOSE_FILE" up -d
fi

# Use //workspaces/fireball to prevent Git Bash path conversion for the container path
# -w sets the working directory inside the containe
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" "$@"
