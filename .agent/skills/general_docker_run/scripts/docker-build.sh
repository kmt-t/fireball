#!/bin/bash
# Mesonビルドをコンテナで実行（Bash版）
# Usage: bash docker-build.sh [build-dir] [--test] [--clean]

set -e

BUILD_DIR="${1:-build}"
RUN_TEST=false
CLEAN=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --test)
            RUN_TEST=true
            ;;
        --clean)
            CLEAN=true
            ;;
    esac
done

# Find containe
CONTAINER_ID=$(docker ps --format "{{.ID}}" | head -1)

if [ -z "$CONTAINER_ID" ]; then
    echo "[ERROR] No running container found"
    exit 1
fi

echo "[*] Using container: $CONTAINER_ID"

# Clean if requested
if [ "$CLEAN" = true ]; then
    echo "[*] Cleaning build directory..."
    docker exec -w //workspaces/fireball "$CONTAINER_ID" rm -rf "$BUILD_DIR"
fi

# Setup
echo "[*] Running meson setup..."
docker exec -w //workspaces/fireball "$CONTAINER_ID" meson setup "$BUILD_DIR"

# Build
echo "[*] Running ninja build..."
docker exec -w //workspaces/fireball "$CONTAINER_ID" ninja -C "$BUILD_DIR"

# Test if requested
if [ "$RUN_TEST" = true ]; then
    echo "[*] Running tests..."
    docker exec -w //workspaces/fireball "$CONTAINER_ID" meson test -C "$BUILD_DIR" --verbose
fi

echo "[OK] Build complete"
