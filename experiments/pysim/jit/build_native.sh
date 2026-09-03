#!/usr/bin/env bash
# Builds the optional native_trace_call Cython accelerator (Linux/WSL, clang).
# See native_trace_call.pyx for what this replaces. Requires: `uv pip install cython`
# (already in requirements.txt) and clang on PATH.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY_INC=$(uv run python -c "import sysconfig; print(sysconfig.get_path('include'))")
PY_LDLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")

echo ">>> Transpiling native_trace_call.pyx -> .c (Cython)"
uv run cython native_trace_call.pyx -3 -o native_trace_call.c

echo ">>> Compiling native_trace_call.c -> .so (clang)"
clang -O2 -shared -fPIC \
    -I"${PY_INC}" \
    native_trace_call.c -o native_trace_call.so \
    ${PY_LDLIB:+-L"${PY_LDLIB}"}

echo "Built native_trace_call.so"
