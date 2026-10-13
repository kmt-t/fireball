# 実行コマンド

作業ディレクトリはリポジトリのルートである。コマンドはこの依頼で実行した主要な入口を示す。全独立プロセスの完全な引数はStep 1の各JSONに保存した。過去のStep 1はStep 0ソースで実行した。現在の最終ソースへ過去のinstrument.pyを直接適用するとSHA検査で拒否する。

## ビルド

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --qa
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --qa --block-counters
```

Clang 21.1.8、C++23、O3、NDEBUG、ThinLTO、lldを使った。最終通常ビルドのログはbuild_final.logとbuild_jit_final.logである。QAライブラリは通常ライブラリと別のパスへ出力する。計時にQAライブラリを使っていない。

## 最終AO-Bench

```bash
.venv/bin/python experiments/pysim/benchmarks/aobench/compare_wamr.py \
  --wamr-build /tmp/fireball-wamr-fast-o3 \
  --output experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/aobench_final.json
```

入口が5解像度×3独立プロセス×3実行系を起動する。workerの引数はtaskset -c 2、--warmup 3、--repeats 7とし、Interpreter/JITは--yield-threshold 4096を使う。WAMRのrevisionと実際のcompile/link optionsはJSONにある。ソースまたはライブラリが途中で変わった実行を末尾のSHA検査で拒否する。

## Step 1の通常計時と診断

Step 0時点の独立した作業コピーで実行したコマンドは次の通りである。再実行時はsources_step0.tar.gzに保存した104ファイルを独立コピーへ展開する。archive_manifest.jsonは元のSHAと一致することを示す。実行に使ったbinaryはbinaries_step0.tar.gzにも保存した。仕様Markdown 1ファイルはarchiveに含めていないが、ビルド・計数の入力ではない。

```bash
mkdir -p /tmp/fireball-contracts-v3-step1
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/run_matrix.py --mode baseline
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/run_profiles.py
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/analyze_profiles.py
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/instrument.py install
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/run_matrix.py --mode diagnostic
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/instrument.py restore
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/summarize.py
```

profile前にInterpreterだけ-gを一時追加した。通常binaryとsymbol binaryの.text SHAは一致した。analyze_profiles.pyは/tmp/fireball-contracts-v3-step1/symbols_interpreter.soとbaseline_jit.soを使う。binary archiveをこの一時ディレクトリへ展開すれば保存済みのsymbol_mapping.jsonと照合できる。通常計時後はsymbols版から通常版へ戻し、診断後はソースとビルドスクリプトを復元した。新たなprofileの再解析には新しいuProfのDBと対応するbinaryが必要である。

実際のuProf入口は次の形式である。measure.pyはlibAMDProfileControllerでwarmup後にresume、7呼出し後にpauseする。

```bash
AMDuProfCLI collect --config hotspots --timer-interval 1 --start-paused \
  -o /tmp/fireball-contracts-v3-step1/profile_interpreter_320_1 \
  taskset -c 2 .venv/bin/python \
  experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/step1/measure.py \
  --engine interpreter --width 320 --profile
AMDuProfCLI report -i /tmp/fireball-contracts-v3-step1/profile_interpreter_320_1 \
  --inline --cutoff 0 --db sqlite
```

## 関係する回帰テスト

```bash
.venv/bin/python -m pytest -q \
  experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py \
  experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py \
  experiments/pysim/qa/tier2_runtime/test_wasm_reader.py \
  experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py \
  experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py
.venv/bin/python docs/components/tier2_runtime/formal/interpreter_stack_model.py
```

最終結果は276 passedと、形式モデル10性質の通常・変異検査の合格である。WASM trapのコードと副作用確認を含む。

## ASan/UBSan

```bash
clang++ -std=c++23 -O1 -g -fno-omit-frame-pointer -fno-exceptions -fno-rtti \
  -fvisibility=hidden -shared -fPIC -fsanitize=address,undefined \
  experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx \
  experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx \
  -o /tmp/fireball-contracts-v3/libinterpreter_sanitized.so
LD_PRELOAD=/usr/lib/llvm-21/lib/clang/21/lib/linux/libclang_rt.asan-x86_64.so \
  ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
  .venv/bin/python experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/sanitizer_driver.py
```

sanitizer_driver.pyはこのテストプロセス内のInterpreterロード先だけを一時ライブラリへ向ける。通常計時の.soは置き換えない。46 passed、100 deselectedとなった。

## 静的検査と文書ゲート

```bash
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=tools/spec-integrator/src \
  UV_CACHE_DIR=/tmp/fireball-uv-cache tools/spec-integrator/.venv/bin/python \
  experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/static_check.py
UV_CACHE_DIR=/tmp/fireball-uv-cache bash tools/check-doc.sh \
  -r experiments/pysim/benchmarks/results/interpreter_contracts_v3_20261012/doc_check_report.md \
  docs/components/tier2_runtime/interpreter.md \
  docs/components/tier2_runtime/jit_abi.md \
  docs/components/tier3_plugins/jit_runtime.md \
  docs/qa/tier2_runtime/interpreter_test_spec.md \
  docs/qa/tier2_runtime/runtime_loader_test_spec.md \
  docs/qa/tier3_plugins/jit_runtime_test_spec.md
git diff --check
```

tools/README.mdの正本パイプラインを使う。check-src.shのrun_testsは指定ファイルにかかわらず全suiteを起動するため、static_check.pyでは正本Configのrun_testsだけをメモリ内で無効化した。他の静的規則・Tier依存検査は有効である。関連テストは上記pytestで実行した。文書ゲートの失敗出力と検証matrixの結果はdoc_check.logへ保存した。
