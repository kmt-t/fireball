#!/usr/bin/env bash
# Linux/WSL build of the C CPS handler chain.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native-cps"
mkdir -p "${native_build_dir}"

py_inc=$(uv run python -c 'import sysconfig; print(sysconfig.get_path("include"))')
generated_c="${native_build_dir}/_interpreter_cps_native.c"
uv run python -B -m cython "${script_dir}/cps_chain.pyx" -3 \
  --module-name _interpreter_cps_native -o "${generated_c}"
clang++ -O2 -shared -fPIC -I"${py_inc}" "${generated_c}" \
  -o "${native_build_dir}/_interpreter_cps_native.so"
echo "Built ${native_build_dir}/_interpreter_cps_native.so"
