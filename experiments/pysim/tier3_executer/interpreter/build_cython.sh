#!/usr/bin/env bash
# Builds the optional Cython pure-Python-mode Tier 3 interpreter accelerator.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${native_build_dir}"

py_inc=$(uv run python -c 'import sysconfig; print(sysconfig.get_path("include"))')
py_ldlib=$(uv run python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')
generated_c="${native_build_dir}/interpreter.c"

echo ">>> Transpiling interpreter.py -> ${generated_c} (Cython)"
uv run python -B -m cython "${script_dir}/interpreter.py" -3 -o "${generated_c}"

echo ">>> Compiling interpreter.c -> interpreter.so (clang)"
clang -O2 -shared -fPIC \
  -I"${py_inc}" \
  "${generated_c}" -o "${script_dir}/interpreter.so" \
  ${py_ldlib:+-L"${py_ldlib}"}
echo "Built ${script_dir}/interpreter.so"
