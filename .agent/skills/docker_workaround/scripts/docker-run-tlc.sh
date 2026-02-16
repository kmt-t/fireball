#!/bin/bash

# Ensure docker-compose service is running
# This script assumes 'fireball-dev' service is defined in .devcontainer/docker-compose.yml

SERVICE_NAME="fireball-dev"
COMPOSE_FILE=".devcontainer/docker-compose.yml"

# Check if the compose file exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Error: $COMPOSE_FILE not found."
    echo "Please ensure you are running this script from the workspace root."
    exit 1
fi

# Check if the service container is running
if ! docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "$SERVICE_NAME"; then
    echo "Service '$SERVICE_NAME' is not running. Starting it..."
    docker compose -f "$COMPOSE_FILE" up -d
fi

# Convert Windows path to container path if necessary (simple heuristic)
# Assuming run from workspace root n:\sources\fireball -> /workspaces/fireball
TARGET_FILE=$1
if [ -z "$TARGET_FILE" ]; then
    echo "Usage: $0 <tla-file>"
    exit 1
fi

# Execute tlc in the container using docker-compose exec
# This ensures we use the correct environment and user
echo "Running TLC on $TARGET_FILE in service $SERVICE_NAME..."

docker compose -f "$COMPOSE_FILE" exec -u developer $SERVICE_NAME bash -c "cd /workspaces/fireball && tlc $TARGET_FILE"
