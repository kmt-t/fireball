# REQ-017 再計測コマンド（2026-10-14）

対象 HEAD: `506d1213e2d3045fb2b7ccec9016ed2e0d1c0849`。測定前の worktree は clean。
AO / WAMR / 0xFC は release、呼出し・統合・メモリは通常ビルド。AO と呼出しは CPU 2、統合・vMMIO・資源計測は CPU 3へ固定した。

## Release

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
cmake -S third_party/wasm-micro-runtime/product-mini/platforms/linux -B build/wamr-benchmark \
  -DCMAKE_C_COMPILER=clang -DCMAKE_BUILD_TYPE=Release \
  '-DCMAKE_C_FLAGS_RELEASE=-O3 -DNDEBUG -flto=thin' \
  '-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld' '-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld' \
  -DWAMR_BUILD_INTERP=1 -DWAMR_BUILD_FAST_INTERP=1 -DWAMR_BUILD_AOT=0 \
  -DWAMR_BUILD_JIT=0 -DWAMR_BUILD_FAST_JIT=0 -DWAMR_BUILD_LIBC_WASI=1 \
  -DWAMR_BUILD_LIBC_BUILTIN=0 -DWAMR_BUILD_SIMD=0 -DWAMR_BUILD_REF_TYPES=0 -DBUILD_SHARED_LIBS=ON
cmake --build build/wamr-benchmark -j 4
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/aobench/compare_wamr.py \
  --wamr-build build/wamr-benchmark --cpu 2 --trials 3 \
  --output experiments/pysim/benchmarks/results/latest/aobench_wamr.json
```

0xFCはrelease buildで次を3回実行し、`fc_runs/fc_run1.json`から`fc_run3.json`へ保存した。

```bash
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_fc.py
```

既存AO/WAMR比較スクリプトの5解像度は64×36から320×180。指定下限の32×16は同じ3プロセス・各3 warmup・7 timingで追加計測した。
追加測定runnerは[`validation/bench_aobench_32.py`](experiments/pysim/benchmarks/results/latest/validation/bench_aobench_32.py)に保存した。

## 通常ビルド

```bash
bash experiments/pysim/native/tier1_core/printk/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py
taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/run_all.py
taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/memory/bench_runtime_memory.py \
  --output experiments/pysim/benchmarks/results/latest/runtime_memory.json
taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/memory/bench_resource_inventory.py \
  --output experiments/pysim/benchmarks/results/latest/resource_inventory.json
taskset -c 3 .venv/bin/python tools/check_source_size.py \
  --json experiments/pysim/benchmarks/results/latest/sloc.json
```

`run_all.py`は独立プロセスで3回実行し、各出力を`unified.log`へまとめる。

native stackはresource inventoryが一時ディレクトリで `-fstack-usage` を使って採取し、公式共有ライブラリを置き換えない。全試行と条件は同階層の結果JSONに保存した。

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
