#!/usr/bin/env bash
# Builds the standalone native x64 Copy-and-Patch compiler and cache (Linux/WSL).
# Requires Clang 17+ on PATH.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
NATIVE_BUILD_DIR="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${NATIVE_BUILD_DIR}"
native_source_dir="${project_root}/experiments/pysim/native/tier3_executer/jit"
SOURCE_CPP="${native_source_dir}/trace_compiler.cxx"
GENERATED_SO="${NATIVE_BUILD_DIR}/libtrace_compiler.so"
CACHE_SOURCE_CPP="${native_source_dir}/fast_cache.cxx"
CACHE_GENERATED_SO="${NATIVE_BUILD_DIR}/libfast_cache.so"

fast_slot_count=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)")
native_jit_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET)")
read -r chain_dispatch_offset chain_dispatch_bytes common_epilogue_offset header_bytes chain_target_offset <<< "${native_jit_config}"

echo ">>> Compiling trace_compiler.cxx -> libtrace_compiler.so (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET="${chain_dispatch_offset}" \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES="${chain_dispatch_bytes}" \
    -DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET="${common_epilogue_offset}" \
    -DFB_CONF_JIT_X64_TRACE_HEADER_BYTES="${header_bytes}" \
    -DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET="${chain_target_offset}" \
    "${SOURCE_CPP}" -o "${GENERATED_SO}"

cp "${GENERATED_SO}" "${script_dir}/libtrace_compiler.so"
echo "Built libtrace_compiler.so"

echo ">>> Compiling fast_cache.cxx -> libfast_cache.so (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC \
    -DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT="${fast_slot_count}" \
    "${CACHE_SOURCE_CPP}" -o "${CACHE_GENERATED_SO}"

cp "${CACHE_GENERATED_SO}" "${script_dir}/libfast_cache.so"
echo "Built libfast_cache.so with ${fast_slot_count} slots"
