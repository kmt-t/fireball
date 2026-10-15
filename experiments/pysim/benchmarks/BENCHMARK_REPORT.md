# PySIM ベンチマークレポート（2026-10-15）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {MemoryBoundaryCheck} {JIT_LazyChaining} {JIT_BackedgeYield} -->

本書は2026-10-15に実施した最新測定だけを記録する。測定値はx86_64上の参照実装による。ARMv8-Mの速度、物理RAM、ROM適合性を示さない。

測定対象の基準HEADは`9d86a4993677c2fcf67d0aa32f789fb087f1af29`、branchは`req-031-call-cps`である。実装作業中のworktreeでrelease native libraryをビルドして測定した。ビルド条件はClang C++23 `-O3 -DNDEBUG -flto=thin -fuse-ld=lld`である。ホストはAMD Ryzen 5 5500GT、Linux 7.0.0-38-generic x86_64、Python 3.13.15である。

全サンプル、プロセスごとの中央値、実行条件、library hash、検証結果は[最新REQ-031計測JSON](results/latest/req031_call_cps.json)に記録した。

## 統合ベンチマーク

`experiments/pysim/benchmarks/run_all.py`は6セクションを31.79秒で実行し、PASSした。この統合実行はCPU affinityを固定していない。

| セクション | 最新結果 |
| :--- | :--- |
| Linear memory | raw bytearray 32-bit R/W 9.16 M ops/s、8-bit R/W 27.14 M ops/s、linear RAM 9.33 MB/s |
| vMMIO | TLB hit 0.85 M ops/s、miss walk 0.78 M ops/s、hit/walk 1.09x |
| JIT compile | 30,318 traces/s、8,246 ns/WASM opcode |
| 算術ループ 100,000回 | Python 3,674.65 ms、NativeInterpreter 19.10 ms、JIT 21.97 ms。結果`704,982,704`は一致し、JITはNativeInterpreterより1.15x遅い |
| JIT cache | 小・中working setのhit rate 100.00%、大working set 99.93%。退役traceのchain解除はPASS |
| Card aging | hot only 60 ms、agingなし190 ms、U=1/O=4で183 ms、既定U=2/O=8で183 ms、U=8/O=32で186 ms |
| AO-Bench 32×16（統合runner） | NativeInterpreter 8.97 ms、Hybrid JIT 11.65 ms。出力一致、JITは1.30x遅い |

## REQ-031 call dispatch と AO-Bench

call dispatchはCPU 2固定、release、条件ごとに独立3プロセス、各プロセスで7回計測した。JIT条件はwarmup後にtraceをコンパイルし、計時前にcompiled状態でもう一度実行した。時間範囲は3プロセスの中央値のmin/maxである。初期化、WASM parse、module登録、JIT compile時間は計時に含めない。

| workload | iteration | NativeInterpreter ms: 中央値（min–max） | JIT ms: 中央値（min–max） | JITの差 |
| :--- | ---: | ---: | ---: | ---: |
| baseline | 3,000 | 0.674600（0.674420–0.697443） | 0.697172（0.694177–0.762845） | +3.35% |
| baseline | 10,000 | 2.028259（2.019603–2.044770） | 2.181646（2.103008–2.201122） | +7.56% |
| direct call | 3,000 | 0.755862（0.751624–0.761603） | 0.824811（0.822928–0.846562） | +9.12% |
| direct call | 10,000 | 2.281432（2.279769–2.330143） | 2.594377（2.547369–2.630925） | +13.72% |
| indirect call | 3,000 | 0.754610（0.752756–0.776921） | 0.845910（0.830441–0.849628） | +12.10% |
| indirect call | 10,000 | 2.295128（2.285690–2.384395） | 2.612110（2.575300–2.647035） | +13.81% |

AO-BenchもCPU 2固定、release、解像度とengineごとに独立3プロセス、各プロセスで3 warmupと7回の計時を行った。時間範囲はプロセスごとの中央値のmin/maxである。

| 解像度 | NativeInterpreter ms: 中央値（min–max） | JIT ms: 中央値（min–max） | JITの差 | 出力SHA-256 |
| :--- | ---: | ---: | ---: | :--- |
| 32×16 | 4.010151（4.005713–4.055366） | 4.902248（4.676546–4.976868） | +22.25% | `5cd38f158314486141e032776845b8eabba32e3be1699b3b382ba92c80bba47e` |
| 320×180 | 397.234553（393.383138–533.062534） | 482.873295（481.140848–695.754028） | +21.56% | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

全call dispatch条件とAO-Bench条件でJITはNativeInterpreterより遅かった。この結果から性能改善を主張しない。特に320×180ではプロセス間の時間幅が大きいため、中央値だけで差の原因を説明しない。

## 検証

| 検証 | 結果 |
| :--- | :--- |
| PySIM単体テスト | 30/30グループPASS |
| JIT runtime test | 130/130 PASS |
| 結合テスト | 12/12シナリオ、5/5グループPASS |
| WASM workload | 3/3 suite PASS。Core Spec 37/37ファイル、15,230 assertions、skip 0 |
| 最適化モード比較 | 16/16 PASS。通常、`-O`、`-OO`を比較 |
| call出口の変異negative control | body endをブロック末尾へずらす変異で、両call形式の出口位置テストが失敗することを確認 |
| 変更ファイルのsource gate | PASS、warning 0 |
| document gate | PASS、error 0、warning 44 |

全PySIM対象の`check-src -g pysim --optimized`では、変更対象外の[`scheduler.py`](../../tier1_core/scheduler.py:1168)にある既存`PY-FORBIDDEN-NONE-DEFAULT-FILL`違反が1件報告された。変更したファイルに絞ったsource gateはPASSした。GDB/TCPテストはsandbox内ではsocket作成を拒否されたが、権限付きの全体再実行ではPASSした。

LLM監査は依頼どおり実施していない。document gateの44 warningには保存済みLLM判定の陳腐化・対象漏れが含まれるが、エラーは0件である。
