#!/bin/bash
# WIT Build Test Script
#
# Tests generated C++ headers by building the project.
# Works in both devcontainer and external environments.
# Usage: bash wit_build.sh

set -e

echo "[*] Testing generated headers with build..."

# Detect if running inside container
if [ -f "/.dockerenv" ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    # Inside container - run directly
    echo "[*] Running inside container"
    cd /workspaces/fireball
    
    echo "[*] Running meson setup..."
    meson setup build --wipe || meson setup build
    
    echo "[*] Running ninja build..."
    ninja -C build
else
    # Outside container - use docker exec
    echo "[*] Running from host, using Docker exec"
    
    CONTAINER_ID=$(docker ps --format "{{.ID}}" | head -1)
    
    if [ -z "$CONTAINER_ID" ]; then
        echo "[ERROR] No running container found"
        exit 1
    fi
    
    echo "[*] Using container: $CONTAINER_ID"
    
    echo "[*] Running meson setup..."
    docker exec "$CONTAINER_ID" bash -c \
        "cd /workspaces/fireball && meson setup build --wipe || meson setup build"
    
    echo "[*] Running ninja build..."
    docker exec "$CONTAINER_ID" bash -c \
        "cd /workspaces/fireball && ninja -C build"
fi

echo "[OK] Build successful"
