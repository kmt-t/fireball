#!/usr/bin/env bash
# Builds the standalone native x64 Copy-and-Patch compiler and cache (Linux/WSL).
# Requires Clang 17+ on PATH.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
NATIVE_BUILD_DIR="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${NATIVE_BUILD_DIR}"
native_source_dir="${script_dir}"
python_package_dir="${project_root}/experiments/pysim/tier3_plugins/jit"
SOURCE_CPP="${native_source_dir}/trace_compiler.cxx"
COMMON_SOURCE_CPP="${native_source_dir}/common_code.cxx"
MEMORY_SOURCE_CPP="${native_source_dir}/executable_memory.cxx"
GENERATED_SO="${NATIVE_BUILD_DIR}/libtrace_compiler.so"
RUNTIME_SOURCE_CPP="${native_source_dir}/jit_runtime.cxx"

fast_slot_count=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)")
cache_runtime_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_CACHE_BANK_ENTRY_CAPACITY, JIT_CACHE_MAX_INBOUND_SOURCES; print(JIT_CACHE_BANK_ENTRY_CAPACITY, JIT_CACHE_MAX_INBOUND_SOURCES)")
read -r bank_entries max_inbound <<< "${cache_runtime_config}"

native_jit_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_TRACE_ENTRY_STUB_BYTES; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_TRACE_ENTRY_STUB_BYTES)")
read -r chain_dispatch_offset chain_dispatch_bytes common_epilogue_offset header_bytes chain_target_offset entry_stub_bytes <<< "${native_jit_config}"

common_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    'import config; names=("JIT_CACHE_REGION_BYTES", "JIT_CACHE_COMMON_CODE_BYTES", "JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES", "JIT_TRACE_COMMON_PROLOGUE_OFFSET", "JIT_TRACE_COMMON_HELPER_OFFSET", "JIT_TRACE_HELPER_ENTRY_BYTES", "JIT_TRACE_TYPED_I32_HELPER_COUNT", "JIT_TRACE_TYPED_I32_HELPER_OFFSET", "JIT_TRACE_WIDE_HELPER_COUNT", "JIT_TRACE_WIDE_HELPER_OFFSET", "JIT_X64_HELPER_TARGET_OFFSET", "JIT_X64_TRACE_ENTRY_STUB_BYTES", "JIT_HISTORY_CAPACITY", "JIT_COMPILE_QUEUE_CAPACITY"); print(" ".join("-DFB_CONF_"+name+"="+str(getattr(config,name)) for name in names))')
read -r -a common_definitions <<< "${common_config}"

echo ">>> Compiling Tier 3 JIT (clang++)"
clang++ -std=c++23 -O2 -g -Wall -Wextra -Wpedantic -shared -fPIC -fvisibility=hidden \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET="${chain_dispatch_offset}" \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES="${chain_dispatch_bytes}" \
    -DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET="${common_epilogue_offset}" \
    -DFB_CONF_JIT_X64_TRACE_HEADER_BYTES="${header_bytes}" \
    -DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET="${chain_target_offset}" \
    -DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT="${fast_slot_count}" \
    -DFB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY="${bank_entries}" \
    -DFB_CONF_JIT_CACHE_MAX_INBOUND_SOURCES="${max_inbound}" \
    "${common_definitions[@]}" -fno-exceptions -fno-rtti \
    "${SOURCE_CPP}" "${COMMON_SOURCE_CPP}" "${MEMORY_SOURCE_CPP}" \
    "${RUNTIME_SOURCE_CPP}" "${native_source_dir}/profiling.cxx" -o "${GENERATED_SO}"
cp "${GENERATED_SO}" "${python_package_dir}/libtrace_compiler.so"
echo "Built Tier 3 JIT libtrace_compiler.so"
