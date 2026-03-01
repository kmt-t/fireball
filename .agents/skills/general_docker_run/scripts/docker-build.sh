#!/bin/bash
# docker-build.sh: Run Meson/Ninja build inside devcontainer
# Usage: ./docker-build.sh [build-dir] [--test] [--clean]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BUILD_DIR="${1:-build}"
RUN_TEST=false
CLEAN=false

for arg in "$@"; do
    case $arg in
        --test) RUN_TEST=true ;;
        --clean) CLEAN=true ;;
    esac
done

CMD=""
if [ "$CLEAN" = true ]; then CMD="rm -rf $BUILD_DIR && "; fi
CMD="${CMD}meson setup $BUILD_DIR && ninja -C $BUILD_DIR"
if [ "$RUN_TEST" = true ]; then CMD="${CMD} && meson test -C $BUILD_DIR --verbose"; fi

bash "$SCRIPT_DIR/docker-run-command.sh" $CMD
