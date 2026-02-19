#!/bin/bash
# docker-explore-codebase.sh: Run codebase explorer inside devcontainer
# Usage: ./docker-explore-codebase.sh [command] [args...]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/.devcontainer/docker-compose.yml"
SERVICE_NAME="fireball-dev"

if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Starting $SERVICE_NAME..." >&2
    docker compose -f "$COMPOSE_FILE" up -d
fi

# -w //workspaces/fireball sets the working directory inside the container
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" \
    bash .agent/skills/general_codebase_explore/scripts/explore-codebase.sh "$@"
