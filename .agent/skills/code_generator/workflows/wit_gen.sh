#!/bin/bash
# WIT → C++ Header Generation Script
#
# Generates C++ headers from WIT package using wasm-tools.
# Works in both devcontainer and external environments.
# Usage: bash wit_gen.sh

set -e

echo "[*] Generating C++ headers from WIT package..."

# Detect if running inside container
if [ -f "/.dockerenv" ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    # Inside container - run directly
    echo "[*] Running inside container"
    cd /workspaces/fireball
    python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen
else
    # Outside container - use docker exec
    echo "[*] Running from host, using Docker exec"
    
    CONTAINER_ID=$(docker ps --format "{{.ID}}" | head -1)
    
    if [ -z "$CONTAINER_ID" ]; then
        echo "[ERROR] No running Docker containers found"
        echo ""
        echo "Available containers:"
        docker ps -a --format "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"
        exit 1
    fi
    
    echo "[*] Using container: $CONTAINER_ID"
    
    # Check if wasm-tools is available
    docker exec "$CONTAINER_ID" bash -c "which wasm-tools" &>/dev/null || {
        echo "[ERROR] wasm-tools not found in container"
        exit 1
    }
    
    # Run generator in container
    docker exec "$CONTAINER_ID" bash -c \
        "cd /workspaces/fireball && python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen"
fi

echo "[OK] Generation complete"
echo ""
echo "Generated files in inc/gen/:"
ls -1 inc/gen/*.hxx 2>/dev/null || echo "  (no .hxx files found)"
