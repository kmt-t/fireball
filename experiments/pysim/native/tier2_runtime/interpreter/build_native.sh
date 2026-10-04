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
trace_dispatch_capacity=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
  uv run --offline --no-sync --python "${uv_python}" python -c \
  'from config import JIT_CACHE_BANK_CAPACITY_BYTES, JIT_CACHE_BANK_COUNT, JIT_X64_TRACE_HEADER_BYTES; print(JIT_CACHE_BANK_COUNT * max(1, JIT_CACHE_BANK_CAPACITY_BYTES // JIT_X64_TRACE_HEADER_BYTES))')
block_dispatch_capacity=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
  uv run --offline --no-sync --python "${uv_python}" python -c \
  'from config import FB_CONF_MAX_BASIC_BLOCKS; print(FB_CONF_MAX_BASIC_BLOCKS)')
echo ">>> Compiling native_interpreter.cxx -> libnative_interpreter.so"
clang++ -std=c++23 -O2 -Wall -Wextra -Wpedantic -shared -fPIC \
  -DFB_CONF_NATIVE_JIT_TRACE_CAPACITY="${trace_dispatch_capacity}" \
  -DFB_CONF_NATIVE_JIT_BLOCK_CAPACITY="${block_dispatch_capacity}" \
  "${source_cpp}" -o "${native_build_dir}/libnative_interpreter.so"
cp "${native_build_dir}/libnative_interpreter.so" "${python_package_dir}/libnative_interpreter.so"
echo "Built libnative_interpreter.so (all template variants available)"
