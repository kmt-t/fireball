#!/usr/bin/env bash
# Build the required native Tier 3 handler table (Linux/WSL, Clang 17+).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${native_build_dir}"
py_inc=$(uv run --offline --no-sync --python "${uv_python}" python -c 'import sysconfig; print(sysconfig.get_path("include"))')
py_ldlib=$(uv run --offline --no-sync --python "${uv_python}" python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')
trace_dispatch_capacity=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
  uv run --offline --no-sync --python "${uv_python}" python -c \
  'from config import JIT_CACHE_BANK_CAPACITY_BYTES, JIT_CACHE_BANK_COUNT, JIT_X64_TRACE_HEADER_BYTES; print(JIT_CACHE_BANK_COUNT * max(1, JIT_CACHE_BANK_CAPACITY_BYTES // JIT_X64_TRACE_HEADER_BYTES))')
block_dispatch_capacity=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
  uv run --offline --no-sync --python "${uv_python}" python -c \
  'from config import FB_CONF_MAX_BASIC_BLOCKS; print(FB_CONF_MAX_BASIC_BLOCKS)')
native_feature_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
  uv run --offline --no-sync --python "${uv_python}" python -c \
  'from config import FB_CONF_JIT_HOTSPOT_PROFILING, FB_CONF_RUNTIME_PROFILE_STATS; print(int(FB_CONF_RUNTIME_PROFILE_STATS), int(FB_CONF_JIT_HOTSPOT_PROFILING))')
read -r runtime_profile_stats jit_hotspot_profiling <<< "${native_feature_config}"
# QA and diagnostic builds can include counters while keeping collection
# runtime-selectable. The ordinary build follows config.py and compiles them out.
runtime_profile_stats="${FIREBALL_BUILD_RUNTIME_PROFILE_STATS:-${runtime_profile_stats}}"
if [[ "${runtime_profile_stats}" != 0 && "${runtime_profile_stats}" != 1 ]]; then
  echo "FB_CONF_RUNTIME_PROFILE_STATS must resolve to 0 or 1" >&2
  exit 2
fi
if [[ "${jit_hotspot_profiling}" != 0 && "${jit_hotspot_profiling}" != 1 ]]; then
  echo "FB_CONF_JIT_HOTSPOT_PROFILING must resolve to 0 or 1" >&2
  exit 2
fi

echo ">>> Compiling native_interpreter.cxx -> _interpreter_native.so"
clang++ -std=c++23 -O2 -Wall -Wextra -Wpedantic -shared -fPIC -I"${py_inc}" \
  -DFB_CONF_NATIVE_JIT_TRACE_CAPACITY="${trace_dispatch_capacity}" \
  -DFB_CONF_NATIVE_JIT_BLOCK_CAPACITY="${block_dispatch_capacity}" \
  -DFB_CONF_RUNTIME_PROFILE_STATS="${runtime_profile_stats}" \
  -DFB_CONF_JIT_HOTSPOT_PROFILING="${jit_hotspot_profiling}" \
  native_interpreter.cxx -o "${native_build_dir}/_interpreter_native.so" \
  ${py_ldlib:+-L"${py_ldlib}"}
cp "${native_build_dir}/_interpreter_native.so" _interpreter_native.so
echo "Built _interpreter_native.so (runtime stats=${runtime_profile_stats}, hotspot profiling=${jit_hotspot_profiling})"
