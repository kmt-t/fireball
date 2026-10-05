#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../../../../.." && pwd)"
native_build_dir="${TMPDIR:-/tmp}/fireball-pysim-native"
mkdir -p "${native_build_dir}"
clang++ -std=c++23 -O2 -Wall -Wextra -Wpedantic -shared -fPIC -fvisibility=hidden \
  -fno-exceptions -fno-rtti "${script_dir}/printk.cxx" -o "${native_build_dir}/libprintk.so"
cp "${native_build_dir}/libprintk.so" "${project_root}/experiments/pysim/tier1_core/libprintk.so"
