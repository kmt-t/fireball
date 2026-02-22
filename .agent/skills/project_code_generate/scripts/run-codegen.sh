#!/bin/bash
# codegen.sh: Unified entry point for WIT-based code generation and verification
# Usage: ./codegen.sh [subcommand] [args...]
# Subcommands:
#   generate [wit_dir] [out_dir]  : Generate C++ headers from WIT
#   check [targets...]           : Run quality checks (naming, violations)
#   build [build_dir] [--test]   : Build the project and optionally run tests
#   all [wit_dir] [out_dir]      : Run the full workflow (generate -> check -> build)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD=python3

function cmd_generate() {
    local WIT_DIR="${1:-wit}"
    local OUT_DIR="${2:-inc/core}"
    $PYTHON_CMD "$SCRIPT_DIR/generate_cpp.py" "$WIT_DIR" "$OUT_DIR"
}

function cmd_check() {
    local TARGETS=("$@")
    if [ ${#TARGETS[@]} -eq 0 ]; then
        TARGETS=("inc/core")
    fi
    
    $PYTHON_CMD "$SCRIPT_DIR/check_violations.py" "${TARGETS[@]}"
    $PYTHON_CMD "$SCRIPT_DIR/check_naming.py" "${TARGETS[@]}"
}

function cmd_build() {
    local BUILD_DIR="${1:-build}"
    local RUN_TEST=false
    shift || true
    for arg in "$@"; do
        if [ "$arg" == "--test" ]; then RUN_TEST=true; fi
    done

    export CC=clang
    export CXX=clang++

    rm -rf "$BUILD_DIR"
    meson setup "$BUILD_DIR"
    ninja -C "$BUILD_DIR"
    if [ "$RUN_TEST" = true ]; then
        meson test -C "$BUILD_DIR" --verbose
    fi
}

case "$1" in
    generate)
        shift
        cmd_generate "$@"
        ;;
    check)
        shift
        cmd_check "$@"
        ;;
    build)
        shift
        cmd_build "$@"
        ;;
    all)
        shift
        cmd_generate "$@"
        cmd_check "${2:-inc/core}"
        cmd_build build --test
        ;;
    *)
        echo "Usage: $0 {generate|check|build|all} [args...]"
        exit 1
        ;;
esac
