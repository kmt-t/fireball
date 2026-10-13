# 再実行コマンド

作業ディレクトリはrepository rootである。計時と診断は別のnative buildを使った。release timing libraryにはdiagnostic counterを入れていない。

## Release buildとAO-Bench timing

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/compare_wamr.py --wamr-build /tmp/fireball-wamr-fast-o3 --output experiments/pysim/benchmarks/results/jit_const_pool_20261013/aobench_after.json --cpu 2 --trials 3
```

`compare_wamr.py`は5解像度で、各解像度・engineごとに3独立processを起動し、3 warmupと7 timed callsを行う。CPU affinityは2、Interpreter yield thresholdは4096である。各runのframe SHA-256はInterpreter、JIT、WAMR Fast間で一致した。計時JSONのSHA-256は`71c47ea5eea9b974a4429431fe8293b3a09250a2a1bc8ff95791ee95bab49652`である。

## JIT constant-pool diagnostics

変更前後それぞれでQA JIT libraryをcounter有効でbuildし、計時libraryと分離した。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa --block-counters
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/results/jit_const_pool_20261013/measure_const_pool.py > experiments/pysim/benchmarks/results/jit_const_pool_20261013/before.json
```

変更後も同じbuildとmeasurement commandを実行し、出力を`after.json`へ保存する。before/after QA JIT library SHA-256はそれぞれ`644db79ed53a1676047a021c2b0b45272e871e5449db7a13ca05dcf579a0c286`と`23093e09c4cc463f05b0748dbc7c58fc62d4f3c722d8649b9277218b92aede89`である。

## `br_table` byte measurement

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/results/jit_const_pool_20261013/measure_br_table_sizes.py
```

このscriptはAO-Benchと`suite.wasm`をLoaderでparseし、現在のannotation pool byte数、`br_table`の数とlabel数、依頼L記載の旧形式から再構成したbyte数を出力する。`br_table_measurement.json`にはbr_table診断、`loader_sizes_after.json`にはLoaderのarenaと記述子の計測値を保存する。

## 関連テスト

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py -k 'annotation_pool or br_table or wasm_10_to_15 or wasm_runtime_traps'
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py -k 'not clang_kernel_suite'
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py -k 'indirect or call or trap or division_and_remainder'
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/workloads/test_wasm_core_spec.py -k trap
```

このturnで再実行した結果は順に23 passed、11 passed（1 deselected）、12 passed、過去の計測runで2 passed（36 deselected）である。JIT kernel suiteの個別runは次のコマンドで300秒timeoutとなった。

```bash
timeout 300s env UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -s -vv experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py::test_jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit
```

短縮した`k_dispatch(4)`比較もWasmtimeとNativeInterpreter/JITの結果が異なった。NativeInterpreter単独でJITを無効にしても同じ値となることを確認した。suite全体の出力一致は未確認とする。

## Formatとdiff check

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-doc.sh docs/components/tier2_runtime/interpreter.md docs/architecture/resource_budget_estimation.md docs/qa/tier3_plugins/jit_runtime_test_spec.md
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-src.sh -g pysim experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py experiments/pysim/benchmarks/results/jit_const_pool_20261013/measure_br_table_sizes.py
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-src.sh -g cpp experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx experiments/pysim/native/tier3_plugins/jit/trace_compiler.cxx experiments/pysim/native/tier3_plugins/jit/jit_runtime_apis.cxx
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-src.sh -g python experiments/pysim/tier2_runtime/interpreter/control_flow.py experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py experiments/pysim/benchmarks/results/jit_const_pool_20261013/measure_br_table_sizes.py
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-doc.sh docs/components/tier2_runtime/interpreter.md docs/components/tier2_runtime/jit_abi.md docs/components/tier3_plugins/jit_compiler.md docs/qa/tier2_runtime/interpreter_test_spec.md docs/qa/tier3_plugins/jit_compiler_test_spec.md docs/qa/tier3_plugins/jit_runtime_test_spec.md docs/architecture/resource_budget_estimation.md
```
