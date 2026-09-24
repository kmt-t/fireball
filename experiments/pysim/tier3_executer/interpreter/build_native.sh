#!/usr/bin/env bash
# Build the required native Tier 3 handler table (Linux/WSL, Clang 17+).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${native_build_dir}"
py_inc=$(uv run python -c 'import sysconfig; print(sysconfig.get_path("include"))')
py_ldlib=$(uv run python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')

echo ">>> Compiling native_interpreter.cxx -> _interpreter_native.so"
clang++ -std=c++23 -O2 -Wall -Wextra -Wpedantic -shared -fPIC -I"${py_inc}" \
  native_interpreter.cxx -o "${native_build_dir}/_interpreter_native.so" \
  ${py_ldlib:+-L"${py_ldlib}"}
cp "${native_build_dir}/_interpreter_native.so" _interpreter_native.so
echo "Built _interpreter_native.so"
