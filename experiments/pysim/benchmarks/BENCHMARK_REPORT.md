# PySIM ベンチマークレポート（2026-10-14）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {MemoryBoundaryCheck} -->

本書は2026-10-14に実行した最新測定だけを記録する。結果はx86_64上の参照実装の値であり、ARMv8-Mの速度、物理RAM、ROM適合性を示さない。測定時のHEADは`37253e695d62bd9d4d9c6ab54eb31f81defe1ec8`で、worktreeは変更中だった。個別測定のsource hash、コンパイラ、ライブラリhashは[証跡一式](results/20261014/req020_phase4/full/)に保存した。

ホストはAMD Ryzen 5 5500GT、Linux 7.0.0-38-generic x86_64、Python 3.13.15、Ubuntu clang 21.1.8である。AO/WAMR、呼出し、0xFC計測はCPU 2、統合・メモリ・資源計測はCPU 3に固定した。通常native buildはClang C++23 `-O3 -flto=thin`、releaseは`-O3 -DNDEBUG -flto=thin`を使った。WAMR比較もrelease buildである。

再実行コマンドと設定は[commands.md](results/20261014/req020_phase4/full/commands.md)を参照する。

## 統合ベンチマーク

登録済み6系統を独立プロセスで3回実行し、全回完走した。総時間の中央値は35.31秒、範囲は34.85–35.33秒である。算術ループのPython、NativeInterpreter、JITの結果は全回`704,982,704`で一致した。JIT cacheの世代hit、退役chain解除、モジュール単位PC検索も全回PASSだった。

| 測定 | 中央値（3回の範囲） |
| :--- | ---: |
| JIT trace compile | 27,221 traces/sec（26,656–29,812） |
| 算術ループ100,000回・Python | 4,076.54 ms（4,020.92–4,077.87） |
| 算術ループ100,000回・NativeInterpreter | 24.18 ms（23.68–27.94） |
| 算術ループ100,000回・Hybrid JIT | 21.66 ms（20.60–21.85） |
| AO-Bench 32×16・Python | 4,830.47 ms（4,742.90–4,902.17） |
| AO-Bench 32×16・NativeInterpreter | 13.07 ms（11.21–14.35） |
| AO-Bench 32×16・Hybrid JIT | 17.96 ms（14.34–18.09） |

AO-Benchのこの入力ではHybrid JITはNativeInterpreterより遅かった。キャッシュ代謝とageingの計数条件は3回とも一致し、全ageing変種で実行を完了した。各回の全指標は[統合run 1](results/20261014/req020_phase4/full/unified_runs/unified_run1.log)、[run 2](results/20261014/req020_phase4/full/unified_runs/unified_run2.log)、[run 3](results/20261014/req020_phase4/full/unified_runs/unified_run3.log)にある。

## release AO-Bench比較

各processでwarmup 3回、計時7回を取り、processごとの中央値の中央値を示す。比較する各engineの出力frameとWASM hashは一致した。

| 解像度 | NativeInterpreter | Hybrid JIT | WAMR |
| :--- | ---: | ---: | ---: |
| 64×36 | 17.388 ms（16.525–19.575） | 19.565 ms（18.888–24.516） | 5.013 ms（4.988–6.855） |
| 128×72 | 70.553 ms（65.366–74.202） | 79.547 ms（78.335–82.661） | 20.630 ms（19.779–23.902） |
| 192×108 | 152.357 ms（148.245–156.541） | 191.400 ms（185.188–194.873） | 46.515 ms（44.146–55.987） |
| 256×144 | 278.835 ms（278.211–288.449） | 321.701 ms（309.142–325.400） | 83.001 ms（81.473–83.609） |
| 320×180 | 432.691 ms（428.750–447.980） | 512.855 ms（504.792–517.793） | 142.168 ms（132.526–147.260） |

追加した32×16測定は各engineを3 process、warmup 3回、計時7回で実行した。中央値はNativeInterpreter 4.229 ms（4.158–4.229）、Hybrid JIT 4.712 ms（4.652–4.790）、WAMR 1.077 ms（1.077–1.098）だった。検証frameは528 byteである。全trialとbuild hashは[AO-Bench/WAMR JSON](results/20261014/req020_phase4/full/aobench_wamr.json)と[32×16 JSON](results/20261014/req020_phase4/full/aobench_32x16_release.json)に保存した。

## Interpreter 0xFCとcall dispatch

0xFC測定は3 processの中央値を集計した。8種類の飽和変換ではNative実装が306.7–398.1 ns/instruction、Python referenceが32.7–38.5 μs/instructionだった。`memory.copy`の長さ16/256/4096 byteではNativeが240/321/2,027 ns、Pythonが41.1/49.7/257.8 μsだった。`memory.fill`はNativeが244/317/1,993 ns、Pythonが36.2/47.3/169.1 μsだった。

vDMA memory.copyのroute別中央値は次のとおりである。

| Route | Native enabled API | Python reference |
| :--- | ---: | ---: |
| linear → dynamic | 34.781 μs/transfer | 62.271 μs/transfer |
| shared → linear | 28.480 μs/transfer | 48.864 μs/transfer |
| dynamic → passthrough | 41.583 μs/transfer | 60.939 μs/transfer |
| passthrough → shared | 34.848 μs/transfer | 61.557 μs/transfer |

call dispatchは10,000 iteration、process内3 repeatsを3 processで測定した。下表はprocess medianの中央値である。

| Engine | Baseline | Direct call | Indirect call |
| :--- | ---: | ---: | ---: |
| Native | 2.516 ms（2.449–3.264） | 2.895 ms（2.805–3.794） | 2.904 ms（2.859–2.919） |
| Python | 463.064 ms（459.941–490.545） | 944.828 ms（920.954–950.518） | 998.632 ms（986.422–1,021.583） |

試行ごとの0xFC結果は[run 1](results/20261014/req020_phase4/full/fc_runs/fc_run1.json)、[run 2](results/20261014/req020_phase4/full/fc_runs/fc_run2.json)、[run 3](results/20261014/req020_phase4/full/fc_runs/fc_run3.json)、call dispatchは[JSON一式](results/20261014/req020_phase4/full/call_dispatch_runs/)にある。

## runtime memoryとresource inventory

Loaderの参照layoutとhost-native ABIによるarena計測では、3回呼出し後の最大値は次のとおりだった。JITの実行region要求はHybrid構成の別領域である。

| Workload | Load後 | Arena最大 | ゲスト論理memory | JIT plugin領域要求 |
| :--- | ---: | ---: | ---: | ---: |
| arithmetic | 390 byte | 3,184 byte | 0 byte | 12,288 byte（Hybrid） |
| indirect calls | 1,263 byte | 4,296 byte | 0 byte | 12,288 byte（Hybrid） |
| kernel suite | 51,911 byte | 56,712 byte | 65,536 byte | 12,288 byte（Hybrid） |
| AO-Bench 32×16 | 5,741 byte | 8,968 byte | 65,536 byte | 12,288 byte（Hybrid） |

表の12,288 byteはHybrid構成のJIT plugin領域要求で、実行コード領域8,192 byteを含む。積算JIT runtime構造体は1,656 byteである。JIT compilerの既知compile call chainは22,656 byteで、scratch 1,588 byteは`compile_one_candidate`のstack frame内に含まれる。この値は全caller/callee/ABI frameを含む上限証明ではない。製品側`inc/`と`src/`は3,704 SLOC（上限20,000）だった。Python/native参照実装の規模とx86_64 ELF値は製品firmwareのRAM/ROM値へ加算しない。

測定詳細は[runtime memory JSON](results/20261014/req020_phase4/full/runtime_memory.json)、[resource inventory JSON](results/20261014/req020_phase4/full/resource_inventory.json)、[製品SLOC JSON](results/20261014/req020_phase4/full/sloc.json)にある。

## REQ-020 backward chaining比較

変更前はcommit `37253e695d62bd9d4d9c6ab54eb31f81defe1ec8`、変更後は同じsource baseline上のworking treeで測定した。transition値はQA counter buildで同一条件を使った。変更後は全workloadで未分類・未観測transitionが0になり、AO-Benchで成功linkも分類された。

| Workload | Runtime transitions | Backedge / self-loop（変更前 → 後） | 変更後の追加確認 |
| :--- | ---: | ---: | :--- |
| arithmetic loop | 200,001 → 1,563（−99.2%） | backedge 100,000 → 1,562 | unclassified 0、unobserved 0 |
| AO-Bench 32×16 | 53,749 → 47,001（−12.6%） | backedge 9,728 → 6,320 | linked 66、unclassified 0、unobserved 0 |
| AO-Bench 320×180 | 6,000,649 → 5,256,290（−12.4%） | backedge 1,062,587 → 686,397 | linked 6,951、unclassified 0、unobserved 0 |
| kernel suite | 456,501 → 203,012（−55.5%） | backedge 70,249 → 43,082; self-loop 188,539 → 2,939 | unclassified 0、unobserved 0 |

別プロセス3回のHybrid JIT算術ループ中央値は21.819 msから20.373 ms（−6.6%）に下がった。AO-BenchのJIT時間は32×16で7.137 msから7.221 ms（+1.2%）、320×180で748.413 msから748.476 ms（ほぼ横ばい）だった。yield return時間の最大観測値は変更前0.039 ms、変更後0.068 msだった。この最大観測値は実時間上限ではない。

変更前後の各sampleは[比較JSON（before）](results/20261014/req020_phase4/comparison_before.json)と[comparison_after.json](results/20261014/req020_phase4/comparison_after.json)、transition counterは[変更前](results/20261014/req020_phase0/measurements.json)と[変更後](results/20261014/req020_phase4/chain_reasons_after.json)に保存した。変更後のcounter測定runnerは[`measure_chain_reasons.py`](results/20261014/req020_phase4/measure_chain_reasons.py)である。
