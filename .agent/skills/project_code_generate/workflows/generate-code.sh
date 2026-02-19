#!/bin/bash
# WIT → C++ Header Generation Script
#
# Generates C++ headers from WIT package using wasm-tools.
# Works in both devcontainer and external environments.
# Usage: bash generate-code.sh

set -e

echo "[*] Generating C++ headers from WIT package..."

# Arguments
WIT_DIR="${1:-wit}"
OUT_DIR="${2:-inc/gen}"

# Detect if running inside container
if [ -f "/.dockerenv" ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    # Inside container - run directly
    echo "[*] Running inside container"
    python3 .agent/skills/project_code_generate/scripts/generate_cpp.py "$WIT_DIR" "$OUT_DIR"
else
    # Outside container - use docker exec
    echo "[*] Running from host, using Docker exec"
    
    CONTAINER_ID=$(docker ps --format "{{.ID}}" | head -1)
    
    if [ -z "$CONTAINER_ID" ]; then
        echo "[ERROR] No running Docker containers found"
        exit 1
    fi
    
    echo "[*] Using container: $CONTAINER_ID"
    
    # Run generator in container
    docker exec "$CONTAINER_ID" bash -c \
        "cd /workspaces/fireball && python3 .agent/skills/project_code_generate/scripts/generate_cpp.py $WIT_DIR $OUT_DIR"
fi

echo "[OK] Generation complete"
echo ""
echo "Generated files in $OUT_DIR/:"
ls -1 "$OUT_DIR"/*.hxx 2>/dev/null || echo "  (no .hxx files found)"
