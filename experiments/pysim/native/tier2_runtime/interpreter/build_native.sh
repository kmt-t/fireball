#!/usr/bin/env bash
# Build the required Tier 2 native Interpreter (Linux/WSL, Clang 17+).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${native_build_dir}"
source_cpp="${script_dir}/native_interpreter.cxx"
python_package_dir="${project_root}/experiments/pysim/tier2_runtime/interpreter"
echo ">>> Compiling native_interpreter.cxx -> libnative_interpreter.so"
clang++ -std=c++23 -O2 -Wall -Wextra -Wpedantic -shared -fPIC \
  "${source_cpp}" -o "${native_build_dir}/libnative_interpreter.so"
cp "${native_build_dir}/libnative_interpreter.so" "${python_package_dir}/libnative_interpreter.so"
echo "Built libnative_interpreter.so (all template variants available)"
