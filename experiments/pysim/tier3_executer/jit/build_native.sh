#!/usr/bin/env bash
# Builds the required native x64 Copy-and-Patch compiler extension (Linux/WSL).
# Requires Clang 17+ on PATH.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
NATIVE_BUILD_DIR="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${NATIVE_BUILD_DIR}"
SOURCE_CPP="${script_dir}/native_trace_call.cxx"
GENERATED_SO="${NATIVE_BUILD_DIR}/native_trace_call.so"
CACHE_SOURCE_CPP="${script_dir}/native_fast_cache.cxx"
CACHE_GENERATED_SO="${NATIVE_BUILD_DIR}/_jit_cache_native.so"

PY_INC=$(uv run --offline --no-sync --python "${uv_python}" python -c "import sysconfig; print(sysconfig.get_path('include'))")
PY_LDLIB=$(uv run --offline --no-sync --python "${uv_python}" python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")
fast_slot_count=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)")
native_jit_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET)")
read -r chain_dispatch_offset chain_dispatch_bytes common_epilogue_offset header_bytes chain_target_offset <<< "${native_jit_config}"

echo ">>> Compiling native_trace_call.cxx -> .so (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET="${chain_dispatch_offset}" \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES="${chain_dispatch_bytes}" \
    -DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET="${common_epilogue_offset}" \
    -DFB_CONF_JIT_X64_TRACE_HEADER_BYTES="${header_bytes}" \
    -DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET="${chain_target_offset}" \
    -I"${PY_INC}" \
    "${SOURCE_CPP}" -o "${GENERATED_SO}" \
    ${PY_LDLIB:+-L"${PY_LDLIB}"}

cp "${GENERATED_SO}" native_trace_call.so
echo "Built native_trace_call.so"

echo ">>> Compiling native_fast_cache.cxx -> .so (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC \
    -DFB_CONF_JIT_ENABLED=1 \
    -DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT="${fast_slot_count}" \
    -I"${PY_INC}" \
    "${CACHE_SOURCE_CPP}" -o "${CACHE_GENERATED_SO}" \
    ${PY_LDLIB:+-L"${PY_LDLIB}"}

cp "${CACHE_GENERATED_SO}" _jit_cache_native.so
echo "Built _jit_cache_native.so with ${fast_slot_count} slots"
