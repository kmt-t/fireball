#!/bin/bash
# docker-run-command.sh: Run arbitrary command inside devcontainer
# Usage: ./docker-run-command.sh [command] [args...]
# Example: ./docker-run-command.sh find src -name "*.cxx"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
DOCKERFILE="$PROJECT_ROOT/.devcontainer/Dockerfile"
IMAGE_NAME="fireball-dev"
CONTAINER_NAME="fireball-dev-container"

# Build image if not exists
if [[ "$(docker images -q $IMAGE_NAME 2> /dev/null)" == "" ]]; then
    echo "Building $IMAGE_NAME..." >&2
    docker build -t "$IMAGE_NAME" -f "$DOCKERFILE" "$PROJECT_ROOT"
fi

# Ensure container is running
if ! docker ps --filter "name=$CONTAINER_NAME" --filter "status=running" | grep -q "$CONTAINER_NAME"; then
    echo "Starting $CONTAINER_NAME..." >&2
    # Remove existing container if it exists but is not running
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    # Standardize mount point and working directory to //workspace
    docker run -d --name "$CONTAINER_NAME" -v "$PROJECT_ROOT://workspace" -w //workspace "$IMAGE_NAME" tail -f /dev/null
fi

# Execute command
docker exec -i -u developer -w //workspace "$CONTAINER_NAME" "$@"
