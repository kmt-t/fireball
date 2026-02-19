# WIT自動生成をコンテナで実行（Bash版）
# Usage: bash docker-gen-wit.sh [-a|--all] [wit_file]

set -e

CONTAINER_ID=$(docker ps --format "{{.ID}}" | head -1)

if [ -z "$CONTAINER_ID" ]; then
    echo "[ERROR] No running container found"
    exit 1
fi

if [ "$1" == "-a" ] || [ "$1" == "--all" ]; then
    echo "[*] Generating C++ headers for all WIT files"
    docker exec -w //workspaces/fireball "$CONTAINER_ID" python3 .agent/skills/project_code_generate/scripts/generate_cpp.py wit/ inc/gen
else
    WIT_FILE="${1:-wit/types.wit}"
    echo "[*] Generating C++ header from $WIT_FILE"
    docker exec -w //workspaces/fireball "$CONTAINER_ID" python3 .agent/skills/project_code_generate/scripts/generate_cpp.py "$WIT_FILE" inc/gen
fi

echo "[OK] Generation complete"
