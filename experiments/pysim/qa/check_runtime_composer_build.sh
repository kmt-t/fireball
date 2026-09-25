#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../../.." && pwd)"
compiler="${CXX:-clang++}"
build_dir="${TMPDIR:-/tmp}/fireball-runtime-composer"
mkdir -p "${build_dir}"

"${compiler}" -std=c++23 -O2 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -DFB_CONF_JIT_ENABLED=0 \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  -o "${build_dir}/interpreter_only"
ASAN_OPTIONS=detect_leaks=0 "${build_dir}/interpreter_only"

"${compiler}" -std=c++23 -DFB_CONF_JIT_ENABLED=0 -E -P \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  >"${build_dir}/interpreter_only.ii"
if rg -n "jit_lookup_result|jit_lookup_policy|jit_runtime_executor" \
  "${build_dir}/interpreter_only.ii"; then
  echo "JIT lookup declarations survived the interpreter-only build configuration."
  exit 1
fi

"${compiler}" -std=c++23 -O2 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -DFB_CONF_JIT_ENABLED=1 \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  -o "${build_dir}/jit_enabled"
ASAN_OPTIONS=detect_leaks=0 "${build_dir}/jit_enabled"

"${compiler}" -std=c++23 -DFB_CONF_JIT_ENABLED=1 -E -P \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  >"${build_dir}/jit_enabled.ii"
rg -q "concept jit_lookup_policy" "${build_dir}/jit_enabled.ii"
rg -q "runtime.call\(interpreter, lookup" "${build_dir}/jit_enabled.ii"

if "${compiler}" -std=c++23 -DFB_CONF_JIT_ENABLED=2 -fsyntax-only \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  >"${build_dir}/invalid_config.log" 2>&1; then
  echo "Invalid JIT configuration value unexpectedly compiled."
  exit 1
fi
rg -q "must be defined as 0 or 1" "${build_dir}/invalid_config.log"

echo "Verified interpreter-only removal and JIT-enabled runtime composition."
