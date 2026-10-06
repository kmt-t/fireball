# PySIM ベンチマーク結果（2026-10-06更新）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {JIT_CardAgingSweep} {Wasm32Only} -->

## 2026-10-06 フルテスト・ベンチマーク確認

### 測定条件

- AMD Ryzen 5 5500GT、x86_64、Ubuntu Linux 7.0.0-34-generic、CPython 3.14.6、Clang/Clang++ 21.1.8、uv 0.12.19を使用した。
- Interpreter、JIT製品ライブラリとQAライブラリを現行作業ツリーから再ビルドした。
- `run_all.py`を現行QAライブラリで実行し、[実行ログ](results/run_all_20261006.txt)に保存した。CPU affinity固定はしておらず、複数回測定の中央値ではない。
- 実測対象はx64ホスト上のPySIMである。Cortex-M33の実行時間や命令数を示さない。VTune、AMD uProf、Linux `perf`は環境にないため使用していない。

### ベンチマーク結果

| 系統 | 指標 | 実測値 |
| :--- | :--- | ---: |
| 線形メモリ | 32-bit raw bytearray read/write | 9.23 M ops/s、108.4 ns/op |
| 線形メモリ | 8-bit / 16-bit read/write | 29.98 / 9.29 M ops/s |
| 線形メモリ | 境界確認 / vMMIO RAM bypass | 14.95 M ops/s（66.9 ns/op） / 3.47 M ops/s（288.1 ns/op） |
| 線形メモリ | RAM帯域 | 13.24 MB/s |
| vMMIO | TLB hit / flat-map miss walk | 1.03 / 0.92 M ops/s（969.0 / 1,086.1 ns） |
| vMMIO | XOR hash / 静的syscall dispatch / RBAC確認 | 11.80 / 1.29 / 1.26 M ops/s |
| JIT | Copy-and-Patchコンパイル | 27,290 traces/s、36.64 µs/trace |
| JIT | 100,000回算術ループ | Python 4,623.21 ms、C++ Interpreter 18.39 ms、JIT 16.78 ms |
| JIT | Python比 / C++ Interpreter比 | 275.55倍高速 / 1.10倍高速 |
| JIT | PIC helper tail dispatch | 0.35 M calls/s、2,886.8 ns/call（10,000回） |
| JIT cache | eviction / generation | 6,413 evictions/s、624 generations |
| JIT cache | working-set hit rate（N=8 / 24 / 100） | 100.00% / 100.00% / 92.44% |
| AO-Bench（32×16） | C++ Interpreter / JIT、1,600 rays | 10.73 / 13.01 ms（149,106 / 122,977 rays/s） |
| AO-Bench | JIT 対 C++ Interpreter | 1.21倍遅い |

算術ループの3経路はすべて`704,982,704`を返した。JIT経路は200,001 trace invocations、198,439 dispatcher trace transitions、Interpreter 3 stepsを記録した。cache metabolismではOldest-only promotion、dangling chain解除、module-scoped PCの各不変条件を確認した。AO-Benchも3経路の出力が一致した。

JIT aging条件は、hot onlyでは16回コンパイル・0回purge・1回rotation、agingなしでは758回コンパイル・725回purge・96回rotation、`U1O4`では727/696/92、既定`U2O8`では670/637/82、`U8O32`では458/427/57だった。各値の順はcompile/purge/rotationである。既定条件のaging処理はこの単独実行で約0.01 msだった。

PIC helper tail dispatchは計測用`ctypes` callbackを呼ぶ経路であり、1呼出し約2,887 nsにはPython/C境界費用を含む。Cortex-M33のC helper実行時間とは比較しない。

### テスト結果

| スイート | 結果 |
| :--- | :--- |
| PySIM QA unit suite | 30/30 suites pass |
| JIT runtime / x64 JIT / differential targeted tests | 133 passed |
| Integration scenarios | 12/12 pass |
| WASM workload runner | 3/3 suites pass。WASI guest 52 pass、driver stubs 11 pass、Core Spec 37/37 files・15,230 assertions pass |
| `check-src.sh -g all` | pass、0 warnings。検証マトリクスは26 specs、14 concepts、21 formal models、25 test specs、12 scenarios、4 integration suites、3 WASM suites |
| `format-src.sh -g pysim` / `format-doc.sh` | pass。ネイティブC/C++の変更19ファイルもclang-format検査済み |
| `pyright` | 0 errors / warnings / information |

文書品質ゲートにはriskと`{VERIFY_LLM}`監査も含まれ、ワークロードのベンチマーク結果とは別に管理する。

ベンチマーク全体は37.56秒で完了した。CIと同じ`bench_vtune_workload.py --scale 0.02`も全6 workloadが完走し、Python/C++ Interpreter/JITのチェックサムが一致した。x64上のJITは算術ループではC++ Interpreterを上回ったが、AO-Benchでは遅かった。この測定だけからJIT全般の実行時間改善を結論づけない。Cortex-M33の物理RAM適合は対象ABIと配置で確認する必要がある。

### x64 local-pairステンシル再測定

Clang生成WASMの`local.get + local.get + i32.<op>`は85回出現した。QA RuntimeEngineで同一作業ツリーのコンパイラを比較し、融合なし版では9〜11命令、融合版では7〜9命令だった。全13種類の出現演算が正しい値を返した。頻度で重み付けした静的削減上限は174命令である。この値は動的実行頻度や実機性能を表さない。

基準ライブラリは、現行のJIT/runtime/ABIを保ち、`is_i32_local_pair_opcode`だけを無効化したQAビルドである。以前の`889e406f`単独ソースを現行ヘッダへ差し替える方法はABI非互換であるため廃止した。測定ログは[融合なし](results/x64_stencil_instructions_20261006_baseline.txt)、[融合版](results/x64_stencil_instructions_20261006_fused.txt)、[比較](results/x64_stencil_instructions_20261006.txt)に保存した。

比較の再現手順は、作業ツリーの`trace_compiler.cxx`を一時コピーし、`is_i32_local_pair_opcode`の本体を`static_cast<void>(op); return false;`に置き換えて`FIREBALL_JIT_TRACE_SOURCE`へ指定し、`--qa --block-counters`で基準ライブラリをビルドする。その後、通常のQAライブラリを再ビルドし、`bench_x64_stencil_instructions.py --baseline-library <基準.so> --compiler-library experiments/pysim/qa/private/libjit_probe.so`を実行する。

同じ基準・候補ライブラリで250,000反復のループを各9回測り、各workerで3回ウォームアップした。両方とも同じWASMループトレースを生成・実行し、実行時のトレース本体は基準129 bytes、融合版117 bytesだった。実行時間の中央値は基準40.485 ms、融合版40.928 msで、融合版は1.09%遅かった。範囲は基準39.913–49.938 ms、融合版38.767–45.254 msであり、この測定では実行時間の改善を確認できない。計時区間からモジュール読込、コンパイル、ウォームアップを除外した。結果と条件は[ランタイム比較ログ](results/x64_stencil_runtime_20261006.txt)に保存した。

## 2026-10-05 統合スイート再測定

### 測定条件

- `run_all.py`と関数呼び出し計測は`dc4f8fec`を基点に測定し、JIT agingベンチマークの未コミット修正を含む。
- 0xFC計測は`3b0cf07c`を基点に測定し、`bench_fc.py`の未コミット修正を含む。C++ネイティブソースに差分はない。
- AMD Ryzen 5 5500GT、Linux 7.0.0-34-generic、CPython 3.14.6、Clang 21.1.8、uv 0.12.19で測定した。
- InterpreterとJITのC++共有ライブラリを再ビルドした。`taskset -c 2`でCPU 2に固定し、`run_all.py`を独立プロセスで3回実行した。
- 通常実行のwall-clock値を採った。VTune、AMD uProf、Linux `perf`、ハードウェアイベントは使用していない。
- ホスト上のPySIM結果である。組み込みCPUの実行時間やROM/RAM使用量を示す値ではない。
- agingの`compile_ms`は`compile_wasm_trace`の開始から`build_runtime_trace`の終了までを計時した。

### ベンチマーク不具合の修正

- JIT aging計測器が現行の`JITRuntimeManager`プロトコルにない`compile_trace`を実装していた。`compile_wasm_trace`と`build_runtime_trace`を実装し、コンパイル区間を両APIにまたがって計時するよう修正した。
- 0xFC計測器は短命なInterpreterを繰り返し生成すると、解放されない計測用領域がモジュールのローダー領域を使い切っていた。各Interpreterに専用の`BumpAllocator`を割り当てるよう修正した。
- vDMA計測器にRuntimeタスクとタスクコンテキストがなく、動的PTEの所有者検査を通過できなかった。さらに、転送元オフセットがHALバッファの範囲外だった。Runtimeタスクを登録し、操作をタスクコンテキスト内で実行して、バッファ内オフセットを使うよう修正した。

### 結果

6系統は3回とも完走した。全体時間の中央値は30.40秒（29.95–30.69秒）だった。

| 指標 | 中央値（範囲） |
| :--- | ---: |
| 線形RAM帯域 | 13.90 MB/s（13.88–14.10） |
| Direct-mapped TLB hit | 958.4 ns（936.5–1,021.3） |
| Copy-and-Patch compile throughput | 35,468 traces/s（29,490–35,597） |
| 100,000回算術ループ・Python Interpreter | 4,678.40 ms（4,644.89–4,792.45） |
| 100,000回算術ループ・C++ Interpreter | 20.82 ms（20.42–20.83） |
| 100,000回算術ループ・Hybrid JIT | 19.19 ms（19.15–19.56） |
| 算術ループ・JITのPython Interpreter比 | 243.79倍高速（242.51–245.02） |
| 算術ループ・JITのC++ Interpreter比 | 1.06倍高速（1.06–1.09） |
| JIT cache churn | 109,687 evictions/s（108,279–115,128） |
| 既定JIT aging・処理時間 | 622 ms（598–641） |
| 既定JIT aging・age step時間 | 0.50 ms（0.49–0.50） |
| AO-Bench・Python Interpreter | 6,148.23 ms（6,141.19–6,564.57） |
| AO-Bench・C++ Interpreter | 13.29 ms（12.48–13.43） |
| AO-Bench・Hybrid JIT | 18.21 ms（17.18–20.28） |
| AO-Bench・JITのC++ Interpreter比 | 1.38倍遅い（1.36–1.53） |

算術ループの3経路はすべて`704,982,704`を返した。AO-Benchは32×16、1,600 rayの各実行で3経路の出力が一致した。キャッシュ不変条件、agingの決定的カウンター、プログラム結果も各実行で確認した。

既定agingは727回のコンパイル・656回の追い出しを行うagingなし構成に対し、698回のコンパイル・631回の追い出しだった。既定値は45回rotateし、aging処理は中央値0.50 msだった。

実行ログ: [1回目](results/run_all_20261005_postfix_1.txt)、[2回目](results/run_all_20261005_postfix_2.txt)、[3回目](results/run_all_20261005_postfix_3.txt)。

再現コマンド:

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --project . --offline --no-sync python experiments/pysim/benchmarks/run_all.py \
    > "experiments/pysim/benchmarks/results/run_all_20261005_postfix_${trial}.txt" 2>&1
done
```

### 個別計測

関数呼び出しベンチマークは10,000回の呼び出しを測り、各経路は`50,005,000`を返した。各JSON内の3反復の中央値を、独立プロセス3回分で集計した。

| 経路 | `call` 合計 / 1回あたり | `call_indirect` 合計 / 1回あたり |
| :--- | ---: | ---: |
| Python Interpreter | 994.73 ms（976.01–1,029.00） / 99.47 µs（97.60–102.90） | 1,056.44 ms（1,036.21–1,098.92） / 105.64 µs（103.62–109.89） |
| C++ Interpreter | 2.58 ms（2.53–4.31） / 0.258 µs（0.253–0.431） | 2.81 ms（2.65–4.58） / 0.281 µs（0.265–0.458） |

JSON: [1回目](results/bench_call_dispatch_retake_20261005_run1.json)、[2回目](results/bench_call_dispatch_retake_20261005_run2.json)、[3回目](results/bench_call_dispatch_retake_20261005_run3.json)。

0xFCベンチマークは、各プロセス内で3反復の中央値を記録した。表の中央値と範囲は独立プロセス3回分である。意味チェックは飽和変換38境界ケース、線形memory.copy／memory.fill 4ケース、vDMA 4経路で通過した。

| 操作 | サイズ | C++ Interpreter | Python Interpreter |
| :--- | ---: | ---: | ---: |
| 飽和数値変換8種 | — | 0.502 µs（0.471–0.551） | 43.309 µs（40.219–50.885） |
| `memory.copy` | 16 B | 0.281 µs（0.276–0.300） | 45.134 µs（44.942–52.579） |
| `memory.copy` | 256 B | 0.528 µs（0.502–0.573） | 56.815 µs（56.061–68.450） |
| `memory.copy` | 4,096 B | 4.977 µs（4.968–4.993） | 241.670 µs（238.381–289.741） |
| `memory.fill` | 16 B | 0.304 µs（0.274–0.304） | 43.701 µs（43.616–50.846） |
| `memory.fill` | 256 B | 0.492 µs（0.480–0.549） | 50.523 µs（49.345–58.108） |
| `memory.fill` | 4,096 B | 5.291 µs（4.864–5.740） | 159.964 µs（153.899–186.738） |

64 B vDMA転送は各経路512回である。C++経路の時間にはC++ InterpreterからPySIMのPython vDMAサービスへ移る境界を含む。

| ルート | C++経路 | Python経路 |
| :--- | ---: | ---: |
| linear → DYNAMIC | 28.321 µs（27.356–29.662） | 60.371 µs（59.690–66.895） |
| SHM → linear | 24.394 µs（24.313–26.156） | 56.603 µs（56.327–63.345） |
| DYNAMIC → PASSTHROUGH | 30.585 µs（30.535–33.377） | 63.092 µs（62.611–70.507） |
| PASSTHROUGH → SHM | 30.366 µs（29.452–32.375） | 62.987 µs（61.817–70.114） |

JSON: [1回目](results/bench_fc_retake_20261005_run1.json)、[2回目](results/bench_fc_retake_20261005_run2.json)、[3回目](results/bench_fc_retake_20261005_run3.json)。

## Cortex-M33ステンシル候補の静的選別

Clang 21.1.8でC23の [`suite.c`](profile/guest/suite.c) をWASMへコンパイルし、WASM命令列から `local.get + i32.const + 整数演算` の132種類を抽出した。各候補についてClangを `armv8m.main-none-eabi` / `cortex-m33` / Thumb / `-O2` で実行し、個別のlocal読出し・定数生成・演算をつないだCプローブと、同じ演算を定数込みで表したCプローブの機械語命令数を比較した。

正の削減を見積もった候補は69種類で、プローブ本体の合計は324バイト、WASMモジュール中の静的出現数で重み付けした見積削減数は534命令である。代表例は `local.get + i32.const 15 + i32.shr_u` が49出現で1出現あたり2命令、即値1の `i32.add` が90出現で1命令、即値2の `i32.shl` が40出現で2命令である。これは静的出現数に基づくCプローブ上の推定であり、動的実行頻度、実装済みARM JITの命令数、実機の性能・ROM量を示さない。ARMv8-MのJIT ABIとステンシル生成は未確定のため、採用前に実際のARMトレース出力で再計数する。

再現コマンドは `uv run --project . --offline --no-sync python experiments/pysim/benchmarks/profile/guest/select_m33_ngrams.py` である。候補の即値、頻度、個別・融合プローブ命令数は [選別結果](results/m33_stencil_candidates_20261006.txt) に保存した。コードサイズ予算を指定する場合は `--rom-budget-bytes N` を追加する。
