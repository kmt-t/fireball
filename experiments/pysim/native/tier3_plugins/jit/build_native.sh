#!/usr/bin/env bash
# Builds the standalone native x64 JIT compiler and cache (Linux/WSL).
# Requires Clang 17+ and matching object inspection utilities at build time.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}"
project_root="$(cd "${script_dir}/../../../../.." && pwd)"
uv_python="${project_root}/.venv/bin/python"
qa_build=0
block_counters=0
release_define=()
for option in "$@"; do
    case "${option}" in
        --qa) qa_build=1 ;;
        --block-counters) block_counters=1 ;;
        --release) release_define+=("-DNDEBUG") ;;
        *) echo "Unknown option: ${option}" >&2; exit 2 ;;
    esac
done
NATIVE_BUILD_DIR="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${NATIVE_BUILD_DIR}"
stencil_output_dir="${NATIVE_BUILD_DIR}/jit-stencils"
native_source_dir="${script_dir}"
python_package_dir="${project_root}/experiments/pysim/tier3_plugins/jit"
SOURCE_CPP="${FIREBALL_JIT_TRACE_SOURCE:-${native_source_dir}/trace_compiler.cxx}"
COMMON_SOURCE_CPP="${native_source_dir}/common_code.cxx"
MEMORY_SOURCE_CPP="${native_source_dir}/executable_memory.cxx"
GENERATED_SO="${NATIVE_BUILD_DIR}/libtrace_compiler.so"
RUNTIME_SOURCE_CPP="${native_source_dir}/jit_runtime.cxx"
runtime_sources=("${RUNTIME_SOURCE_CPP}")
measurement_flags=()
qa_export_map="${project_root}/experiments/pysim/qa/private/jit_probe.exports"
visibility_flags=(-fvisibility=hidden)
linker_flags=()

cache_runtime_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_CACHE_BANK_ENTRY_CAPACITY; print(JIT_CACHE_BANK_ENTRY_CAPACITY)")
read -r bank_entries <<< "${cache_runtime_config}"

native_jit_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_COMMON_CODE_RELATIVE_OFFSET, JIT_X64_TRACE_CHAIN_ENTRY_OFFSET; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_COMMON_CODE_RELATIVE_OFFSET, JIT_X64_TRACE_CHAIN_ENTRY_OFFSET)")
read -r chain_dispatch_offset chain_dispatch_bytes header_bytes chain_target_offset common_relative_offset chain_entry_offset <<< "${native_jit_config}"

common_config=$(PYTHONPATH="${project_root}/experiments/pysim/tier1_core" \
    uv run --offline --no-sync --python "${uv_python}" python -c \
    'import config; names=("JIT_CACHE_REGION_BYTES", "JIT_CACHE_COMMON_CODE_BYTES", "JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET", "JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES", "JIT_HISTORY_CAPACITY", "JIT_COMPILE_QUEUE_CAPACITY", "JIT_CARD_SHIFT", "JIT_CACHE_BANK_CAPACITY_BYTES", "JIT_CACHE_ACTIVE_OFFSET_BYTES", "JIT_CACHE_WARM_OFFSET_BYTES", "JIT_CACHE_OLDEST_OFFSET_BYTES", "FB_CONF_JIT_AGING_STEP_UNITS", "FB_CONF_JIT_AGING_STEP_SCAN_BYTES"); print(" ".join("-D"+(name if name.startswith("FB_CONF_") else "FB_CONF_"+name)+"="+str(getattr(config,name)) for name in names))')
read -r -a common_definitions <<< "${common_config}"

if [[ "${qa_build}" == "1" ]]; then
    runtime_sources+=("${project_root}/experiments/pysim/qa/private/native_jit_probe.cxx")
    measurement_flags=(-include "${project_root}/experiments/pysim/qa/private/jit_measurements.hxx")
    GENERATED_SO="${NATIVE_BUILD_DIR}/libjit_probe.so"
    python_package_dir="${project_root}/experiments/pysim/qa/private"
    visibility_flags=(-fvisibility=default)
    linker_flags+=("-Wl,--version-script=${qa_export_map}")
fi
if [[ "${block_counters}" == "1" ]]; then
    common_definitions+=("-DFB_CONF_JIT_BLOCK_COUNTERS=1")
fi

echo ">>> Assembling x64 JIT trace stencils"
"${uv_python}" "${project_root}/tools/jit_stencil_gen/generate_assembly.py" \
    "${native_source_dir}/lir/x64/trace.S" \
    --role trace \
    --output-dir "${stencil_output_dir}" \
    --header stencils.hxx --binary stencils.bin --namespace generated \
    --assembly-output "${stencil_output_dir}/trace_stencils.preprocessed.S"
echo ">>> Assembling optimized x64 operation stencils"
"${uv_python}" "${project_root}/tools/jit_stencil_gen/generate_assembly.py" \
    "${native_source_dir}/lir/x64/operations.S" \
    --role operations \
    --output-dir "${stencil_output_dir}" \
    --header asm_stencils.hxx --binary asm_stencils.bin \
    --namespace asm_generated \
    --assembly-output "${stencil_output_dir}/operations.preprocessed.S"
echo ">>> Assembling shared x64 JIT code area"
"${uv_python}" "${project_root}/tools/jit_stencil_gen/generate_assembly.py" \
    "${native_source_dir}/lir/x64/common.S" \
    --role common \
    --output-dir "${stencil_output_dir}" \
    --header common_code.hxx --binary common_code.bin --namespace generated \
    --assembly-output "${stencil_output_dir}/common_code.preprocessed.S"

echo ">>> Compiling Tier 3 JIT"
clang++ -std=c++23 -O3 -flto=thin -fuse-ld=lld -fomit-frame-pointer -g -Wall -Wextra -Wpedantic -shared -fPIC \
    "${visibility_flags[@]}" \
    "${release_define[@]}" \
    "${measurement_flags[@]}" \
    -I "${native_source_dir}" \
    -I "${stencil_output_dir}" \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET="${chain_dispatch_offset}" \
    -DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES="${chain_dispatch_bytes}" \
    -DFB_CONF_JIT_X64_TRACE_HEADER_BYTES="${header_bytes}" \
    -DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET="${chain_target_offset}" \
    -DFB_CONF_JIT_X64_TRACE_CHAIN_ENTRY_OFFSET="${chain_entry_offset}" \
    -DFB_CONF_JIT_X64_COMMON_CODE_RELATIVE_OFFSET="${common_relative_offset}" \
    -DFB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY="${bank_entries}" \
    "${common_definitions[@]}" -fno-exceptions -fno-rtti \
    "${SOURCE_CPP}" \
    "${COMMON_SOURCE_CPP}" "${MEMORY_SOURCE_CPP}" \
    "${native_source_dir}/jit_runtime_apis.cxx" \
    "${native_source_dir}/../../tier2_runtime/interpreter/native_wasm_operations.cxx" \
    "${runtime_sources[@]}" "${native_source_dir}/profiling.cxx" \
    "${linker_flags[@]}" -o "${GENERATED_SO}"
cp "${GENERATED_SO}" "${python_package_dir}/$(basename "${GENERATED_SO}")"
if [[ "${qa_build}" == "1" ]]; then
    echo "Built QA Tier 3 JIT libjit_probe.so"
else
    echo "Built Tier 3 JIT libtrace_compiler.so"
fi
