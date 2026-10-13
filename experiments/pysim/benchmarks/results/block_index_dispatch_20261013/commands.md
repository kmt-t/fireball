# 再実行コマンドと結果の識別

作業ディレクトリはリポジトリルート。Pythonは`.venv/bin/python`、wheel/cache書込み先は`/tmp/fireball-uv-cache`を使った。

## Release build

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
```

両build入口がClang 21.1.8、C++23、O3、NDEBUG、ThinLTO、lldを使う。AO-Bench workerはCPU 2へ`taskset -c 2`で固定された。JIT/Interpreterのyieldしきい値は4096。計時コマンドは次のとおり。

```bash
.venv/bin/python experiments/pysim/benchmarks/aobench/compare_wamr.py \
  --wamr-build /tmp/fireball-wamr-fast-o3 \
  --output experiments/pysim/benchmarks/results/block_index_dispatch_20261013/aobench.json \
  --cpu 2 --trials 3
```

runnerは5解像度、3独立プロセス、各3 warmup・7 timed callsでInterpreter、JIT、WAMRを実行する。全15組のframe SHA-256が一致し、サイズごとに3 engine間でhashが一致することを確認した。計時対象は`NativeInterpreter.call()`または`wasm_runtime_call_wasm()`であり、WASM load、runtime setup、hash計算は区間外である。

## Loader arenaとABI構造体サイズ

```bash
cd experiments/pysim
PYTHONPATH=.:tier1_core:tier1_interface:tier3_plugins:tier3_platform:qa/private \
  .venv/bin/python benchmarks/results/block_index_dispatch_20261013/measure_loader_memory.py
```

このスクリプトはAO-Bench WASMをLoaderだけでparseし、関数ごとの注釈プール、BasicBlock配列、Loader arena、Interpreter ABI viewのサイズを測る。変更前のStep 0値は`interpreter_contracts_v3_20261012/pool_step0.json`から読む。

## 関連テストとモデル

```bash
cd experiments/pysim
.venv/bin/python -m pytest -x -q \
  qa/tier2_runtime/interpreter/test_interpreter.py \
  qa/tier2_runtime/interpreter/test_cps_interpreter.py \
  qa/tier2_runtime/test_wasm_reader.py \
  qa/tier2_runtime/test_interpreter_abi.py \
  qa/tier3_plugins/jit/test_jit_runtime.py \
  qa/workloads/test_wasm_core_spec.py
```

結果は316 passed。WASM trapとJIT chain successorを含む。Interpreter/JIT形式モデルの通常検証と`guards=False`変異検査、C++静的チェック、formalソース静的チェックも別途実行し、合格した。`check-src -g pysim`は内部から全PySIM QAを起動し60秒でtimeoutとなったため、関連pytestをtools/README.mdの方針で直接実行した。

形式モデルはリポジトリルートから次のコマンドで個別にも再実行できる。

```bash
.venv/bin/python docs/components/tier2_runtime/formal/interpreter_control_flow_model.py
.venv/bin/python docs/components/tier3_plugins/formal/jit_cache_model.py
```

Interpreter modelは`guards=True`と`guards=False`をpassした。JIT cache modelは13 propertyの通常検証と変異検査をpassし、index lookupのsuccessor PC照合性質も期待通り変異でrefuteされた。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash tools/check-doc.sh \
  -r /tmp/fireball-block-index-doc-check.md \
  docs/components/tier2_runtime/interpreter.md \
  docs/components/tier3_plugins/jit_runtime.md \
  docs/components/tier3_plugins/jit_compiler.md \
  docs/components/tier3_plugins/benchmarks/jit_runtime_bench_spec.md \
  docs/qa/tier2_runtime/interpreter_test_spec.md \
  docs/qa/tier3_plugins/jit_runtime_test_spec.md
git diff --check
```

`check-doc.sh`はFormat、Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopicを通過した。TraceabilityとObligationで283 errors（258と25）、16 warningsとなり、品質ゲートは未通過である。グローバル文書グラフにある未定義キーワードと未充足の`VERIFY_LLM`義務を含む全出力を[`doc_check_report.md`](doc_check_report.md)に保存した。

## SHA-256

| ファイル | SHA-256 |
| :--- | :--- |
| `aobench.json` | `3d2e1a12614b9ace643007a8bd45581347423a75816f8df84e794827d627cb25` |
| `memory_impact.json` | `20239b8265bab297db82bbfd92aa716f838035440cd4c1611b724cf22e054765` |
| `measure_loader_memory.py` | `073d6fb7932aa2689623a35a1006d270688d8d9b5dcbee05ea38fab0eb7b1542` |
| `doc_check_report.md` | [`SHA256SUMS`](SHA256SUMS)を参照 |
| NativeInterpreter shared library | `f778fcaeabcf03eea632333837620605af3d100244028457ed2da19998877251` |
| JIT shared library | `b590c0b5cfdf68187c586d139eb1ee6a951e4507359e3086a666c02226beae54` |
| WAMR Fast shared library | `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960` |

ソース別SHA-256とWAMRのconfigure/compile/link optionsは`aobench.json`に記録する。
