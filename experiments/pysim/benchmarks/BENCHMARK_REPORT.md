# PySIM AO-Bench 性能比較（2026-10-11 card-state gate後）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} -->

## 現行結果

card-state bitmapでJIT provider lookupをゲートする変更を含む作業ツリーから、NativeInterpreterとHybrid JITのrelease共有ライブラリを再ビルドした。WAMR Fast Interpreterと同じAO-Bench条件で5解像度×3試行を完走した。全解像度で各エンジンの出力ハッシュが一致した。計測ソースと実行環境の識別情報は[card-state gate後の再計測ログ](results/aobench_interpreter_jit_wamr_20261011_card_gate.txt)に記録した。

| サイズ | rays/フレーム | NativeInterpreter | Hybrid JIT | WAMR Fast | JIT/Interpreter | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 7,332 | 21.030 ms（20.751–21.173） | 32.841 ms（32.592–32.846） | 5.381 ms（5.311–5.516） | 1.562× | 3.908× | 6.103× |
| 128×72 | 29,732 | 81.402 ms（81.384–81.452） | 129.123 ms（128.784–129.539） | 21.636 ms（21.594–22.029） | 1.586× | 3.762× | 5.968× |
| 192×108 | 67,032 | 181.535 ms（181.282–182.085） | 288.043 ms（285.964–290.399） | 48.991 ms（48.940–49.334） | 1.587× | 3.705× | 5.880× |
| 256×144 | 119,352 | 321.336 ms（320.298–326.375） | 513.304 ms（506.936–518.108） | 86.988 ms（85.318–87.576） | 1.597× | 3.694× | 5.901× |
| 320×180 | 186,792 | 508.072 ms（500.646–509.976） | 819.902 ms（801.680–822.724） | 137.818 ms（137.190–138.429） | 1.614× | 3.687× | 5.949× |

値は各独立プロセス内7サンプルの中央値を取り、3プロセス中央値の中央値を代表値とした。括弧内は3プロセス中央値の最小値と最大値である。

## bitmap gate前後の比較

同じ日のbitmap gate前計測と比べたJIT時間を示す。両計測は同一の解像度、yieldしきい値、warmup、試行数、CPU固定条件を使った。

| サイズ | gate前JIT | gate後JIT | 変化 |
| :--- | ---: | ---: | ---: |
| 64×36 | 33.958 ms | 32.841 ms | -3.3% |
| 128×72 | 134.122 ms | 129.123 ms | -3.7% |
| 192×108 | 323.620 ms | 288.043 ms | -11.0% |
| 256×144 | 575.817 ms | 513.304 ms | -10.9% |
| 320×180 | 833.575 ms | 819.902 ms | -1.6% |

320×180ではJIT/Interpreter比が1.644倍から1.614倍へ下がった。JITは全解像度で依然としてNativeInterpreterより遅い。別プロセス計測の変動を含むため、解像度ごとの改善率を厳密な因果効果とは扱わない。

## JIT実行経路の診断

時間計測の終了後、各解像度を独立のQA診断実行に通した。JIT providerのlookup callback呼出し数は、時間計測へ含めていない。

| サイズ | gate前lookup callback | gate後lookup callback | スキップ数 | スキップ率 | gate後hit数 | frame_bytes |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 732,490 | 238,910 | 493,580 | 67.4% | 238,910 | 2,340 |
| 128×72 | 2,953,224 | 956,191 | 1,997,033 | 67.6% | 956,191 | 9,288 |
| 192×108 | 6,640,775 | 1,959,708 | 4,681,067 | 70.5% | 1,959,708 | 20,844 |
| 256×144 | 11,829,269 | 3,500,988 | 8,328,281 | 70.4% | 3,500,988 | 37,008 |
| 320×180 | 18,498,388 | 5,868,175 | 12,630,213 | 68.3% | 5,868,175 | 57,780 |

Interpreter dispatcherが2-bit card-state bitmapを読み、状態が`COMPILED`でないカードではprovider関数ポインタを呼ばない。AO-Benchではlookup callbackを67〜71%削減した。gate後に呼ばれたcallbackはすべてtrace hitとなった。スキップ件数はgate前後の独立診断実行差分であり、計時run中の厳密な命令数ではない。

## uProf診断

card-state gate前に計測ホスト上でAMD uProf hotspotsをInterpreterとJITの各1回実行した。プロファイラ実行時間は計測オーバーヘッドを含むため通常ベンチマークとの比較には使わず、関数別サンプルの所在だけを診断に使った。Interpreterでは `h_runtime_api<runtime_local_get>` が1.18秒、`h_runtime_api<runtime_call<false>>` が1.06秒、`runtime_end` が0.72秒、`runtime_if` が0.37秒、`runtime_br_if` が0.34秒だった。JITでは `fb_native_run_dispatch_jit` が自己時間2.92秒・子関数込み9.81秒で、同じRuntime APIハンドラ群（call 1.42秒、local_get 0.96秒）に加え `native_jit_plugin::lookup_runtime_trace`（自己時間0.44秒・子関数込み1.05秒）が現れた。これらはgate前のprofileであり、現行経路の関数別割合としては扱わない。

profileのlookup時間とAO-Bench callback差分が同じ箇所を示すため、未コンパイルカードでもJIT providerを呼ぶ処理が余分な経路だったと確認できた。現行dispatcherはbitmapでそれらの呼出しを省く。JIT内のtrace検索とInterpreter Runtime API呼出しは引き続き必要である。uProfのLinux hotspots分析は[AMD uProf CLI Hotspots guide](https://docs.amd.com/r/en-US/68658-uProf-getting-started-guide/Hotspots-Analysis-Using-AMD-uProf-CLI)に従った。

## 計測条件

- 5解像度×3独立プロセス×3エンジンを計測した。各プロセスで3フレームをウォームアップし、後続7フレームを計時した。試行ごとにエンジンの実行順を入れ替え、CPU 2へ固定した。
- InterpreterとHybrid JITは同じNativeInterpreter.call()入口とyieldしきい値4096を使った。Python参照インタープリタは計測対象外とした。
- JITはウォームアップ後にidle_hookで待ち列を処理し、計時呼び出しはidle_budget=0とした。JIT実行カウンターは計時後の独立診断で記録した。
- WASM出力は全15組で一致した。サイズごとの出力SHA-256は64×36: `570ecec0d82c7dfc11805f203e7f5282f12cf1882d0f774783b8d9c057ac5ee2`、128×72: `b6a28d273e549e783d8e5526fd2f9ad1ed8f08b45fba32571f689d7034f6ea03`、192×108: `48b7b02de263c6610328b961fd0f5c2ea901cb82731901b6c36477c0eb913c0c`、256×144: `2ce48b29f0eeabda19490cdf68d35742bbbc837ebb2e6b5d3185df980cdfa67c`、320×180: `b836421dc04ef17309c5bb32b760cdb38f4de85e9cea5943fb90df0697f92f48`。
- WASMのSHA-256は9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9。
- CPUはAMD Ryzen 5 5500GT、Linux 7.0.0-38-generic x86_64。CPythonは3.13.15、Clangは21.1.8。
- Interpreter/JIT共有ライブラリは2026-10-11に現行作業ツリーからrelease再ビルドした。WAMRは2.4.3、commit f5f57c09aee623436f5fb87a90798fdd2cdf39fd。Fast Interpreter + WASIを使い、AOT/JITを無効にした。
- WASM解析、ホスト・Interpreter準備、WAMR初期化とmodule load/instantiate、実行環境生成は計時区間から除外した。計時対象は各フレームのNativeInterpreter.call()またはwasm_runtime_call_wasm()である。
- uProf診断はInterpreterとJITを別々に実施した。診断のプロファイラ時間は通常ベンチマークの集計には含めていない。

## 比較上の制約

計測ホストはx86-64である。このAO-Bench結果から、ARMでの大きな即値ロード列とrel経由ロードの速度差は判断できない。ARM上の方式比較にはAArch64実機計測が必要である。また、FireballはPython WASI/HALとStreamTransport、WAMRはC WASIとstdoutを通るため、この値はホスト統合を含むワークロード比較であり、命令ディスパッチ単体の比較ではない。

## 詳細記録

各プロセスの全サンプル、出力ハッシュ、QA診断カウンター、build識別子は[card-state gate後の再計測ログ](results/aobench_interpreter_jit_wamr_20261011_card_gate.txt)に記録した。gate前の同条件計測は[gate前ログ](results/aobench_interpreter_jit_wamr_20261011_final_current.txt)、直前のloader更新後計測は[loader更新後ログ](results/aobench_interpreter_jit_wamr_20261011_post_loader.txt)、過去値は[2026-10-07の計測ログ](results/aobench_interpreter_jit_wamr_20261007.txt)にある。
