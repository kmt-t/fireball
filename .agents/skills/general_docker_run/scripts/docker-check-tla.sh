#!/bin/bash
# docker-check-tla.sh: Run TLA+ model checker inside devcontainer
# Usage: ./docker-check-tla.sh <tla_file>

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$1" ]; then
    echo "Usage: $0 <tla_file>"
    exit 1
fi

CMD="tlc $@"

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD

