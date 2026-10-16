# Interpreterの実行記述子・記憶域整理

2026-10-13に、AO-Benchの前後計測とNative ABI/Loader storageの確認を行った。前後の生データは[`aobench_before.json`](aobench_before.json)と[`aobench_after.json`](aobench_after.json)、現在のLoader/ABI計測は[`storage_after.json`](storage_after.json)に保存した。

## 実行内容

変更前の計測は、cleanup対象の実装をrelease buildして取得した。変更後は、Native InterpreterとJITを再buildし、同じWAMR Fast buildとAO-Bench runnerで再計測した。比較条件は5解像度、3独立プロセス、各3 warmup・7 timed calls、CPU 2固定、yieldしきい値4096である。計時区間にはload/setup/hashを含めない。

```sh
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/compare_wamr.py --wamr-build /tmp/fireball-wamr-fast-o3 --output experiments/pysim/benchmarks/results/storage_cleanup_20261013/aobench_after.json --cpu 2 --trials 3
```

release buildには次を使った。

```sh
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
```

Loader memoryは次のコマンドで測った。

```sh
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/results/storage_cleanup_20261013/measure_storage.py
```

変更後のbuild識別子はClang 21.1.8、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`、Native Interpreter SHA-256 `b3a8af3b3053b144a2f1e78bd15ab61c118c33e41581a4a694119635417171a5`、JIT SHA-256 `d4014b06a4415a00268b9c49b57679b274a4159adf57150cf44d4eba3402d6a5`である。WAMRはcommit `f5f57c09aee623436f5fb87a90798fdd2cdf39fd`、共有ライブラリSHA-256 `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960`を使用した。

## AO-Bench結果

各欄は、3独立プロセスそれぞれの7回中央値をさらに集計した中央値である。

| 解像度 | Engine | 変更前 | 変更後 | 変化 |
| :--- | :--- | ---: | ---: | ---: |
| 64×36 | NativeInterpreter | 17.425 ms | 15.877 ms | −8.88% |
|  | Hybrid JIT | 20.111 ms | 19.057 ms | −5.24% |
|  | WAMR Fast | 4.909 ms | 4.816 ms | −1.88% |
| 128×72 | NativeInterpreter | 67.791 ms | 62.549 ms | −7.73% |
|  | Hybrid JIT | 79.703 ms | 74.722 ms | −6.25% |
|  | WAMR Fast | 19.523 ms | 19.185 ms | −1.73% |
| 192×108 | NativeInterpreter | 154.872 ms | 139.518 ms | −9.91% |
|  | Hybrid JIT | 182.517 ms | 169.037 ms | −7.39% |
|  | WAMR Fast | 44.167 ms | 43.222 ms | −2.14% |
| 256×144 | NativeInterpreter | 270.484 ms | 246.070 ms | −9.03% |
|  | Hybrid JIT | 317.750 ms | 294.648 ms | −7.27% |
|  | WAMR Fast | 80.484 ms | 75.987 ms | −5.59% |
| 320×180 | NativeInterpreter | 425.064 ms | 394.635 ms | −7.16% |
|  | Hybrid JIT | 501.539 ms | 461.494 ms | −7.98% |
|  | WAMR Fast | 122.472 ms | 119.715 ms | −2.25% |

前後で全解像度の出力SHA-256が一致した。hashは64×36 `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567`、128×72 `e9f20b8f0414f7406aa2f3ea96738e1c7755d0148090bf72f8703cffd3d374a4`、192×108 `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1`、256×144 `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4`、320×180 `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699`である。結果差分は全変更の効果であり、個々のstorage項目の因果効果を分離したA/Bではない。

## 記憶域の前後

AO-Benchは937 code byte、107 BasicBlock、50 local、6定義関数と1 importを持つ。Native ModuleExecutionViewが参照していた密な制御表とPC検索表は削除した。Python `block_storage`と`Module.get_block()`は維持した。

| Storage | 変更前 | 変更後 | 削減 |
| :--- | ---: | ---: | ---: |
| Native per-byte control map (`937 × 16`) | 14,992 B | 0 B | 14,992 B |
| Native PC index entries + radix table | 676 B | 0 B | 676 B |
| Native local size table | 50 B | 0 B | 50 B |
| Native ControlStack allocation | 520 B | 0 B | 520 B |
| `ExecutionContextABI` | 168 B | 144 B | 24 B/context |
| `FunctionExecutionViewNative` | 88 B × 7 | 72 B × 7 | 112 B |
| `ModuleExecutionViewNative` | 128 B | 104 B | 24 B |
| 32-entry `CallStackNative` | 1,544 B | 1,288 B | 256 B |

`CallFrameNative`は48 Bから40 Bへ8 B/frame縮小した。32個分の256 Bは`CallStackNative`行に含める。したがって合計から重ねて数えない。`FunctionExecutionViewNative`の7件には1 importを含む。`local_sizes`と`control_map`のview pointer 2個は、view 1件当たり16 Bの縮小に含める。上の合計は16,654 Bである。

Loader arenaは前測定の21,444 Bから現在5,741 Bへ15,703 B（73.23%）減った。容量131,072 Bに対する使用率は16.36%から4.38%へ下がった。Native表削除のgross 15,718 Bに対して、同じ2測定snapshotのannotation poolは1,712 Bから1,729 Bへ17 B増え、allocator alignment差が2 Bある。このためarenaのnet減少は15,703 Bとなる。残差を削除項目の削減量へ加算していない。

`current_block_index`は実行中のBasicBlockを指すindexとして維持した。制御命令は注釈済みindexへ直接更新し、fall-throughは現在blockの`next_block_index`を使う。分岐境界ではindexに対応するPCをassertする。PCからblock indexを全表検索する通常dispatch経路はない。`execution_context.ip`もJITの呼出し・復帰・再開に使うため保持した。

## ABI・仕様と検証

`stack_checkpoint`、Native PC索引entry/radix表、Native `NativeControlMapEntry`、実行時`ControlStackNative`、重複した`CallFrame.func_index`、`LocalLayout._sizes`、`FunctionExecutionView.local_sizes`を削除した。関数番号は共有FunctionExecutionViewから参照し、local幅は保持したWASM型から導出する。旧ABI-onlyの`WasmFunctionViewNative`、`WasmModuleViewNative`、`WasmRunRequestNative`、`WasmRunResultNative`はC++/Python定義とlayout-only試験から削除した。WASM readerの`_AnalysisControlFrame`はロード時の型解析用であり、実行時storageではないため残した。

直接関係するpytestはInterpreter ABI・trap・reader・debuggerとJIT連結を実行した。形式モデルは`guards=True`と`guards=False`の両方で確認した。

```sh
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier2_runtime/test_interpreter_abi.py experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier2_runtime/test_wasm_reader.py experiments/pysim/qa/tier3_plugins/debugger/test_debugger.py experiments/pysim/qa/workloads/test_wasm_core_spec.py::test_official_left_to_right_executes_on_native_interpreter --tb=short
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py -k '31_to_35_trace_chaining_and_ok_unlinking or trace_exit_reuses_loader_successor_block_index_and_pc or native_header_chain_executes_successor_body_once or trace_execution_count_excludes_invalid_static_depth' --tb=short
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py -k '53_loop_entered_after_hot_block_uses_static_branch_metadata or 55_static_branch_edges_resolve_every_resume_point or 69_forward_br_chains_preserve_static_nesting_metadata' --tb=short
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py -k 'jitr_56_jit_runs_in_a_frame_that_also_holds_a_wide_local or jitr_wide_local_accesses_run_as_native_traces or jitr_60_frames_with_different_local_layout_sizes_run_side_by_side or jitr_74_call_indirect_uses_compact_annotation_records or jitr_nested_wasm_call_keeps_callee_result_on_shared_operand_stack or jitr_mixed_typed_callee_returns_keep_the_shared_stack_synchronized or jitr_runtime_engine_surfaces_guest_trap_at_sync_boundary' --tb=short
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python docs/components/tier2_runtime/formal/interpreter_control_flow_model.py
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python docs/components/tier2_runtime/formal/interpreter_stack_model.py
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python docs/components/tier2_runtime/formal/vsoc_state_model.py
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python docs/components/tier3_plugins/formal/jit_cache_model.py
```

pytestは順に202件、7件、3件、7件が通過した。最後の7件はJIT wide-local layout、indirect call、defined-call/return、guest-trapを含む。形式モデルはInterpreter control-flow、stack、VSOC state、JIT cacheの各通常検証と変異検査が通過した。対象C++ source gateと変更PythonファイルのRuff検査は通過した。選択した文書のgateは281件のtraceability/`VERIFY_LLM` obligation errorで失敗した。新しい資源見積もり文書内のファイルリンクformat errorは解消済みであり、残りはlinked document corpusにまたがる既存のkeyword/obligation findingsである。Pysim source gateは`control_flow.py`、`reader.py`、`native_stack_abi.py`の規約違反を報告し、全体`qa/run_all.py`も60秒でtimeoutした。これらのgate結果は対象pytestを直接実行した結果と分けて扱う。
