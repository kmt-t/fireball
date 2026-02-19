#!/bin/bash
# explorer.sh - Dispatcher for Fireball Explorer tools

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMMAND=$1
shift

case "$COMMAND" in
    context)
        python3 "$SCRIPT_DIR/search_context.py" "$@"
        ;;
    summary)
        # Use explore_codebase.py's summary mode
        python3 "$SCRIPT_DIR/explore_codebase.py" --summary "$@"
        ;;
    graph)
        python3 "$SCRIPT_DIR/explore_codebase.py" --graph "$@"
        ;;
    symbols)
        python3 "$SCRIPT_DIR/explore_codebase.py" --symbols "$@"
        ;;
    report)
        python3 "$SCRIPT_DIR/generate_report.py" "$@"
        ;;
    pipe)
        SUBCOMMAND=$1
        shift
        if [ "$SUBCOMMAND" == "summary" ]; then
            while read -r path; do
                if [ -n "$path" ]; then
                    python3 "$SCRIPT_DIR/explore_codebase.py" --summary "$path"
                fi
            done
        else
            echo "Unknown pipe subcommand: $SUBCOMMAND"
        fi
        ;;
    *)
        # Default to explore_codebase.py (interactive or other flags)
        # Shift back to include COMMAND if it's actually an argument (like --help or --ast)
        python3 "$SCRIPT_DIR/explore_codebase.py" "$COMMAND" "$@"
        ;;
esac
