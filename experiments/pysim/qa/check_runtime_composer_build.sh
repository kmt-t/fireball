#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../../.." && pwd)"
compiler="${CXX:-clang++}"
build_dir="${TMPDIR:-/tmp}/fireball-runtime-composer"
mkdir -p "${build_dir}"

"${compiler}" -std=c++23 -O2 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -I"${project_root}/experiments/pysim/tier2_runtime" \
  "${script_dir}/runtime_composer_build_probe.cxx" \
  -o "${build_dir}/coexisting_compositions"
ASAN_OPTIONS=detect_leaks=0 "${build_dir}/coexisting_compositions"

echo "Verified that interpreter-only and JIT runtime compositions coexist."
