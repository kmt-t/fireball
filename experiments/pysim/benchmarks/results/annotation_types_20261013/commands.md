# 再実行コマンド

作業ディレクトリはrepository root `/home/t-matsu/workspace/mysrc/fireball`。Python commandsはrepositoryの`.venv`を使い、キャッシュを`/tmp/fireball-uv-cache`へ指定した。

## Loader sizeとAO-Bench

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/results/annotation_types_20261013/measure_loader_sizes.py --output experiments/pysim/benchmarks/results/annotation_types_20261013/loader_memory_impact.json
```

計測用release buildは両方ともClang 21.1.8、C++23、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`。counter用defineなしでbuildした。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/compare_wamr.py --wamr-build /tmp/fireball-wamr-fast-o3 --output experiments/pysim/benchmarks/results/annotation_types_20261013/aobench.json --cpu 2 --trials 3
```

`compare_wamr.py`は各engine、解像度、独立processごとに3 warmup、7 timed callsを実行する。CPU affinityは`taskset -c 2`、yield thresholdは4096。engine間のframe SHA-256とWASM SHA-256が一致した場合にJSONを書き出す。実行結果はpass。

最終sourceでの再計測は2026-10-13 08:00 JSTに完了した。`aobench.json`の`source_sha256`と`library_sha256`がこのrunのbuild識別子である。5解像度すべてで各engine 3 process（合計9 row）を確認し、frame/WASM hashは全engine・process間で一致した。

同じ計測build libraryを使った予備runは[`aobench_initial.json`](aobench_initial.json)にある。これはsection-count parameter prevalidationを最後に集約する前のrunであり、最終source識別子は[`aobench.json`](aobench.json)を使う。

## QA buildと関連pytest

QA用Interpreter/JIT native librariesは診断カウンタを有効にしたbuildで作り、計時結果には使っていない。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa --block-counters
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
```

ABIのassert負例を実行するため、次の単体assert有効buildを先に作成した。release buildのままassert負例testを実行すると`RUN_DISPATCH(None, ...)`が`SIGSEGV`となるため、この負例だけはassert有効buildで判定する。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
```

Interpreter、loader、CPS、ABI（上限ちょうど・超過、変数幅、ABI layoutを含む）:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync pytest -q experiments/pysim/qa/tier2_runtime/test_wasm_reader.py experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py experiments/pysim/qa/tier2_runtime/test_interpreter_abi.py
```

release libraryでの初回実行ではassert負例1件がSIGSEGVとなった。assert有効buildを作って同じcommandを再実行した結果は163 passed。

JIT runtime、実行メモリ、差分テスト:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py experiments/pysim/qa/tier3_plugins/jit/test_exec_memory.py experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py -k 'not jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit'
```

結果: 133 passed、1 deselected。除外した長時間testを個別に300秒上限で実行した。

```bash
timeout 300s env UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync pytest -s -vv experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py::test_jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit
```

結果: 300秒でtimeout（exit 124）。

WASM Core Spec trap tests:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync pytest -q experiments/pysim/qa/workloads/test_wasm_core_spec.py -k trap
```

結果: 2 passed、36 deselected.

## Formal、format、static checks

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python docs/components/tier2_runtime/formal/interpreter_stack_model.py
```

通常propertyと`guards=False` mutation propertyが全件pass。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-src.sh -g cpp experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.hxx experiments/pysim/native/tier3_plugins/jit/trace_compiler.cxx
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-src.sh -g cpp experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-src.sh -g cpp experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-src.sh -g pysim experiments/pysim/tier2_runtime/interpreter/control_flow.py experiments/pysim/tier2_runtime/wasm/module.py experiments/pysim/tier2_runtime/wasm/reader.py experiments/pysim/qa/tier2_runtime/test_wasm_reader.py experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/format-doc.sh docs/architecture/resource_budget_estimation.md docs/components/tier2_runtime/interpreter.md docs/components/tier2_runtime/jit_abi.md docs/components/tier2_runtime/runtime_loader.md docs/qa/tier2_runtime/interpreter_test_spec.md
```

5 formatter invocations pass。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-src.sh -g cpp experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.hxx experiments/pysim/native/tier3_plugins/jit/trace_compiler.cxx experiments/pysim/native/tier3_plugins/jit/jit_runtime.cxx
```

結果: pass、0 warning。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-src.sh -g pysim experiments/pysim/tier2_runtime/interpreter/control_flow.py experiments/pysim/tier2_runtime/wasm/module.py experiments/pysim/tier2_runtime/wasm/reader.py experiments/pysim/qa/tier2_runtime/test_wasm_reader.py experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py
```

対象fileの静的規約検査はpassしたが、検査pipelineが起動する全体`qa/run_all.py`は60秒でtimeoutした。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-doc.sh docs/architecture/resource_budget_estimation.md docs/components/tier2_runtime/interpreter.md docs/components/tier2_runtime/jit_abi.md docs/components/tier2_runtime/runtime_loader.md docs/qa/tier2_runtime/interpreter_test_spec.md
git diff --check
```

`git diff --check`はpass。`check-doc`はFormat/linkを含む構造系gateは通り、repository全体のtraceabilityとobligation gateが残って229 errors、14 warningsでfailした。既存の未定義keywordと`VERIFY_LLM` obligationが主因である。

計測後にInterpreterとJITのrelease libraryを再buildし、QA/assert有効libraryではなくrelease artifactを作業ツリーに戻した。再build command:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
```
