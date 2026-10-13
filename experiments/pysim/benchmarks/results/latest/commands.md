# 最新ベンチマークの再実行コマンド

リポジトリのルートで実行する。依存環境とWASI-SDK、公式Core選択セットは配置済みとする。
通常ビルドとreleaseビルドの結果を別ファイルへ保存する。

## WAMRの固定構成

WAMRのrevisionは`aobench_wamr.json`に記録した固定commitと一致させる。

```bash
cmake -S third_party/wasm-micro-runtime/product-mini/platforms/linux -B build/wamr-benchmark \
  -DCMAKE_C_COMPILER=clang -DCMAKE_BUILD_TYPE=Release \
  '-DCMAKE_C_FLAGS_RELEASE=-O3 -DNDEBUG -flto=thin' \
  '-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld' '-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld' \
  -DWAMR_BUILD_INTERP=1 -DWAMR_BUILD_FAST_INTERP=1 \
  -DWAMR_BUILD_AOT=0 -DWAMR_BUILD_JIT=0 -DWAMR_BUILD_FAST_JIT=0 \
  -DWAMR_BUILD_LIBC_WASI=1 -DWAMR_BUILD_LIBC_BUILTIN=0 \
  -DWAMR_BUILD_SIMD=0 -DWAMR_BUILD_REF_TYPES=0 -DBUILD_SHARED_LIBS=ON
cmake --build build/wamr-benchmark -j 4
```

## Releaseの計測

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_fc.py > /tmp/fc.json
.venv/bin/python experiments/pysim/benchmarks/aobench/compare_wamr.py \
  --wamr-build build/wamr-benchmark --cpu 2 --trials 3 --output /tmp/aobench_wamr.json
```

## 通常ビルドへの復元と計測

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier1_core/printk/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
.venv/bin/python experiments/pysim/benchmarks/run_all.py > /tmp/unified.log
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py > /tmp/call_dispatch.json
.venv/bin/python experiments/pysim/benchmarks/memory/bench_runtime_memory.py --output /tmp/runtime_memory.json
.venv/bin/python experiments/pysim/benchmarks/memory/bench_resource_inventory.py --output /tmp/resource_inventory.json
```

## フル検証

```bash
.venv/bin/python experiments/pysim/qa/run_all.py
.venv/bin/python experiments/pysim/qa/integration/run_all.py
.venv/bin/python experiments/pysim/qa/workloads/run_all.py
.venv/bin/pyright --project pyrightconfig.json
PATH="$PWD/.venv/bin:$PATH" UV_CACHE_DIR=/tmp/uv-cache ./tools/check-src.sh -g all
UV_CACHE_DIR=/tmp/uv-cache ./tools/check-doc.sh
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --project tools/spec-integrator --extra dev --extra prose \
  python -m pytest tools/spec-integrator/tests -q
cmake -S . -B build/codex-ci -G Ninja -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ -DFIREBALL_BUILD_UVWASI=ON
cmake --build build/codex-ci --target fireball uvwasi
build/codex-ci/fireball
```

コンパイル時のassert無効化に対するcopy/fill回帰テストは単体スイートに含む。
計測ファイルを読んだ時点のsource・library hashは`metadata.json`へ記録する。
