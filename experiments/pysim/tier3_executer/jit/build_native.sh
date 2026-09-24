#!/usr/bin/env bash
# Builds the required native x64 Copy-and-Patch compiler extension (Linux/WSL).
# Requires Clang 17+ on PATH.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
NATIVE_BUILD_DIR="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${NATIVE_BUILD_DIR}"
SOURCE_CPP="${script_dir}/native_trace_call.cxx"
GENERATED_SO="${NATIVE_BUILD_DIR}/native_trace_call.so"

PY_INC=$(uv run python -c "import sysconfig; print(sysconfig.get_path('include'))")
PY_LDLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")

echo ">>> Compiling native_trace_call.cxx -> .so (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC \
    -I"${PY_INC}" \
    "${SOURCE_CPP}" -o "${GENERATED_SO}" \
    ${PY_LDLIB:+-L"${PY_LDLIB}"}

cp "${GENERATED_SO}" native_trace_call.so
echo "Built native_trace_call.so"
