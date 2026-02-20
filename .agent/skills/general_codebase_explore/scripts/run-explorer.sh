# run-explorer.sh - Dispatcher for Fireball Explorer tools

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMMAND=$1
shift

case "$COMMAND" in
    # context subcommand was removed per 3c2b8d32-13ac-4bcf-a57c-5d78bc9ec866 protocol
    summary)
        # Pass all arguments to support --json
        python3 "$SCRIPT_DIR/generate_summary.py" "$@"
        ;;
    ast)
        python3 "$SCRIPT_DIR/explore_codebase.py" --ast "$@"
        ;;
    callers)
        python3 "$SCRIPT_DIR/explore_codebase.py" --callers "$@"
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
                    python3 "$SCRIPT_DIR/generate_summary.py" "$path" --json
                fi
            done
        else
            echo "Unknown pipe subcommand: $SUBCOMMAND"
        fi
        ;;
    *)
        # Default to explore_codebase.py (interactive or other flags)
        python3 "$SCRIPT_DIR/explore_codebase.py" "$COMMAND" "$@"
        ;;
esac
