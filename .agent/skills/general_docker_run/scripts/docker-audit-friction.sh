#!/bin/bash
# docker-audit-friction.sh: Run friction audit inside devcontainer
# Usage: ./docker-audit-friction.sh [args...]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="python3 .agent/skills/project_friction_audit/scripts/audit_friction.py $@"

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD

