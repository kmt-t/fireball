# BasicBlock indexによるInterpreter dispatchとJIT連結の計測

2026-10-13に実施した依頼Aの実装後計測である。製品経路ではInterpreterのPC→BasicBlock検索を取り除き、execution contextと注釈済み辺indexからBasicBlockを直接参照する。JITのchain successorと候補受け入れも、境界metadataのBasicBlock indexを使う。git commitは行っていない。

## 結果

AO-Bench全5解像度でInterpreter、JIT、WAMR Fastの出力hashが一致した。依頼A直前の2026-10-12計測と比べ、320×180のNativeInterpreter中央値は475.138 msから411.346 msへ13.43%短くなった。JITは576.598 msから484.947 msへ15.90%短くなった。320×180でInterpreterはWAMR Fastの3.445倍の時間を要するため、PC検索の除去後も実行時間の差は残る。

以下の表は各独立プロセス内7 timed callsの中央値を3プロセスで集計した代表値と、3つのプロセス中央値の範囲である。比較前値は[`aobench_interpreter_jit_wamr_20261012_current_baseline.json`](../aobench_interpreter_jit_wamr_20261012_current_baseline.json)、変更後の全サンプルは[`aobench.json`](aobench.json)にある。

| 解像度 | Interpreter 前→後 ms（変化） | JIT 前→後 ms（変化） | WAMR Fast 前→後 ms（変化） | 変更後 Interpreter/JIT/WAMR 範囲 ms | 出力SHA-256 |
| :--- | ---: | ---: | ---: | :--- | :--- |
| 64×36 | 18.959→16.931 (-10.70%) | 22.541→19.288 (-14.43%) | 4.704→4.772 (+1.43%) | 16.832–17.326 / 19.229–20.083 / 4.668–4.816 | `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567` |
| 128×72 | 78.323→65.841 (-15.94%) | 90.426→78.499 (-13.19%) | 19.662→18.990 (-3.42%) | 65.481–66.034 / 77.922–78.630 / 18.855–19.258 | `e9f20b8f0414f7406aa2f3ea96738e1c7755d0148090bf72f8703cffd3d374a4` |
| 192×108 | 170.001→147.618 (-13.17%) | 204.053→175.271 (-14.11%) | 42.557→42.956 (+0.94%) | 146.161–148.149 / 172.880–177.522 / 42.699–43.397 | `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1` |
| 256×144 | 303.002→266.417 (-12.07%) | 360.994→314.357 (-12.92%) | 78.174→75.957 (-2.84%) | 262.693–271.995 / 308.171–314.520 / 74.776–77.509 | `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4` |
| 320×180 | 475.138→411.346 (-13.43%) | 576.598→484.947 (-15.90%) | 119.845→119.411 (-0.36%) | 411.205–412.866 / 481.720–488.540 / 119.053–119.860 | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

前後比較は独立した計測日の中央値の差であり、ランダム化した同一ソースA/Bではない。Interpreter/JITの短縮は観測事実だが、その全量をBasicBlock index化だけの因果効果とは断定しない。WAMRの変動はホストノイズの目安として併記した。

## 検索回数と処理時間

変更前の動的回数は、直近に保存されている診断である[`interpreter_contracts_v3_20261012/step1/counts.csv`](../interpreter_contracts_v3_20261012/step1/counts.csv)と`REPORT.md`から転記した。これは契約整理第2弾のStep 2〜4を適用する前の診断で、依頼A直前のソースを別途instrumentして取り直した値ではない。Step 2〜4はAO-Benchで通る通常分岐の遷移方法を変更していないが、この前提を含む既存値として扱う。

各値はAO-Bench 1呼出しあたりである。block searchはInterpreter/JIT両実行で同数、外側dispatch反復あたり1回だった。JIT lookupは7 timed callを各3独立プロセスで測ったQA診断値の平均である。

| 解像度 | 変更前 block search | cache hit | cache miss | Interpreter外側loop | JIT lookup（変更前平均） |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 745,491 | 284,672 | 460,819 | 745,491 | 222,807.000 |
| 128×72 | 3,005,146 | 1,146,574 | 1,858,572 | 3,005,146 | 903,902.143 |
| 192×108 | 6,756,885 | 2,576,769 | 4,180,116 | 6,756,885 | 2,077,694.857 |
| 256×144 | 12,036,303 | 4,591,553 | 7,444,750 | 12,036,303 | 3,733,500.857 |
| 320×180 | 18,821,697 | 7,178,464 | 11,643,233 | 18,821,697 | 5,970,030.143 |

変更後のNativeInterpreter製品dispatchには`find_cached_block_index`の呼出しがなく、`current_block_index`でBasicBlock配列を参照する。したがって製品dispatchでのPC→BasicBlock検索回数は構造上0である。境界停止APIはPythonとの境界比較用にPCを保持する。

JIT製品の`lookup_cached_trace(block_index, pc)`はLoader BasicBlock上の`jit_cache_reference`を読み、参照先traceのPCとblock indexを照合する。resident successorの連結と候補受け入れもindexとPCを渡す。製品構成は`Measurement=void`であり、`find_cached_trace_by_pc`へ進むQA fixture用分岐はcompile-timeで除かれるため、製品経路における同関数呼出しは構造上0である。これは変更後の動的カウンター値ではない。indexによるJIT参照やcache missを含むJIT lookup全体の呼出し数は、今回の計時ビルドでは別カウンターで数えていない。

変更前の検索時間は通常時間の中央値とAMD uProf Hotspotsのサンプル比から推定した。1回あたり値は総推定時間を診断回数で割った償却平均であり、各関数呼出しを個別に時計で測った値ではない。

| 解像度 | Interpreter検索 ms / 全体比 / ns/検索 | JIT検索 ms / 全体比 / ns/検索 | JIT PC lookup ms / 全体比 / ns/lookup |
| :--- | :--- | :--- | :--- |
| 64×36 | 3.030 / 15.686% / 4.065 | 2.456 / 10.860% / 3.294 | 1.228 / 5.430% / 5.511 |
| 128×72 | 15.195 / 20.107% / 5.056 | 10.217 / 11.468% / 3.400 | 6.714 / 7.536% / 7.428 |
| 192×108 | 35.075 / 20.460% / 5.191 | 22.659 / 11.434% / 3.353 | 13.222 / 6.672% / 6.364 |
| 256×144 | 63.419 / 20.785% / 5.269 | 41.778 / 11.732% / 3.471 | 25.118 / 7.053% / 6.728 |
| 320×180 | 102.157 / 21.833% / 5.428 | 59.760 / 10.814% / 3.175 | 42.091 / 7.617% / 7.050 |

変更後の直接index参照時間、index lookup単体の処理時間、およびそのAO-Bench全体比は単独で採取していない。全体時間の前後比較は上表のAO-Bench測定に限る。命令種別（`br`、`br_if`、`br_table`、`if`、`else`、`loop`、fall-through、call/return）ごとの遷移回数と処理時間も今回の既存診断では分離されていない。

JITのchain link実行回数は独立のカウンターがないため、前後の実測回数を提示できない。変更後の`link_resident_successor`はsuccessor indexとPCでlookupし、受け入れも両値を検査する。テスト`test_jitr_trace_exit_reuses_loader_successor_block_index_and_pc`と`test_jitr_native_header_chain_executes_successor_body_once`はこの経路の値と実行結果を検証する。旧PC lookup回数の変更前値は上記JIT lookup列に対応する診断値、変更後は製品経路0（構造上）であり、QA metadataなしfixtureだけは`lower_bound`を残す。

## メモリ増分

AO-Bench WASM（6関数、107 BasicBlock）のLoader arenaで計測した。詳細は[`memory_impact.json`](memory_impact.json)、再計測スクリプトは[`measure_loader_memory.py`](measure_loader_memory.py)にある。

| 項目 | 変更前 | 変更後 | 増加 | 増加率 |
| :--- | ---: | ---: | ---: | ---: |
| 注釈プール | 2,456 B | 2,944 B | 488 B | 19.87% |
| BasicBlock metadata | 2,996 B | 3,852 B | 856 B | 28.57% |
| Loader arena | 25,756 B | 27,100 B | 1,344 B | 5.22% |
| JIT境界metadata | 24 B | 24 B | 0 B | 0% |

Loader arenaは131,072 B容量の20.68%を使用し、増分は容量の1.03%にあたる。別枠のruntime ABI増分はExecutionContext +8 B、CallFrame +8 B/frame、32-frame CallStack +256 B、FunctionExecutionView +8 B/function（AO-Benchの6関数なら+48 B）である。これらはLoader arena値に含まれない。測定はx86_64 PySIMの論理アリーナと構造体サイズであり、Cortex-M33物理RAM予算の適合証明ではない。

## 確認できた事実と推測

確認できた事実は、Interpreter製品dispatchにPCからblock indexを探す呼出しがなくなったこと、JITの製品chain lookupがLoader successor indexとPCの両方を照合すること、footer metadataの24-byteサイズが変わらないこと、AO-Benchの出力hashが変更前後および3実行系で一致したことである。変更後のAO-Bench中央値は全解像度でInterpreterとJITとも変更前より小さかった。

推測として、検索を行わない経路とAO-Bench時間の短縮には関連がある。ただし、非交互の別日計測であり、変更後に検索関数だけを差し戻すA/Bはしていない。改善率をこの変更単独の因果効果としては扱わない。WAMR時間が解像度により-3.42%から+1.43%変わったことも比較にはホスト変動が含まれることを示す。

## 検証

- 関連pytest 316 passed。Interpreter、CPS、WASM reader/ABI、JIT runtime、WASM Core Spec trap testsを実行した。WASM trap回帰とJIT chain successorのテストを含む。
- Interpreter/JITの関連形式モデルは通常検証と`guards=False`変異検査を実行し、期待結果を確認した。
- 対象C++静的チェックは0 warning、formalソース静的チェックはpass。
- `git diff --check`はpass。`check-doc.sh`は6文書を指定して実行したが、全体で283 errors（traceability 258、obligation 25）、16 warningsとなり失敗した。Format、Hierarchy、Formal、WIT、Evidence、Consistency、SemanticTopic gateはpass。TraceabilityとObligation gateがfailした。出力は[`doc_check_report.md`](doc_check_report.md)に保存した。エラーの中心は文書グラフ内の未定義キーワードと未充足の`VERIFY_LLM`義務であり、この依頼の文書だけに閉じた問題として解消できないため、全体ゲートは未通過として残す。

## 未完了

- 制御移動を命令種別ごとに分けた変更前後の動的カウントと時間。
- `link_resident_successor`、chain候補受け入れ、新しいindex lookupを別々に数えた変更前後の回数。
- 変更後のindex参照単体の時間、検索単体時間と同じ方法で比較できるサンプル。
- ARMv8-Mの物理RAMおよび性能測定。

これらは既存QA診断の計数範囲外であり、製品計時ビルドへinstrumentationを混ぜない方針を維持した。必要なら診断専用計数を追加して別測定する。

## buildと結果の識別子

Release buildはClang 21.1.8、C++23、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`を使う。WAMR Fastのcommitは`f5f57c09aee623436f5fb87a90798fdd2cdf39fd`。ライブラリとrunnerのSHA-256は[`aobench.json`](aobench.json)に全ソース別SHA-256とともに保存した。

| 対象 | SHA-256 |
| :--- | :--- |
| NativeInterpreter shared library | `f778fcaeabcf03eea632333837620605af3d100244028457ed2da19998877251` |
| JIT shared library | `b590c0b5cfdf68187c586d139eb1ee6a951e4507359e3086a666c02226beae54` |
| WAMR Fast shared library | `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960` |
| AO-Bench結果JSON | `3d2e1a12614b9ace643007a8bd45581347423a75816f8df84e794827d627cb25` |
| Loader計測スクリプト | `073d6fb7932aa2689623a35a1006d270688d8d9b5dcbee05ea38fab0eb7b1542` |

再実行コマンドは[`commands.md`](commands.md)、主要結果ファイルのSHA-256は[`SHA256SUMS`](SHA256SUMS)を参照。
