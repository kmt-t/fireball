# PySIM ベンチマーク結果（2026-10-05）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {JIT_CardAgingSweep} {Wasm32Only} -->

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
