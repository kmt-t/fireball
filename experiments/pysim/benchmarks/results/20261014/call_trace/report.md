# REQ-019 呼出しとトレース計測結果

計測対象は `bench_call_dispatch.py` の同一WATである。対象HEADは `8875be18f53ab460b55c8acb600d859afb8a7d47`、開始時の作業ツリーはclean、ホストはAMD Ryzen 5 5500GT、x86_64、CPU 2固定である。計測ごとに1回の準備実行後7サンプルを取り、3つの独立プロセスを実行した。表の中央値はプロセス中央値3個の中央値、括弧内はプロセス中央値のmin–maxである。個別サンプルは [`measurement.json`](measurement.json) に保存した。

## 同一ゲストの比較

10,000反復の期待値は50,005,000、3,000反復の期待値は4,501,500であり、Fireball、WAMR、JITの全準備・計時呼び出しで一致した。WAMRの時間区間は `wasm_runtime_call_wasm` だけで、ロードと初期化を含まない。Fireballは `NativeInterpreter.call()` だけを計時する。

| 反復 | ビルド | baseline ms | direct ms | indirect ms |
| :--- | :--- | ---: | ---: | ---: |
| 10,000 | Fireball A-1 assert有効 | 2.395 (2.383–2.399) | 2.724 (2.713–2.811) | 2.843 (2.740–2.892) |
| 10,000 | Fireball A-2 NDEBUG | 1.993 (1.987–2.084) | 2.243 (2.212–2.337) | 2.205 (2.200–2.221) |
| 10,000 | WAMR Fast Interpreter | 0.0588 (0.0582–0.0590) | 0.1472 (0.1439–0.1483) | 0.1714 (0.1710–0.1756) |
| 3,000 | Fireball A-1 assert有効 | 0.782 (0.771–0.783) | 0.879 (0.844–0.911) | 0.907 (0.865–0.910) |
| 3,000 | Fireball A-2 NDEBUG | 0.658 (0.650–0.660) | 0.708 (0.704–0.712) | 0.714 (0.711–0.756) |
| 3,000 | WAMR Fast Interpreter | 0.01807 (0.01806–0.01811) | 0.0443 (0.0441–0.0490) | 0.0531 (0.0520–0.0535) |

NDEBUGはassert有効buildに比べ、baselineを15.8–16.8%、directを17.7–19.5%、indirectを21.4–22.4%短縮した。従ってassert有効の従来値とWAMR releaseの比較にはbuild条件差があり、FireballをNDEBUGに揃えると、Fireball/WAMRの全呼出し時間比はbaselineで33.9–36.4倍、directで15.2–16.0倍、indirectで12.9–13.4倍となる。

## 命令費用と呼出し経路の追加分

baselineはWATとWABT逆アセンブルから静的21命令（暗黙のfunction endを含む）を確認した。実行時はループ内15命令とループ判定、関数終端から `15*n + 8` 命令となる。対象の動的命令数は10,000反復で150,008、3,000反復で45,008である。内訳と数え方は [`measurement.json`](measurement.json) に示す。

| 反復 | baseline ns/WASM命令 | direct−baseline ns/leaf call | indirect−baseline ns/leaf call |
| :--- | ---: | ---: | ---: |
| 10,000 A-1 | 15.96 | 32.93 | 44.77 |
| 10,000 A-2 | 13.28 | 25.01 | 21.22 |
| 10,000 WAMR | 0.392 | 8.84 | 11.26 |
| 3,000 A-1 | 17.37 | 32.52 | 41.90 |
| 3,000 A-2 | 14.62 | 16.67 | 18.43 |
| 3,000 WAMR | 0.402 | 8.73 | 11.68 |

呼出し経路の差分には、追加されたゲスト命令とcallee本体も含まれるため、machine call命令だけの純粋な費用ではない。Fireballのreleaseではbaselineの1ループ約199–219 nsに対し、direct/indirect経路の増加は約17–25 ns/反復であり、このゲストでは通常命令のdispatch費用が支配的である。WAMRではbaselineが約5.9–6.0 ns/反復、呼出し経路の追加分が約8.7–11.7 ns/反復となり、callを含む経路の増加分を呼出し側が支配する。A-2 10,000回でdirectとindirectの差の順序が逆転し、3,000回では戻るため、小さな差の順位づけはしない。

## JITの有効・無効

JIT時間はrelease buildで測り、トレース生成を計時外にした。各条件は3独立プロセスである。

| 反復 | workload | JIT無効 ms | JIT有効 ms | JIT有効時の差 |
| :--- | :--- | ---: | ---: | ---: |
| 10,000 | direct | 2.234 (2.103–2.247) | 2.384 (2.372–2.472) | 6.7%遅い |
| 10,000 | indirect | 2.289 (2.271–2.355) | 2.455 (2.446–2.494) | 7.2%遅い |
| 3,000 | direct | 0.743 (0.698–0.753) | 0.771 (0.760–0.807) | 3.8%遅い |
| 3,000 | indirect | 0.734 (0.710–0.742) | 0.801 (0.800–0.806) | 9.2%遅い |

QA専用カウンタでトレース生成を確認し、各ケース3回の診断値は一致した。10,000反復ではdirectに2本、indirectに3本のtraceが常駐した。directは20,001回のtrace body実行、19,845回のdispatcher-to-trace遷移、20,004回の解釈器basic-block dispatch、157回の解釈器への境界復帰だった。leaf呼出しあたりではそれぞれ約2.0001回、1.9845回、2.0004回、0.0157回である。indirectでは30,001回、29,845回、10,004回、157回で、leaf呼出しあたり約3.0001回、2.9845回、1.0004回、0.0157回である。3,000反復の値は [`diagnostics.json`](diagnostics.json) に保持する。

「解釈器への境界復帰」は既存の `RuntimeStatsEngine.stat_trace_exits_to_interp` の値であり、全内部trace終端辺を数えるカウンタではない。157回/10,000回と47回/3,000回は、yieldしきい値64に応じたscheduler boundary handoffを示す。dispatcher-to-trace遷移回数はguest leaf callあたりdirectで約1.985回、indirectで約2.985回となった。JIT有効化はこのゲストで3.8–9.2%遅く、改善効果は測定されなかった。

## 制約と結論

WAMRは固定commit `f5f57c09aee623436f5fb87a90798fdd2cdf39fd` のFast Interpreterである。PySIM側はWasmtime、WAMR側はWABT 1.0.36で同じWATを変換した。WASMファイルのhashは異なるが、関数コードの逆アセンブルは一致し、全出力値も一致した。計測はx86_64 hostのみで、ARMv8-Mの速度を示さない。

測定中に同一ワークスペースで他のbuildやtestは動かしていない。記録したCPU 2 idle率は93.5–97.0%、負荷平均は0.80以下だった。生サンプルに外れ値があるため、結論は3プロセスの中央値で比較し、すべての試行値をJSONに残した。任意項目のuProf関数別内訳は未実施である。

再実行コマンド、build flags、WAMR構成は [`commands.md`](commands.md) を参照する。
