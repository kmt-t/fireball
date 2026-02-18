#!/bin/bash
# Wrapper to run explorer scripts inside the devcontainer

# Determine the project root (assumed to be where .devcontainer folder is)
# We are currently in .agent/skills/explorer/scripts/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"

# Check if docker-compose.yml exists in .devcontainer
if [ ! -f "$PROJECT_ROOT/.devcontainer/docker-compose.yml" ]; then
    echo "Error: .devcontainer/docker-compose.yml not found."
    exit 1
fi

# The service name in docker-compose is typically 'app' or 'dev'
SERVICE_NAME="fireball-dev"

# Execute the python script inside the container
# We map the arguments passed to this script to the python script
# The path inside the container should mirror the host path relative to the mount point
# Assuming the workspace is mounted at /workspace or similar.
# For simplicity, we assume the working directory in container is the project root.

# Construct the command to run inside container
# We need to translate host paths to container paths if they are absolute paths,
# but for simplicity, we assume relative paths from project root are used.

# Run using docker compose
# -T: Disable pseudo-tty allocation (for piping)
# --rm: Remove container after exit
docker compose -f "$PROJECT_ROOT/.devcontainer/docker-compose.yml" run --rm -T $SERVICE_NAME //bin/bash .agent/skills/explorer/scripts/explorer-cli "$@"
