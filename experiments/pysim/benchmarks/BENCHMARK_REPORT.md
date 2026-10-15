# PySIM ベンチマークレポート（2026-10-14）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {MemoryBoundaryCheck} {JIT_LazyChaining} {JIT_BackedgeYield} -->

本書は今回の最新測定だけを記録する。結果はx86_64上の参照実装の値であり、ARMv8-Mの速度、物理RAM、ROM適合性を示さない。比較用の変更前値はREQ-020 Phase 4の変更中worktreeで測った。測定元のworktreeは変更中で、固定revisionの結果ではない。

ホストはAMD Ryzen 5 5500GT、Linux 7.0.0-38-generic x86_64、Python 3.13.15である。時間測定はCPU 2に固定した。製品native libraryはClang C++23 `-O3 -flto=thin`でビルドした。QA用カウンタはassert有効の別ビルドで測った。

全測定のサンプル、実行回数、実行カウンタは[最新REQ-033計測JSON](results/latest/req033_control_flow.json)に記録した。

## 統合ベンチマーク

`benchmarks/run_all.py`は6セクションすべてを31.64秒で実行し、PASSした。算術ループの結果はPython、NativeInterpreter、JITで一致した。

| セクション | 最新結果 |
| :--- | :--- |
| Linear memory | raw bytearray 32-bit read/write 9.39 M ops/s、linear RAM 10.85 MB/s |
| vMMIO | TLB hit 0.78 M ops/s、miss walk 0.71 M ops/s、hit/walk 1.09x |
| JITと算術ループ | trace compile 28,885 traces/s。100,000回の算術ループはPython 3,702.51 ms、NativeInterpreter 23.27 ms、JIT 20.77 ms。結果は`704,982,704`で一致 |
| JIT cache | 小・中working setのhit rate 100.00%、大working set 99.93%。退役済みtraceのchain解除はPASS |
| Card aging | 条件別実行時間は61、190、188、185、184 ms。cache eviction後も実行を完了 |
| AO-Bench 32×16 | Python 4,450.45 ms、NativeInterpreter 11.42 ms、Hybrid JIT 15.32 ms。出力一致、dispatcher trace transition 40,589 |

## REQ-033の時間比較

AO-Benchは各プロセスでwarmupを3回、計時を7回行った。算術ループとcall dispatchは各3プロセスで測った。表の中央値と範囲はプロセスごとの中央値から集計した。

| Workload | 変更前 ms | 最新 ms | 中央値の差 |
| :--- | ---: | ---: | ---: |
| AO-Bench 32×16 Hybrid JIT | 7.221458（7.200598–7.268907） | 7.341091（7.335121–7.423155） | +1.66% |
| AO-Bench 320×180 Hybrid JIT | 748.476370（742.906094–761.214584） | 769.897949（763.078282–770.099376） | +2.86% |
| 算術ループ 100,000回 Hybrid JIT | 20.373250（19.758500–20.533000） | 20.645462（20.386417–21.650058） | +1.34% |
| `bench_call_dispatch` baseline Native | 2.365295（2.327073–2.395110） | 2.486244（2.477578–2.513024） | +5.11% |
| `bench_call_dispatch` direct Native | 2.801709（2.728803–2.809854） | 2.849383（2.848079–2.897783） | +1.70% |
| `bench_call_dispatch` indirect Native | 2.817208（2.808020–2.857413） | 2.926897（2.897512–2.936425） | +3.89% |

最新の中央値はすべて変更前より遅かった。小さな時間差だけから原因や性能改善を結論づけない。call dispatchのNative測定はNativeInterpreterの計測であり、JIT call tracingの性能ではない。`call`と`call_indirect`を含むblockはREQ-031までJIT候補から外し、Interpreterへ渡す。

## 制御命令とtrace遷移

動的命令数はQA Runtime APIの実呼出し数である。`block`、`loop`、`else`、`end`は構文metadataになり、最新のRuntime API実行数には含まれない。

| Workload | 変更前の動的制御API数 | 最新の動的制御API数 | JIT bodies 変更前 → 最新 |
| :--- | :--- | :--- | :--- |
| 算術ループ 100k | `br` 100,000、`br_if` 100,001 | `br` 100,000、`br_if` 100,001 | 3,128 → 3,126 |
| AO-Bench 32×16 | `block` 519、`br` 9,728、`br_if` 5,522、`else` 2,462、`end` 27,954、`if` 7,564 | `br` 10,125、`br_if` 5,803、`if` 7,948 | 50,065 → 54,647 |
| AO-Bench 320×180 | 変更前のopcode別内訳なし | `br` 1,114,861、`br_if` 616,302、`if` 891,123 | 5,327,605 → 6,013,345 |
| call dispatch baseline | `br` 10,000、`br_if` 10,001 | `br` 10,000、`br_if` 10,001 | 最新314 bodies |
| call dispatch direct | `br_if` 10,001、`end` 10,000 | `br_if` 10,001 | 最新20,001 bodies |
| call dispatch indirect | `br_if` 10,001、`end` 10,000 | `br_if` 10,001 | 最新30,002 bodies |

変更前後のJIT body数は同じRuntimeStatsEngine計数である。AO-Benchではbody数が32×16で9.15%、320×180で12.87%増えた。最新のdispatcher trace transition数は算術ループ1、AO-Bench 32×16で41,818、320×180で4,585,506である。これは変更前のchain runtime transition数1,563、47,001、5,256,290とは異なる境界を数えるため、直接の差分として扱わない。

変更後の旧出口理由カウンタは直接native chainの出口を特定できず、unobservedを記録した。値は算術ループ1,563、AO-Bench 32×16で41,889、320×180で4,593,746だった。この理由別カウンタから制御命令ごとの出口数は算出していない。代わりにJIT body数、dispatcher transition数、interpreter exit数を別々に記録した。

## 検証範囲

フルQAは30/30グループがPASSした。通常sandboxではlocalhost socketを使うGDBの2試験だけが拒否され、権限付き再実行で2件ともPASSした。統合シナリオは12/12、WASM workload suiteは3/3、最適化モード比較は12件PASSした。
