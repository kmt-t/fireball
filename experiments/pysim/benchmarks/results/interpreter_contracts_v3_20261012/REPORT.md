# Interpreter 契約整理・性能改善 第2弾 第3版の計測結果

2026-10-12。Step 0とStep 1を先に完了し、計数追加を復元した後でStep 2〜4を実施した。git commitは行っていない。既存の作業ツリー変更を保持した。

## 結論

命令幅の固定u16化、関数入口のオペランド容量検査、通常callからの毎命令境界検査の除去、指定された内部契約のassert化を実装した。容量不足時のWASM trapとの優先順位変更は依頼者の追加回答で許容された。関数途中のJIT trace入口にオペランド容量検査を追加する必要はなく、既存の再検査を外した。

320×180のNativeInterpreterは、Step 0直後の467.904 msから最終443.025 msへ5.32%短縮した。最終WAMR Fastは118.918 msであり、Interpreterは3.725倍の時間を要する。差は残っており、3.5倍の差を解消する結果にはなっていない。これは全変更後の観測比較であり、各Step単独の寄与は識別しない。交互A/Bではないため、ホスト変動を分離した因果効果とは扱わない。64×36はStep 0直後より約1.92%長く、全解像度で一様に改善した結果ではない。

## Step 0: 定数プール

命令幅はすべてlittle-endianの2byte u16とした。65535を超える幅は読み込み時に`ERR_WASM_UNSUPPORTED_FEATURE: instruction width exceeds u16`で拒否する。0xFF sentinel分岐をInterpreterとJITの共有payload生成・復帰時rel消費量から削除した。

| 関数index | 命令数 | 変更前byte | 変更後byte | 増加byte |
|---|---:|---:|---:|---:|
| 0 | 6 | 18 | 24 | 6 |
| 1 | 12 | 32 | 44 | 12 |
| 2 | 67 | 219 | 286 | 67 |
| 3 | 69 | 265 | 334 | 69 |
| 4 | 102 | 394 | 496 | 102 |
| 5 | 254 | 1018 | 1272 | 254 |
| 合計 | 510 | 1946 | 2456 | 510 |

増加率は26.2076%である。AO-BenchのLoader arena使用量は25756 / 131072 byteであり、ホストの設定上限内に収まった。ターゲットMCUの物理予算はTBDのため、この計測から物理ROM/RAM予算への適合は証明できない。追加の超過判定テストは幅254、255、256、65535の受理と65536の拒否・allocator巻き戻しを確認した。

## Step 1: 計数

各解像度で3独立プロセス、各3 warmup・7 timed calls、CPU 2固定、yieldしきい値4096とした。以下はAO-Bench 1呼出し当たりである。検索、hit/miss、制御構築、runtime_call、callee構築はInterpreter/JITで同じ値になった。JIT lookupは21呼出しの平均を示す。NativeInterpreter単独のlookupは0である。

| 解像度 | 検索 | hit | miss | 制御構築合計 | runtime_call | callee構築 | Interpreter外側loop | JIT外側loop | JIT lookup平均 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 64×36 | 745,491 | 284,672 | 460,819 | 82,729 | 210,578 | 210,577 | 745,491 | 745,492 | 222,807.000 |
| 128×72 | 3,005,146 | 1,146,574 | 1,858,572 | 332,570 | 852,748 | 852,747 | 3,005,146 | 3,005,147 | 903,902.143 |
| 192×108 | 6,756,885 | 2,576,769 | 4,180,116 | 746,726 | 1,922,018 | 1,922,017 | 6,756,885 | 6,756,886 | 2,077,694.857 |
| 256×144 | 12,036,303 | 4,591,553 | 7,444,750 | 1,330,174 | 3,421,799 | 3,421,798 | 12,036,303 | 12,036,304 | 3,733,500.857 |
| 320×180 | 18,821,697 | 7,178,464 | 11,643,233 | 2,079,358 | 5,354,466 | 5,354,465 | 18,821,697 | 18,821,698 | 5,970,030.143 |

制御構築は`block/loop`と実際に生成された`if`フレームの合計である。falseのelseなしifは構築に含めない。runtime_callにはhost import 1回を含むため、callee構築より1回多い。初期のPython側トップレベルCallFrame 1個はcallee構築カウンタに含めない。JIT lookupのmin/max、個別の制御種別、dispatch呼出し回数は`step1/counts.csv`と`step1/summary.json`に保存した。

## Step 1: 時間内訳

計数のないO3/NDEBUG/ThinLTOビルドをAMD uProf 5.3.521 Hotspotsで1ms間隔にサンプリングした。warmup後にresumeし、7回の計時呼出しの後にpauseした。Interpreterには一時的に-gを付けたが、通常ビルドと.textのSHA-256が一致することを確認した。時間は独立の通常計時の中央値×サンプル割合による推定である。1回当たりのns値は、その推定時間を診断回数で割った償却平均であり、個別呼出しを時計で囲った実測ではない。

検索全体にはcache解決とmiss後の検索を含む。フレーム構築はruntime_callの部分集合であり、列を足してはならない。制御構築storeのサンプルは320×180でもInterpreter 12、JIT 9と少ないため、サブnsの値を精密な単体レイテンシとして解釈しない。

| 実行系 | 解像度 | 処理 | 推定ms/呼出し | 割合% | 償却ns/処理1回 |
|---|---|---|---:|---:|---:|
| interpreter | 64×36 | 基本ブロック検索全体 | 3.030 | 15.686 | 4.065 |
| interpreter | 64×36 | runtime_call全体 | 2.020 | 10.458 | 9.593 |
| interpreter | 64×36 | calleeフレーム構築部分 | 1.263 | 6.536 | 5.996 |
| interpreter | 64×36 | 制御フレームstore部分 | 0.000 | 0.000 | 0.000 |
| jit | 64×36 | 基本ブロック検索全体 | 2.456 | 10.860 | 3.294 |
| jit | 64×36 | runtime_call全体 | 1.433 | 6.335 | 6.803 |
| jit | 64×36 | calleeフレーム構築部分 | 0.921 | 4.072 | 4.374 |
| jit | 64×36 | 制御フレームstore部分 | 0.000 | 0.000 | 0.000 |
| jit | 64×36 | JIT lookup | 1.228 | 5.430 | 5.511 |
| interpreter | 128×72 | 基本ブロック検索全体 | 15.195 | 20.107 | 5.056 |
| interpreter | 128×72 | runtime_call全体 | 6.994 | 9.254 | 8.202 |
| interpreter | 128×72 | calleeフレーム構築部分 | 3.874 | 5.126 | 4.543 |
| interpreter | 128×72 | 制御フレームstore部分 | 0.050 | 0.067 | 0.151 |
| jit | 128×72 | 基本ブロック検索全体 | 10.217 | 11.468 | 3.400 |
| jit | 128×72 | runtime_call全体 | 7.356 | 8.257 | 8.627 |
| jit | 128×72 | calleeフレーム構築部分 | 3.737 | 4.194 | 4.382 |
| jit | 128×72 | 制御フレームstore部分 | 0.000 | 0.000 | 0.000 |
| jit | 128×72 | JIT lookup | 6.714 | 7.536 | 7.428 |
| interpreter | 192×108 | 基本ブロック検索全体 | 35.075 | 20.460 | 5.191 |
| interpreter | 192×108 | runtime_call全体 | 19.256 | 11.232 | 10.019 |
| interpreter | 192×108 | calleeフレーム構築部分 | 9.653 | 5.631 | 5.022 |
| interpreter | 192×108 | 制御フレームstore部分 | 0.253 | 0.147 | 0.338 |
| jit | 192×108 | 基本ブロック検索全体 | 22.659 | 11.434 | 3.353 |
| jit | 192×108 | runtime_call全体 | 16.075 | 8.112 | 8.364 |
| jit | 192×108 | calleeフレーム構築部分 | 8.065 | 4.070 | 4.196 |
| jit | 192×108 | 制御フレームstore部分 | 0.110 | 0.055 | 0.147 |
| jit | 192×108 | JIT lookup | 13.222 | 6.672 | 6.364 |
| interpreter | 256×144 | 基本ブロック検索全体 | 63.419 | 20.785 | 5.269 |
| interpreter | 256×144 | runtime_call全体 | 31.760 | 10.409 | 9.282 |
| interpreter | 256×144 | calleeフレーム構築部分 | 19.730 | 6.467 | 5.766 |
| interpreter | 256×144 | 制御フレームstore部分 | 0.403 | 0.132 | 0.303 |
| jit | 256×144 | 基本ブロック検索全体 | 41.778 | 11.732 | 3.471 |
| jit | 256×144 | runtime_call全体 | 28.296 | 7.946 | 8.269 |
| jit | 256×144 | calleeフレーム構築部分 | 14.353 | 4.031 | 4.195 |
| jit | 256×144 | 制御フレームstore部分 | 0.051 | 0.014 | 0.039 |
| jit | 256×144 | JIT lookup | 25.118 | 7.053 | 6.728 |
| interpreter | 320×180 | 基本ブロック検索全体 | 102.157 | 21.833 | 5.428 |
| interpreter | 320×180 | runtime_call全体 | 40.834 | 8.727 | 7.626 |
| interpreter | 320×180 | calleeフレーム構築部分 | 21.271 | 4.546 | 3.972 |
| interpreter | 320×180 | 制御フレームstore部分 | 0.585 | 0.125 | 0.282 |
| jit | 320×180 | 基本ブロック検索全体 | 59.760 | 10.814 | 3.175 |
| jit | 320×180 | runtime_call全体 | 44.294 | 8.015 | 8.272 |
| jit | 320×180 | calleeフレーム構築部分 | 23.786 | 4.304 | 4.442 |
| jit | 320×180 | 制御フレームstore部分 | 0.440 | 0.080 | 0.212 |
| jit | 320×180 | JIT lookup | 42.091 | 7.617 | 7.050 |

サンプルが0のセルは分解能不足であり、処理費用が0という証拠ではない。全カテゴリと各プロセスの割合は`step1/costs.csv`、`step1/summary.json`、アドレスごとの根拠は`step1/profile_*_samples.csv`と`step1/symbol_mapping.json`にある。

## 診断オーバーヘッド

既存のnative_dispatch_statsはCollectStatsの有効化でdispatch経路が変わるため、比較用の計数には使わなかった。FB_CONF_JIT_BLOCK_COUNTERSはブロック別回数の機構であり、要求されたフレーム構築回数を直接取れない。診断専用`FB_COST_DIAGNOSTIC`で12個のカウンタを追加し、CollectStats=falseと通常の境界フラグを維持した。両ライブラリのカウンタを合計する。計測後に追加を復元し、105ファイルのソースSHAと通常ライブラリSHAの一致を確認した。カウンタpatchは結果として保存した。

| 実行系 | 解像度 | 通常ms | 診断ms | 観測差% | 通常プロセス中央値min..max ms | 診断プロセス中央値min..max ms |
|---|---|---:|---:|---:|---|---|
| interpreter | 64×36 | 19.318 | 19.333 | +0.076 | 19.291..21.078 | 19.022..19.640 |
| jit | 64×36 | 22.615 | 22.685 | +0.309 | 22.542..22.792 | 22.567..23.693 |
| interpreter | 128×72 | 75.575 | 75.503 | -0.094 | 74.883..81.245 | 75.359..75.821 |
| jit | 128×72 | 89.094 | 89.210 | +0.130 | 89.061..94.057 | 89.078..95.340 |
| interpreter | 192×108 | 171.433 | 169.790 | -0.958 | 168.350..178.679 | 168.269..171.543 |
| jit | 192×108 | 198.169 | 199.024 | +0.431 | 197.857..208.229 | 198.797..201.627 |
| interpreter | 256×144 | 305.118 | 298.807 | -2.068 | 297.784..486.739 | 297.803..307.324 |
| jit | 256×144 | 356.115 | 350.734 | -1.511 | 350.769..364.904 | 350.683..358.034 |
| interpreter | 320×180 | 467.904 | 470.052 | +0.459 | 467.001..476.833 | 465.880..471.969 |
| jit | 320×180 | 552.618 | 551.271 | -0.244 | 549.964..555.379 | 549.719..593.779 |

差にはホストノイズとコード配置の変化が含まれる。負値はカウンタが高速化したという証拠ではない。通常と診断の時間は分離し、診断時間を性能値へ採用していない。uProfの観測差も`step1/timing.csv`へ保存した。Step 1の30通常・30診断・30profileプロセスの全出力は既存のhash oracleと一致した。

## Step 2〜4の差分

- Step 2: 型解析でpush/pop/trimに追従して最大の生32bit語高を求め、FunctionとABI viewに保存した。Nativeのruntime_callは引数とcall_indirectのselectorを除いたSPで一度検査する。トップレベルとPython参照の関数入口も同じ基準で検査する。失敗は明示的な戻り値からOPERAND_STACK_CAPACITYへ伝播し、内部AssertionErrorは捕捉しない。ABI viewは末尾paddingを使い104byteのままである。JIT途中入口のオペランド容量分岐は削除した。制御フレーム容量のフォールバックは維持した。
- Step 2/4: native_wasm_operations.cxxの命令内OPERAND_STACK_CAPACITY返却32箇所を削除し、Nativeの関数入口に1箇所追加した。pop/pop_u64の下限とpush/push_u64の上限の4分岐は契約assertへ変更した。直接の下限17箇所、drop/selectの2箇所、残る命令内の容量失敗12箇所、conversionの既定値1箇所が32返却箇所の内訳である。assertはreleaseのNDEBUGで消える。
- Step 3: 既存のDebugger型引数でblock_step_aspect用ハンドラ表を選ぶ。要求境界停止の毎命令分岐はこの表だけに生成し、通常call表から除外した。Debuggerのテンプレート引数をさらに増やす変更は行っていない。
- Step 4: dispatch_nextのコード末尾、control entryとmatch_end、drop/select幅、branchの静的アリティと復帰高さ、branch_fixupの範囲をassert契約へ変更した。WASM実行のunreachable命令のtrapは維持した。
- Step 4: can_chain_from/toの欠損metadataは製品でassertとした。コードを持たないQA fixtureの既定値の違いは、同深度のforward chainの適格性と深度変更のBR証明という異なる判定を表すため、QA専用分岐へ限定して維持した。同じ意味でないtrue/falseを一律に揃えていない。
- Step 4: conversionのfalseは、容量の失敗経路を除いた後もNaN/範囲外に対してINVALID_CONVERSIONを設定する。trap_code!=0をassertし、capacityへの既定値置換を削除した。非飽和truncのWASM trapを維持した。

ホストELFのllvm-size結果は.text等のtext欄221173→202710 byte（-18463）、data 4912→7056 byte（+2144）、bss 2769→2721 byte（-48）である。data増加は追加の268 pointerのstep専用表に対応する。ホストの再配置欄をMCUの物理RAM消費と同一視しない。

### 残した明示的なtrap

| 分類 | 残す実行時失敗 |
|---|---|
| 制御 | unreachableの実行 |
| 整数 | 除算・剰余のゼロ除算、符号付き除算オーバーフロー |
| 数値変換 | 非飽和truncのNaN・範囲外 |
| メモリ | load/store、memory.copy/fill、セグメント初期化の範囲外。host/VMMIO境界での実行時エラー |
| 間接呼出し・table | 範囲外、未初期化/null、型不一致、table操作と要素セグメント初期化の境界エラー |
| 資源上限 | 呼出し深さ、local領域、control frame、関数入口のoperand領域 |

容量不足時は入口で本体を拒否するため、以前の途中のglobal更新や、先に到達していたunreachableが観測されない。`step2_priority_counterexample.json`は変更前に実測したunreachable=9と静的な必要量66語を記録する。追加テストは同じ型の入力に対して変更後にcapacity=4とglobal未更新を直接確認する。容量が足りる場合は7種類のWASM trap×Python/Nativeの14ケースでtrapコードと後続global更新の不在を確認した。

## 最終AO-Bench

代表値は3プロセスの中央値の中央値である。計時区間はNativeInterpreter.call()またはwasm_runtime_call_wasm()とし、ロード、初期化、出力hash計算を含めない。

| 解像度 | Step 0直後Interpreter ms | 最終Interpreter ms | 時間差% | 最終JIT ms | 最終WAMR Fast ms | Interpreter/WAMR |
|---|---:|---:|---:|---:|---:|---:|
| 64×36 | 19.318 | 19.688 | +1.916 | 23.040 | 4.809 | 4.094 |
| 128×72 | 75.575 | 74.324 | -1.654 | 87.105 | 19.394 | 3.832 |
| 192×108 | 171.433 | 161.723 | -5.664 | 194.998 | 43.693 | 3.701 |
| 256×144 | 305.118 | 280.272 | -8.143 | 339.050 | 76.822 | 3.648 |
| 320×180 | 467.904 | 443.025 | -5.317 | 533.076 | 118.918 | 3.725 |

| 解像度 | 出力SHA-256（全実行系一致） |
|---|---|
| 64×36 | `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567` |
| 128×72 | `e9f20b8f0414f7406aa2f3ea96738e1c7755d0148090bf72f8703cffd3d374a4` |
| 192×108 | `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1` |
| 256×144 | `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4` |
| 320×180 | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

5解像度×3実行系×3プロセス=45プロセス、各7回の315計時値を保存した。最終320×180のInterpreterプロセス中央値は437.812..446.205 msである。ソースとライブラリの計測前後SHA一致を確認した。途中でPython契約の戻り値形式を変更した実行はSHA安定性検査で失敗したため除外し、最終ソースを固定して再実行した。失敗出力は`aobench_rejected_source_changed.log`へ保存した。

## 検証結果と制限

- Interpreter、CPS、Reader、JIT runtime、JIT differentialの関連5ファイル: 276 passed。`tests_final.log`に保存した。
- ASan/UBSanで容量、混在幅、trapを確認した関連ケース: 46 passed、100 deselected。Pythonホストのリーク検査だけdetect_leaks=0とした。`tests_sanitized_final.log`に保存した。
- interpreter_stack_model.py: 10性質の通常検証とguards=False変異検査の双方が期待通りとなった。幅2性質、境界2性質、最大高1性質、入口予約2性質、既存スタック独立・結果保持3性質である。
- spec-integrator SourceFacade: pysimの9ファイル、C++の7ファイル、formalの1ファイルの静的検査にエラーなし。正本設定を読み、run_testsだけをメモリ上で無効化して全30 suiteの再実行を防ぎ、上記の関連pytestに置き換えた。設定ファイルは変更していない。`static_check.py`と`static_check_final.log`に保存した。
- 指定6文書のcheck-doc: 品質ゲートは失敗。200件の未定義キーワード参照と28件の検証義務エラー、14警告が出た。限定した文書集合では他文書の定義を解決できない参照を含む。Format/Hierarchy/Formal/WITは通過し、verification matrixも通過した。有料LLM監査は指示がないため実行していない。文書品質ゲート全体の通過は未完了である。詳細は`doc_check_report.md`と`doc_check.log`に保存した。
- 初回の古いQAライブラリ、QA fixtureのframe_depth設定方法、Pythonの静的規約の失敗出力も保存した。修正・再ビルド後の結果だけを上の合格値として採用した。
- 関数別フレーム構築の新たな事前計算、基本ブロック検索回数の削減、個々のStepの単独A/B、MCU物理予算確認は今回の実装・実測では行っていない。

## 次の作業の提案

測定事実: 320×180のStep 0直後で、検索全体21.833%に対しruntime_call全体8.727%、callee構築部分4.546%だった。miss後の実検索だけでも10.771%だった。制御フレームstoreは0.125%の標本にとどまり、大きな費用という仮説を裏付けなかった。ただしstoreへのソース行帰属は粗いため、制御ハンドラ全体2.648%と区別する。

提案・推測: 次はブロック検索回数を削減する候補を優先して、ブロック後続情報の再利用を小さな比較変更で測る。cache大型化はRAM/ROMと最悪時検索を別途評価する。関数単位のフレーム構築事前計算は次点とする。少なくとも単純な構築部分4.546%だけを消してもWAMRとの差全体は埋まらない。今回の入口検査と境界分離による観測短縮を、検索やフレーム構築の個別短縮量として帰属しない。

## 再実行と保存物

実行した主要コマンドは`commands.md`へまとめた。Step 1の各プロセスJSONには完全なtasksetコマンド、profileのcommands.jsonにはuProf collect/reportコマンドがある。`step1/sources_step0.tar.gz`にはStep 0時点とSHAが一致する104ソースを保存した。仕様Markdown 1ファイルだけは元byte列を保存していないためarchive対象外とした。計時とアドレス対応に使った通常・シンボルライブラリは`step1/binaries_step0.tar.gz`へ保存した。現在のソースへ過去の診断patchを直接適用しない。独立した作業コピーにStep 0 source archiveを展開し、診断を復元・通常ビルドへ戻した後で計時する。生のuProf sessionはskillの方針通り/tmpに置き、サンプルCSV、シンボル対応、集計、元レポートをresultsに保存した。

最終build識別子は以下のSHA-256である。ソース別SHAとコンパイラ版、WAMR revision、全flagは`aobench_final.json`にある。結果ファイルのSHAは`SHA256SUMS`にある。

- `/home/t-matsu/workspace/mysrc/fireball/experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so`: `b42fedf3b6c668c53b52148af3c27fd5c9c7a0aad24a1fcbafcffe0050fb97c8`
- `/home/t-matsu/workspace/mysrc/fireball/experiments/pysim/tier3_plugins/jit/libtrace_compiler.so`: `8bf08e8d1c079434211a138d143dca731fddfb7f668dc73b68aadc3d6abe66bb`
- `/tmp/fireball-wamr-fast-o3/libiwasm.so`: `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960`
