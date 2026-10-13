# Interpreter 呼び出し・制御フレームと JIT 生成処理の評価

計測日: 2026-10-13
対象: 依頼C（`runtime_call`）、依頼E（設計案のみ）、依頼D（現状確認）、依頼F（JIT生成側）

## 計測条件とビルド識別子

AO-Bench は 64×36、128×72、192×108、256×144、320×180 の5解像度で測定した。各解像度・エンジンにつき3独立プロセス、各プロセスで3回 warmup、7回 timed call とした。CPUは `taskset -c 2` で固定し、yield threshold は4096とした。計時には `--release` ビルド（Clang 21.1.8、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`）を使った。計数ビルドと uProf 用ビルドの時間は AO-Bench の計時結果に採用していない。

最終ビルドのSHA-256は次のとおり。

| 成果物 | SHA-256 |
| :--- | :--- |
| NativeInterpreter `libnative_interpreter.so` | `fdb7a2e148230f5da0a373700a4d6ca43c7758491cc16a52fb92b3d8ca406ce4` |
| JIT `libtrace_compiler.so` | `c80024834cd51122099e38474ea1f38e4955c68a5942ec83b4b7fa7a08ba48b6` |
| WAMR Fast `libiwasm.so` | `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960` |

最終計測が参照した変更対象ソースのSHA-256は、`native_wasm_operations.cxx` が `26843740958bebae6ef9aefb9dc7d6fd120932adf7ea470a4265c32f845385ac`、`trace_compiler.cxx` が `664adede2c81b344dbb0ec90ca83c658305a86cb29eade9b9477e6620bafef43`、`generate_assembly.py` が `8dfd0623ae7bb545d818ec57bbff19ee1be078b3b74b7160e5940b406c6b680e` である。AO-Bench の全出力ハッシュは変更前と一致した。

計数用一時ビルドの識別子は、呼び出しカウンタが元ソースSHA `d03d4cf4b6f95008f236472b01817e7574d8f33143187b83527b20185662a6a8`、元releaseライブラリSHA `f778fcaeabcf03eea632333837620605af3d100244028457ed2da19998877251`、診断ライブラリSHA `9f5d177d0515a6454df37091a3b4ef8b0c765a1ae9008e252c631907f0fb727e` である。制御フレームカウンタは元ソースSHA `2d09c647d1bc4676d874da759716b6ca65646075aeb4f256f3822555ff89f95d`、元releaseライブラリSHA `fdb7a2e148230f5da0a373700a4d6ca43c7758491cc16a52fb92b3d8ca406ce4`、診断ライブラリSHA `49a213b2875c44d85f426dc99c13669f6a101aea2cb7700f0dd37b7fa36b8ae6` である。診断用変更は一時的に挿入し、ソースとライブラリを元のSHAへ戻した。

## 依頼C: 呼び出しフレーム構築

AO-Bench 1呼び出しあたりの計数は次のとおり。値は計測専用カウンタを持つ一時ビルドから得た。各プロセスのカウンタ値は解像度ごとに一致した。

| 解像度 | `runtime_call` 回数 | 定義済み関数フレーム数 | 引数コピー語数 | 追加ローカルのゼロ化語数 |
| :--- | ---: | ---: | ---: | ---: |
| 64×36 | 210,578 | 210,577 | 622,400 | 202,782 |
| 128×72 | 852,748 | 852,747 | 2,521,882 | 821,356 |
| 192×108 | 1,922,018 | 1,922,017 | 5,684,884 | 1,850,970 |
| 256×144 | 3,421,799 | 3,421,798 | 10,121,155 | 3,296,043 |
| 320×180 | 5,354,466 | 5,354,465 | 15,838,692 | 5,157,834 |

引数とローカルは実行コンテキスト内の別領域にあり、引数の位置をローカル領域に重ねることはできない。引数のパック済み語数は連続しているため、要素ごとのネストしたコピーを `std::memcpy` 1回へ置き換えた。関数型、引数語数、ローカル総語数などの関数ごとに決まる値は既に `target` の関数ビューに保持されている。ゼロ化範囲の事前保存は、`local_total_words - param_words` 相当の簡単な範囲計算を削るだけなので実装しなかった。ゼロ初期化と呼び出し深さ検査は維持した。

320×180で `runtime_call` のフレーム準備範囲（引数転送、ゼロ化、フレーム設定を含む）を AMD uProf で3回サンプリングした推定値は、変更前の中央値が AO 呼び出し時間の6.48%・26.36 ms、変更後が2.81%・10.56 msだった。呼び出し数で割ると約4.92 nsから約1.97 ns/フレームとなる。これはソース行範囲のサンプルに基づく推定であり、コピー命令だけを分離した時間ではない。計時対象となるフレーム準備範囲のサンプル数は変更前173、変更後71（各3回の中央値）であり、細かな時間差の解釈には限界がある。

| 解像度 | Interpreter 変更前 (ms) | 変更後 (ms) | 変化 |
| :--- | ---: | ---: | ---: |
| 64×36 | 16.912 | 15.351 | −9.23% |
| 128×72 | 65.397 | 61.141 | −6.51% |
| 192×108 | 154.102 | 137.481 | −10.79% |
| 256×144 | 268.813 | 240.798 | −10.42% |
| 320×180 | 406.965 | 376.573 | −7.47% |

計数ビルドの時間と通常の変更前計時ビルドを比較した差は、64×36 −0.02%、128×72 +1.22%、192×108 −3.15%、256×144 −2.44%、320×180 +1.51%だった。計数による一貫した遅延傾向は見えず、これらはプロセス間の測定揺れと区別できない。uProf の計数とは別に、制御フレーム診断ビルドと通常ビルドの差は順に+0.66%、−0.20%、−1.21%、+7.38%、−0.34%だった。256×144の値を含め、診断カウンタ自体の正確なオーバーヘッドを求められる精度はない。

## 依頼E: 制御フレームを静的注釈に置き換える設計案

実装は行っていない。設計案を [interpreter.md](../../../../../../docs/components/tier2_runtime/interpreter.md) の「制御フレームを静的注釈へ置き換える設計案（未承認・未実装）」に追加した。型解析時に各制御構造の入口語高と字句深度を記録し、`br`、`br_if`、`br_table` の全ラベル、`return` に、分岐先ブロック番号、戻り値語数、破棄する語数、分岐後の深度を持たせる案である。`br_if` の不成立経路は条件語だけを消費する。`else` は対応する `end` の後ろへ進み、`end` は深度を戻す。`loop` はループ入口へ分岐する。I64/F64も注釈上は2語として扱う。

制御フレーム診断カウンタの1 AO 呼び出しあたりの値は次のとおり。pushと各popの合計は一致する。

| 解像度 | push | `else` pop | `end` pop | branch pop | static prune | 関数return pop |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 82,729 | 17,174 | 26,773 | 15,994 | 0 | 22,788 |
| 128×72 | 332,570 | 68,167 | 108,100 | 63,646 | 0 | 92,657 |
| 192×108 | 746,726 | 152,377 | 242,827 | 142,138 | 0 | 209,384 |
| 256×144 | 1,330,174 | 271,996 | 432,175 | 253,578 | 0 | 372,425 |
| 320×180 | 2,079,358 | 423,916 | 676,302 | 395,790 | 0 | 583,350 |

既存の320×180 uProf 記録では、制御フレームの保存命令範囲は全サンプルの0.125%に相当し、約0.585 ms/AO呼び出しと推定された。制御ハンドラ全体は約2.65%・12.39 msだった。保存命令の計測サンプルは9,591件中12件のみなので、フレーム配列の積み下ろしだけをなくす効果は大きくない可能性が高い。実装判断の前に、制御ハンドラ全体からフレーム状態参照を除ける範囲を設計レビューする必要がある。

## 依頼D: JIT の local 命令

AO-Benchの `local.get`、`local.set`、`local.tee` は既に直接stencilへ分岐していた。`trace_compiler.cxx` の `local_stencil` 経路は3命令をI32/I64/F32/F64の各型で処理し、JIT runtime API表の opcode `0x20`〜`0x22` は `nullptr` であることを静的検査している。そのため、この作業では追加のstencilやコード変更は行っていない。AO-Bench上でこれら3命令がruntime APIに下がる回数は構造上0回である。今回の開始時点ですでに直接stencil化されていたため、依頼Dによる前後差は発生せず、D以前のAO trace計数も存在しない。JIT差分テスト11件は通過した。

## 依頼F: JIT生成側の整理

共通runtime API stencilの4種類を、命令数を相対情報欄に符号化する1種類へまとめた。失敗終端の戻り値パック処理を成功終端と共有し、生成済みtraceの構築では命令追加ごとの本体 `memmove` と最後のwrapper `memmove` をなくした。分岐オフセット変換は、拡張長のprefixを一度構築し、二分探索で対応位置を求める形にした。分岐ごとの探索総数は個別カウンタでは記録していないため、この点の実行回数は未計測である。

共通末尾の重複を減らす変換は、`tools/jit_stencil_gen/generate_assembly.py` 内で生成前に適用する。手書きLIRの最適化前入力を変更せず、変換後のtrace・operationsバイナリは計測時と同じSHA-256・サイズになることを確認した。

| 生成role | 変更前バイト | 変更後バイト | 差 |
| :--- | ---: | ---: | ---: |
| trace | 670 | 660 | −10 (−1.49%) |
| operations | 2,094 | 1,531 | −563 (−26.89%) |
| common | 57 | 57 | 0 |
| 合計 | 2,821 | 2,248 | −573 (−20.31%) |

同じtrace構成を10,000回コンパイルする3独立プロセス測定では、1 traceあたりの中央値は31.928 µsから31.486 µs（−1.38%）だった。差は小さく、コンパイル時間は実質横ばいと判断する。AO-Benchの実行時間にも一貫した悪化・改善は確認できなかった。

## 最終AO-Bench結果

最終ライブラリ識別子の下での中央値を示す。各解像度で3エンジンの出力frame SHA-256は一致し、変更前とも一致した。

| 解像度 | Interpreter (ms) | JIT (ms) | WAMR Fast (ms) | 出力frame SHA-256 |
| :--- | ---: | ---: | ---: | :--- |
| 64×36 | 15.240 | 18.642 | 4.772 | `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567` |
| 128×72 | 60.938 | 73.533 | 19.156 | `e9f20b8f0414f7406aa2f3ea96738e1c7755d0148090bf72f8703cffd3d374a4` |
| 192×108 | 133.913 | 163.291 | 42.681 | `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1` |
| 256×144 | 239.566 | 292.306 | 76.491 | `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4` |
| 320×180 | 374.698 | 454.066 | 117.403 | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

320×180ではInterpreterはWAMR Fastの約3.19倍だった。`runtime_call` のコピー変更前後では、5解像度すべてでInterpreter中央値が低下した。一方、Fの前後を比べたJIT差は−1.78%〜+1.35%の範囲に収まり、実行性能への効果は測定揺れと区別できなかった。

## 検証結果と未完了事項

通過した関連テストは次のとおり。

```text
.venv/bin/python -m pytest -q experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py experiments/pysim/qa/workloads/test_wasm_core_spec.py
137 passed

.venv/bin/python -m pytest -q experiments/pysim/qa/tier3_plugins/jit/test_jit_differential.py experiments/pysim/qa/tier3_plugins/jit/test_jit_runtime.py
131 passed

UV_CACHE_DIR=/tmp/fireball-uv-cache ./tools/check-src.sh -g cpp experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx experiments/pysim/native/tier3_plugins/jit/trace_compiler.cxx
passed

.venv/bin/ruff check tools/jit_stencil_gen/generate_assembly.py experiments/pysim/benchmarks/results/runtime_call_20261013/measure_call_diagnostic.py experiments/pysim/benchmarks/results/runtime_call_20261013/measure_control_frames.py experiments/pysim/benchmarks/results/runtime_call_20261013/measure_jit_compiler.py
passed
```

`py_compile`も生成器と3計測スクリプトで通過した。`check-src -g python` は一度、計測スクリプトの `object` 型注釈を検出したため具体的union型に修正した。修正後の同ゲートは対象ファイルの静的検査を終えてからQA全体の `qa/run_all.py` が60秒でタイムアウトしたため、ゲート全体としては未通過である。直接実行した関連テストは通過している。

`check-doc.sh docs/components/tier2_runtime/interpreter.md` は文書ゲート111エラー、5警告で失敗した。今回の設計案より前の行にある既存キーワード参照を含め、作業ツリー内の文書全体に未定義キーワードと未履行検証義務の指摘がある。今回追加した案は未承認・未実装と明記しており、形式モデルやテスト仕様書は変更していない。

依頼Eの実装は、指示どおり設計承認待ちである。branch offset変換の実行回数、個別branch命令のAO-Bench分布、uProfのsampling誤差は今回の測定で確定していない。コミットは作成していない。

## 再実行コマンドと保存先

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh --release
UV_CACHE_DIR=/tmp/fireball-uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh --release
.venv/bin/python experiments/pysim/benchmarks/aobench/compare_wamr.py --wamr-build /tmp/fireball-wamr-fast-o3 --output experiments/pysim/benchmarks/results/runtime_call_20261013/aobench_final.json --cpu 2 --trials 3
.venv/bin/python experiments/pysim/benchmarks/results/runtime_call_20261013/run_call_diagnostics.py
.venv/bin/python experiments/pysim/benchmarks/results/runtime_call_20261013/run_control_frame_diagnostic.py
.venv/bin/python experiments/pysim/benchmarks/results/runtime_call_20261013/profile_frame_setup.py
taskset -c 2 .venv/bin/python experiments/pysim/benchmarks/results/runtime_call_20261013/measure_jit_compiler.py --label after --trial 1
```

`measure_jit_compiler.py` は `--label after --trial 1` の trial番号を1〜3に変えて3回実行する。`before` の値は最適化前に同じ引数で採取した記録であり、現作業ツリーからは再生成できない。全JSON、診断・計時スクリプト、uProf生ログ、コンパイラ測定値は [`runtime_call_20261013/`](.) に保存した。主な再確認先は [aobench_final.json](aobench_final.json)、[call_diagnostic.json](call_diagnostic.json)、[control_frame_diagnostic.json](control_frame_diagnostic.json)、[frame_setup_profile.json](frame_setup_profile.json)、[jit_compiler_before_1.json](jit_compiler_before_1.json)、[jit_compiler_after_1.json](jit_compiler_after_1.json) である。
