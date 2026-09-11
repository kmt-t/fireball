#!/usr/bin/env bash
# Builds optional Cython pure-python-mode acceleration for pysim's hottest
# interpreter modules (Linux/WSL, clang). See leb128.py / interpreter.py's
# `@cython.locals(...)`-annotated functions for what this compiles.
# Requires: `uv pip install cython` (already in requirements.txt) and clang
# on PATH.
#
# Not building this is always safe: `import cython` provides a no-op shim at
# runtime, so every module here runs as plain, unaccelerated Python with
# identical behavior when no .so is present.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY_INC=$(uv run python -c "import sysconfig; print(sysconfig.get_path('include'))")
PY_LDLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")

# Dependency order: see build_native.ps1's comment.
for mod in leb128 interpreter runtime_engine; do
    echo ">>> Transpiling ${mod}.py -> .c (Cython)"
    uv run cython "${mod}.py" -3 -o "${mod}.c"

    echo ">>> Compiling ${mod}.c -> .so (clang)"
    clang -O2 -shared -fPIC \
        -I"${PY_INC}" \
        "${mod}.c" -o "${mod}.so" \
        ${PY_LDLIB:+-L"${PY_LDLIB}"}

    echo "Built ${mod}.so"
done
