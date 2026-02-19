#!/bin/bash
# docker-tlc.sh: Run TLC model checker inside devcontaine

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/.devcontainer/docker-compose.yml"
SERVICE_NAME="fireball-dev"

if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Starting $SERVICE_NAME..."
    docker compose -f "$COMPOSE_FILE" up -d
fi

if [ -z "$1" ]; then
    echo "Usage: $0 <tla_file>"
    exit 1
fi

CMD="tlc $@"

# -w //workspaces/fireball sets the working directory inside the containe
docker compose -f "$COMPOSE_FILE" exec -T -u developer -w //workspaces/fireball "$SERVICE_NAME" $CMD

