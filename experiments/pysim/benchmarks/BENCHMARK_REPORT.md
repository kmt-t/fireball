# PySIM AO-Bench 性能比較（2026-10-12）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} -->

## Runtime API命令を含むJIT trace範囲の修正後（最終再計測、2026-10-12）

Loaderのtrace範囲を直接stencilの有無で絞らず、Runtime API loweringされる通常命令も含める修正を反映した最終ソースをrelease再ビルドして計測した。5解像度、3独立プロセス、各3 warmup・7 timed calls、CPU 2固定、yieldしきい値4096、WAMR 2.4.3 Fast Interpreterとの比較である。Python参照Interpreterは対象外とした。全解像度で3実行系のframe hashが一致した。

| サイズ | NativeInterpreter | Hybrid JIT | WAMR Fast |
| :--- | ---: | ---: | ---: |
| 64×36 | 19.477 ms（19.442–19.542） | 25.067 ms（24.907–25.129） | 5.308 ms（5.224–5.385） |
| 128×72 | 74.400 ms（73.972–76.163） | 96.950 ms（96.316–98.123） | 21.556 ms（21.032–22.746） |
| 192×108 | 166.590 ms（165.692–168.090） | 218.846 ms（218.468–221.107） | 46.798 ms（46.583–47.756） |
| 256×144 | 295.573 ms（291.688–297.119） | 390.792 ms（388.355–396.171） | 84.833 ms（83.450–86.489） |
| 320×180 | 463.430 ms（461.691–468.642） | 611.654 ms（607.592–637.384） | 131.888 ms（131.437–132.349） |

計時とは別のQA診断で、320×180のJIT operation bodyを3,947,129回実行し、frame hashも計時runと一致した。修正対象のRuntime API loweringを含むJIT trace経路が実際に動作している。AO-BenchではHybrid JITは全解像度でNativeInterpreterより遅く、320×180では32.0%遅い。WAMR Fastは同サイズでNativeInterpreterの3.51倍、Hybrid JITの4.64倍速かった。この修正でtrace対象範囲と機能の欠落は直ったが、AO-BenchのJIT性能改善は確認できない。個別変更による速度差とは扱わない。全サンプル、出力hash、実行ライブラリとrunnerのSHA-256は[最終計測ログ](results/aobench_interpreter_jit_wamr_20261012_api_span_fix_final.txt)に記録した。

## JIT trace terminal dispatch更新後の再計測（2026-10-12）

JIT traceの正常終端でIPを設定してInterpreterへ戻り、次の基本ブロック設定をInterpreter側で行う変更を含む作業ツリーをreleaseビルドして計測した。前回と同じAO-Bench、5解像度、3独立プロセス、各3 warmup・7 timed calls、CPU 2固定、yieldしきい値4096、WAMR Fast Interpreter比較である。Python参照Interpreterは対象外とした。全解像度でNativeInterpreter、Hybrid JIT、WAMRのフレームhashが一致した。

| サイズ | NativeInterpreter | Hybrid JIT | WAMR Fast |
| :--- | ---: | ---: | ---: |
| 64×36 | 19.390 ms（19.193–19.907） | 25.017 ms（24.972–25.757） | 5.325 ms（5.216–5.326） |
| 128×72 | 75.884 ms（74.780–76.188） | 96.777 ms（96.703–97.946） | 20.843 ms（20.425–20.998） |
| 192×108 | 169.274 ms（169.060–170.083） | 214.921 ms（214.524–215.133） | 47.421 ms（46.457–48.262） |
| 256×144 | 300.114 ms（299.661–300.204） | 383.760 ms（381.374–385.641） | 84.920 ms（83.429–85.327） |
| 320×180 | 468.359 ms（468.153–468.617） | 597.786 ms（593.941–598.083） | 132.697 ms（132.127–144.059） |

320×180ではHybrid JITはInterpreterより27.6%遅く、WAMR FastはInterpreterの3.53倍、Hybrid JITの4.50倍速かった。計時runとは分けたQA診断でも、JIT operation bodyが5,522,136回実行され、trace連結を含むJIT実行経路が有効なことを確認した。診断runのframe hashも一致した。前回値との差は測定変動と複数の作業ツリー変更を含むため、今回のterminal dispatch変更単独の効果とは扱わない。

全プロセスのサンプル、hash、共有ライブラリとrunnerのSHA-256は[2026-10-12 terminal dispatch後の計測ログ](results/aobench_interpreter_jit_wamr_20261012_trace_terminal.txt)に記録した。

## 現行作業ツリーの最新再計測

block-search cacheのhashと関数入口dispatch経路を更新した後、現行作業ツリーからInterpreterとJITをrelease再ビルドして計測した。WAMR Fast Interpreterも同じAO-Bench入力で比較した。5解像度、3独立プロセス、各3 warmup・7 timed calls、CPU 2固定、yieldしきい値4096である。Python参照インタープリタは計時していない。全解像度でInterpreter、Hybrid JIT、WAMRの出力hashが一致した。

| サイズ | NativeInterpreter | Hybrid JIT | WAMR Fast |
| :--- | ---: | ---: | ---: |
| 64×36 | 19.841 ms（19.821–19.848） | 25.926 ms（25.497–26.134） | 5.322 ms（5.287–5.332） |
| 128×72 | 77.411 ms（77.123–77.551） | 101.446 ms（100.737–102.996） | 21.560 ms（21.297–21.829） |
| 192×108 | 173.044 ms（171.280–173.128） | 225.027 ms（222.983–225.496） | 47.474 ms（47.067–47.643） |
| 256×144 | 304.261 ms（304.079–306.671） | 395.313 ms（395.309–398.078） | 86.824 ms（84.903–87.017） |
| 320×180 | 479.342 ms（477.542–482.454） | 621.428 ms（620.282–630.779） | 136.761 ms（134.788–138.940） |

320×180ではHybrid JITがInterpreterより29.6%遅く、WAMRはInterpreterの3.51倍、Hybrid JITの4.54倍速かった。block-search cache単独の効果はAO-Bench 320×180のA/Bでも確認した。cacheありのプロセス中央値はInterpreter 474.013 ms、JIT 618.973 ms、cacheなしはInterpreter 479.675 ms、JIT 623.824 msであり、差はそれぞれ1.18%、0.78%だった。AO-Benchではblock検索cacheの差は小さい。

10,007件のBasicBlockを持つ合成fixtureでは、250,000回のloopを実行し、その後3,334件のcold blockを飛ばした。cacheあり7.460 ms、cacheなし11.224 msで1.505倍だった。大規模block表で同じPCを繰り返し検索する場合には効果があるが、AO-Bench全体の高速化率とは区別する。

全プロセスのサンプル、フレームhash、ライブラリとrunnerのSHA-256は[現行ツリーのInterpreter・JIT・WAMR再計測ログ](results/aobench_interpreter_jit_wamr_20261011_latest_retest.txt)、[AO-Bench block-search cache A/Bログ](results/aobench_block_search_cache_ab_20261011_latest_retest.txt)、[10k BasicBlock block-search cache A/Bログ](results/native_block_search_cache_10k_20261011_latest_retest.txt)に記録した。

## 10,000件BasicBlockでのblock-search cache A/B（初回実装時の履歴）

AO-BenchのBasicBlock数は少ないため、block検索cacheの効果を切り分けられない。10,007件のBasicBlockを持つ合成モジュールを用意し、250,000回のloop実行と3,334件の未実行blockを含む条件でA/B計測した。3独立プロセスすべてでcacheありの方が速く、3試行中央値はcacheあり12.113 ms、cacheなし16.194 ms、1.337倍だった。これはlookupを多く行う合成fixtureでの結果であり、AO-Benchやアプリケーション全体の高速化率を示さない。詳細なサンプルとbuild識別子は[10k BasicBlock cache A/Bログ](results/native_block_search_cache_10k_20261011.txt)に記録した。

## Interpreter block-search cache初回導入時のAO-Bench結果（履歴）

これは2026-10-11に計測した当時のdispatch-local block-search cache実装スナップショットのAO-Bench結果であり、本turnでcache slotのhashと関数入口のdispatch経路を改めた後の再計測値ではない。AO-Benchを従来と同じ5解像度・3独立プロセス・3 warmup・7 timed calls・CPU 2固定・yieldしきい値4096で計測した。当時のInterpreter、Hybrid JIT、WAMR出力は各解像度で一致した。全サンプルは[block-search cache後のAO-Benchログ](results/aobench_interpreter_jit_wamr_20261011_block_search_cache.txt)、cache有無の比較は[block-search cache A/Bログ](results/aobench_block_search_cache_ab_20261011.txt)に記録した。

| サイズ | rays/フレーム | NativeInterpreter | Hybrid JIT | WAMR Fast | JIT/Interpreter | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 7,332 | 17.403 ms（17.376–17.495） | 27.777 ms（27.737–27.956） | 5.241 ms（5.231–5.261） | 1.596× | 3.321× | 5.300× |
| 128×72 | 29,732 | 68.146 ms（67.673–68.613） | 109.592 ms（109.459–109.692） | 21.026 ms（20.854–21.119） | 1.608× | 3.241× | 5.212× |
| 192×108 | 67,032 | 152.874 ms（151.460–153.431） | 246.035 ms（245.353–246.093） | 49.084 ms（48.786–49.305） | 1.609× | 3.115× | 5.013× |
| 256×144 | 119,352 | 271.583 ms（269.863–277.551） | 440.613 ms（434.692–442.096） | 86.042 ms（85.116–86.785） | 1.622× | 3.156× | 5.121× |
| 320×180 | 186,792 | 427.135 ms（426.045–428.798） | 695.104 ms（692.933–702.089） | 138.342 ms（136.496–138.541） | 1.627× | 3.088× | 5.025× |

値は各独立プロセス内7サンプルの中央値を取り、3プロセス中央値の中央値を代表値とした。括弧内は3プロセス中央値の最小値と最大値である。320×180のInterpreterは427.135 msで、400 msを超えている。

card-state gate後の基準値との差は、複数の実装変更と測定変動を含むため、block-search cache単独の効果とは扱わない。キャッシュ単独の有無は別のA/Bで確認した。

## card-state gate後の基準結果

block-search cache A/B前に、card-state bitmapでJIT provider lookupをゲートする作業ツリーから計測した値である。計測ソースと実行環境の識別情報は[card-state gate後の再計測ログ](results/aobench_interpreter_jit_wamr_20261011_card_gate.txt)に記録した。

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

各プロセスの全サンプル、出力ハッシュ、build識別子は[block-search cache後のAO-Benchログ](results/aobench_interpreter_jit_wamr_20261011_block_search_cache.txt)に記録した。キャッシュA/B前のQA診断カウンターと計測識別子は[card-state gate後の再計測ログ](results/aobench_interpreter_jit_wamr_20261011_card_gate.txt)にある。gate前の同条件計測は[gate前ログ](results/aobench_interpreter_jit_wamr_20261011_final_current.txt)、直前のloader更新後計測は[loader更新後ログ](results/aobench_interpreter_jit_wamr_20261011_post_loader.txt)、過去値は[2026-10-07の計測ログ](results/aobench_interpreter_jit_wamr_20261007.txt)にある。
