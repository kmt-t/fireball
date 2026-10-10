# PySIM AO-Bench 性能比較（2026-10-11 loader更新後）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} -->

## 現行結果

現行作業ツリーからNativeInterpreterとHybrid JITのrelease共有ライブラリを再ビルドし、WAMR Fast Interpreterと同じAO-Bench条件で計測した。5解像度×3試行を完走し、全解像度で各エンジンの出力ハッシュが一致した。

| サイズ | rays/フレーム | NativeInterpreter | Hybrid JIT | WAMR Fast | JIT/Interpreter | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 7,332 | 19.925 ms（19.684–20.121） | 34.473 ms（32.033–34.690） | 5.170 ms（5.165–5.219） | 1.730× | 3.854× | 6.668× |
| 128×72 | 29,732 | 76.851 ms（76.781–79.478） | 124.301 ms（124.124–125.202） | 20.942 ms（20.618–21.093） | 1.617× | 3.670× | 5.935× |
| 192×108 | 67,032 | 171.880 ms（171.128–174.905） | 301.819 ms（278.133–304.253） | 47.498 ms（47.028–47.681） | 1.756× | 3.619× | 6.354× |
| 256×144 | 119,352 | 307.697 ms（307.003–317.092） | 496.784 ms（493.231–498.595） | 83.291 ms（83.286–90.365） | 1.615× | 3.694× | 5.964× |
| 320×180 | 186,792 | 484.758 ms（483.393–485.513） | 786.252 ms（779.301–852.489） | 131.642 ms（130.189–132.702） | 1.622× | 3.682× | 5.973× |

値は各独立プロセス内7サンプルの中央値を取り、3プロセス中央値の中央値を代表値とした。括弧内は3プロセス中央値の最小値と最大値である。

## 直近の同日計測との差

直近の同条件ログ（bool Runtime API版）と比べると、320×180ではNativeInterpreterが559.279 msから484.758 ms（13.3%短縮）、JITが881.125 msから786.252 ms（10.8%短縮）となった。WAMRは131.905 msから131.642 ms（0.2%短縮）で、ほぼ同じだった。これは計測値の比較であり、変化の原因までは特定していない。

今回もJITはNativeInterpreterより1.622倍遅く、WAMRはNativeInterpreterより3.682倍、JITより5.973倍速い。AO-BenchではJITの改善後もNativeInterpreterを上回っていない。

## JIT実行経路の診断

時間計測の終了後、各解像度を独立のQA診断実行に通した。以下のJITカウンターは時間計測には含めていない。

| サイズ | jit_lookup_calls | jit_trace_hits | jit_trace_bodies | frame_bytes |
| :--- | ---: | ---: | ---: | ---: |
| 64×36 | 732,490 | 238,910 | 311,464 | 2,340 |
| 128×72 | 2,953,224 | 956,191 | 1,280,723 | 9,288 |
| 192×108 | 6,640,775 | 2,105,438 | 2,896,617 | 20,844 |
| 256×144 | 11,829,269 | 3,500,988 | 4,694,756 | 37,008 |
| 320×180 | 18,498,388 | 5,868,175 | 7,984,818 | 57,780 |

## 計測条件

- 5解像度×3独立プロセス×3エンジンを計測した。各プロセスで3フレームをウォームアップし、後続7フレームを計時した。試行ごとにエンジンの実行順を入れ替え、CPU 2へ固定した。
- InterpreterとHybrid JITは同じNativeInterpreter.call()入口とyieldしきい値4096を使った。Python参照インタープリタは計測対象外とした。
- JITはウォームアップ後にidle_hookで待ち列を処理し、計時呼び出しはidle_budget=0とした。JIT実行カウンターは計時後の独立診断で記録した。
- WASM出力は全15組で一致した。サイズごとの出力SHA-256は64×36: `570ecec0d82c7dfc11805f203e7f5282f12cf1882d0f774783b8d9c057ac5ee2`、128×72: `b6a28d273e549e783d8e5526fd2f9ad1ed8f08b45fba32571f689d7034f6ea03`、192×108: `48b7b02de263c6610328b961fd0f5c2ea901cb82731901b6c36477c0eb913c0c`、256×144: `2ce48b29f0eeabda19490cdf68d35742bbbc837ebb2e6b5d3185df980cdfa67c`、320×180: `b836421dc04ef17309c5bb32b760cdb38f4de85e9cea5943fb90df0697f92f48`。
- WASMのSHA-256は9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9。
- CPUはAMD Ryzen 5 5500GT、Linux 7.0.0-38-generic x86_64。CPythonは3.13.15、Clangは21.1.8。
- Interpreter/JIT共有ライブラリは2026-10-11に現行作業ツリーからrelease再ビルドした。WAMRは2.4.3、commit f5f57c09aee623436f5fb87a90798fdd2cdf39fd。Fast Interpreter + WASIを使い、AOT/JITを無効にした。
- WASM解析、ホスト・Interpreter準備、WAMR初期化とmodule load/instantiate、実行環境生成は計時区間から除外した。計時対象は各フレームのNativeInterpreter.call()またはwasm_runtime_call_wasm()である。
- 新規uProf計測は行っていない。通常ベンチマークの時間にはプロファイラを含めていない。

## 比較上の制約

計測ホストはx86-64である。このAO-Bench結果から、ARMでの大きな即値ロード列とrel経由ロードの速度差は判断できない。ARM上の方式比較にはAArch64実機計測が必要である。また、FireballはPython WASI/HALとStreamTransport、WAMRはC WASIとstdoutを通るため、この値はホスト統合を含むワークロード比較であり、命令ディスパッチ単体の比較ではない。

## 詳細記録

各プロセスの全サンプル、出力ハッシュ、QA診断カウンター、build識別子は[今回の再計測ログ](results/aobench_interpreter_jit_wamr_20261011_post_loader.txt)に記録した。比較対象の同日計測は[bool Runtime API版ログ](results/aobench_interpreter_jit_wamr_20261011_bool_api.txt)、過去値は[2026-10-07の計測ログ](results/aobench_interpreter_jit_wamr_20261007.txt)にある。
