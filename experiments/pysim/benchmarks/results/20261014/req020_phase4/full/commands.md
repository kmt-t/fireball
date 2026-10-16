# REQ-020 最新測定コマンド

測定日は2026-10-14。ベースHEADは`37253e695d62bd9d4d9c6ab54eb31f81defe1ec8`で、ベンチマーク対象のworktreeには変更があった。出力先はこのディレクトリである。個々の測定JSONには環境・source hash・library hashを記録した。

## 検証

```bash
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python experiments/pysim/qa/run_all.py
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python experiments/pysim/qa/run_all.py --optimized
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python experiments/pysim/qa/integration/run_all.py
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python experiments/pysim/qa/workloads/run_all.py
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/pyright --project pyrightconfig.json
PATH="$PWD/.venv/bin:$PATH" UV_CACHE_DIR=/tmp/uv-cache ./tools/check-src.sh -g all
UV_CACHE_DIR=/tmp/uv-cache ./tools/check-src.sh -g formal
UV_CACHE_DIR=/tmp/uv-cache ./tools/check-doc.sh --report /tmp/req020-check-doc-final2.md
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --project tools/spec-integrator --extra dev --extra prose python -m pytest tools/spec-integrator/tests -q
cmake -S . -B build/codex-ci -G Ninja -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DFIREBALL_BUILD_UVWASI=ON
cmake --build build/codex-ci --target fireball uvwasi
build/codex-ci/fireball
```

GDB remote統合試験を含むQAはloopback socketを使う。

## Release測定

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
  --output experiments/pysim/benchmarks/results/20261014/req020_phase4/full/aobench_wamr.json
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/results/latest/validation/bench_aobench_32.py \
  "$PWD" experiments/pysim/benchmarks/results/20261014/req020_phase4/full/aobench_32x16_release.json
```

0xFC計測は同じrelease interpreterで独立プロセス3回を実行した。

```bash
for trial in 1 2 3; do
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_fc.py \
    > "experiments/pysim/benchmarks/results/20261014/req020_phase4/full/fc_runs/fc_run${trial}.json"
done
```

## 通常build測定

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier1_core/printk/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
for trial in 1 2 3; do
  taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    > "experiments/pysim/benchmarks/results/20261014/req020_phase4/full/call_dispatch_runs/run${trial}.json"
done
for trial in 1 2 3; do
  taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/run_all.py \
    > "experiments/pysim/benchmarks/results/20261014/req020_phase4/full/unified_runs/unified_run${trial}.log"
done
taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/memory/bench_runtime_memory.py \
  --output experiments/pysim/benchmarks/results/20261014/req020_phase4/full/runtime_memory.json
taskset -c 3 .venv/bin/python experiments/pysim/benchmarks/memory/bench_resource_inventory.py \
  --output experiments/pysim/benchmarks/results/20261014/req020_phase4/full/resource_inventory.json
taskset -c 3 .venv/bin/python tools/check_source_size.py \
  --json experiments/pysim/benchmarks/results/20261014/req020_phase4/full/sloc.json
UV_CACHE_DIR=/tmp/uv-cache taskset -c 3 .venv/bin/python \
  experiments/pysim/benchmarks/results/20261014/req020_phase4/measure_chain_reasons.py all \
  --output experiments/pysim/benchmarks/results/20261014/req020_phase4/chain_reasons_after.json
```

REQ-054でスケジューラ/JITの処理件数APIを廃止したため、旧APIに依存するyield応答の変更前後測定コマンドと数値は現行条件として扱わず、保存対象から除いた。

## LLM audit

```bash
UV_CACHE_DIR=/tmp/uv-cache ./tools/llm-judge.sh \
  --max-documents 20 --max-keyword-groups 20 \
  --backend clef-flash --model fireball-tev1-64k:latest \
  > /tmp/req020-llm-judge-final.log 2>&1
```

## 実行結果

2026-10-14に記載コマンドを実行した。完了した各コマンドの終了コードは0である。

| 対象 | 結果 | 範囲 |
| :--- | :--- | :--- |
| PySIM unit QA | PASS | 通常30/30、optimized 16 passed・9 deselected |
| Integration QA | PASS | 12/12 tests、5/5 groups |
| Workload QA | PASS | 38 passed、WASM Core Spec 37/37 files・15,230 assertions、3/3 workload suites |
| Pyright | PASS | 0 errors・warnings・information |
| Source gate | PASS | 26 specs、15 concepts、23 formal models、25 test specs、12 scenarios、4 integration groups、3 WASM workloads、0 warnings |
| Formal source recheck | PASS | 26 files、0 warnings、`guards=False` mutation rule satisfied |
| spec-integrator tests | PASS | 172 passed、2 import deprecation warnings |
| CMake native build・boot | PASS | `fireball`・`uvwasi` build、`build/codex-ci/fireball` 起動 |
| Full benchmark suite | PASS | unified・AO/WAMR・0xFC・call dispatch・runtime memory・resource inventory・SLOC・chain diagnostics |
| Ollama LLM judge | PASS | `fireball-tev1-64k:latest`、20/20 documents・671 sections（11 PASS・660 WARN・0 FAIL）、20/20 keyword groups・94 reference pairs（2 PASS・92 WARN・0 FAIL）、confirmed violation 0 |
| Document quality gate | PASS | exit 0、0 errors・36 warnings（16 prose・20 obligations）、verification obligations 4/4 discharged |
