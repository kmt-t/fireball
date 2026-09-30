# PySIM 現行ベンチマーク結果（2026-10-01）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {JIT_CardAgingSweep} {Wasm32Only} -->

## 測定条件

- 測定スナップショット: `HEAD 3274f977` の未コミット作業ツリー。測定前にInterpreterとJITのC++共有ライブラリを再ビルドした。
- 実行環境: AMD Ryzen 5 5500GT、Linux 7.0.0-34-generic、CPython 3.14.6、Clang 21.1.8、uv 0.12.19。
- `taskset -c 2` でCPU 2に固定した。統合スイート、関数呼び出し、0xFCベンチマークをそれぞれ独立プロセスで3回実行した。各行の範囲はプロセス間の最小値と最大値である。
- AMD uProfとLinux `perf` はこの環境にない。記載値はwall-clock計測で、ハードウェアカウンタは取得していない。
- 数値はホスト上のPySIM結果であり、組み込みCPUの実行時間やROM/RAM使用量を表さない。

## 統合スイート

`run_all.py` の6系統（linear memory、vMMIO、JIT、JIT cache、JIT aging、AO-Bench）は3回とも完走した。所要時間の中央値は34.94秒（34.88–35.16秒）だった。

| ワークロード | Python Interpreter | C++ Interpreter | Hybrid JIT |
| :--- | ---: | ---: | ---: |
| 100,000回算術ループ | 4,743.28 ms (4,718.33–4,793.02) | 104.42 ms (103.13–104.62) | 140.84 ms (140.04–146.48) |
| AO-Bench、32×16、1,600 ray | 6,425.38 ms (6,367.81–6,469.92) | 34.40 ms (34.21–34.78) | 1,356.95 ms (1,351.33–1,361.90) |

算術ループではC++ InterpreterがPythonの45.4倍、JITがPythonの33.7倍速かった。JITはC++ Interpreterより1.35倍遅かった。AO-BenchではC++ InterpreterがPythonの186.8倍、JITがPythonの4.73倍速かった。JITはC++ Interpreterより39.5倍遅かった。AO-BenchのJIT値はトレース検出・コンパイルを含む初回実行である。

算術ループの3経路はすべて`704,982,704`を返した。AO-Benchの3経路は各528バイトの描画出力が完全一致し、各試行で1,600 rayを処理した。

## 関数呼び出し

各関数呼び出しワークロードは10,000回を実行した。表の「1回あたり」はループ全体の時間を呼び出し回数で割った値であり、baselineとの差分ではない。PythonとC++の全ケースで結果は`50,005,000`に一致した。

| 経路 | `call` 合計 / 1回あたり | `call_indirect` 合計 / 1回あたり |
| :--- | ---: | ---: |
| Python Interpreter | 1,060.28 ms (1,030.04–1,076.97) / 106.03 µs | 1,104.94 ms (1,074.04–1,136.49) / 110.49 µs |
| C++ Interpreter | 11.84 ms (11.54–12.20) / 1.184 µs | 11.67 ms (11.63–12.04) / 1.167 µs |

## 線形メモリ、vMMIO、0xFC、vDMA

`run_all.py`のメモリとvMMIO計測値を示す。各値は3プロセスの中央値と範囲である。

| 指標 | 中央値 | 範囲 |
| :--- | ---: | ---: |
| Raw bytearray 32-bit R/W | 102.5 ns/op | 102.3–121.9 |
| 8-bit byte R/W | 34.07 M ops/s | 30.48–34.07 |
| 16-bit half-word R/W | 9.78 M ops/s | 9.22–9.82 |
| 境界チェック | 57.8 ns/op | 57.6–71.0 |
| vMMIO linear bypass | 277.0 ns/op | 274.0–278.5 |
| 線形RAM帯域 | 13.77 MB/s | 13.69–13.92 |
| Direct-mapped TLB hit | 922.2 ns/hit | 912.8–935.6 |
| Folding XOR hash | 83.0 ns/op | 82.6–84.3 |
| TLB miss後のFlatMap探索 | 1,062 ns/walk | 1,031.9–1,071.0 |
| TLB hit / miss探索比 | 1.14× | 1.13–1.16 |
| Static syscall dispatch | 766.4 ns/dispatch | 747.4–798.0 |
| RBAC task isolation check | 769.8 ns/check | 748.9–794.5 |

0xFCの飽和変換8種は38の意味境界ケースで確認した。下表の変換値は8 subopcode・3プロセスの各中央値をまとめた中央値と範囲である。`memory.copy`と`memory.fill`の値は操作1回あたりである。

| 操作 | サイズ | C++ Interpreter | Python Interpreter |
| :--- | ---: | ---: | ---: |
| 飽和数値変換8種 | — | 1.58 µs (1.46–3.20) | 45.45 µs (43.21–55.25) |
| `memory.copy` | 16 B | 1.27 µs (1.25–1.31) | 54.18 µs (47.00–64.92) |
| `memory.copy` | 256 B | 1.54 µs (1.53–1.56) | 58.45 µs (56.97–58.56) |
| `memory.copy` | 4,096 B | 8.43 µs (7.03–8.45) | 246.09 µs (244.16–246.39) |
| `memory.fill` | 16 B | 1.25 µs (1.21–1.31) | 46.41 µs (45.55–47.29) |
| `memory.fill` | 256 B | 1.54 µs (1.54–1.59) | 52.63 µs (51.72–55.23) |
| `memory.fill` | 4,096 B | 7.46 µs (7.26–7.53) | 164.83 µs (158.41–169.34) |

64 B vDMA転送は各ルート512回で、転送回数、物理アドレス、転送後データを照合した。C++側の時間にはC++ InterpreterからPysimのPython vDMAサービスへ移る境界を含む。

| ルート | C++経路 | Python経路 |
| :--- | ---: | ---: |
| linear → DYNAMIC | 35.77 µs (35.59–40.25) | 56.96 µs (56.90–66.11) |
| SHM → linear | 35.43 µs (34.64–45.54) | 60.37 µs (55.90–69.28) |
| DYNAMIC → PASSTHROUGH | 38.07 µs (37.57–47.93) | 61.30 µs (59.30–63.12) |
| PASSTHROUGH → SHM | 41.02 µs (39.81–43.85) | 62.13 µs (62.08–80.45) |

## JITとキャッシュ

| 指標 | 中央値 | 範囲 |
| :--- | ---: | ---: |
| Copy-and-Patch compile throughput | 36,393 traces/s | 33,593–36,956 |
| 1 traceあたりcompile時間 | 27.48 µs | 27.06–29.77 |
| 2-bit card状態確認 | 579.3 ns | 572.7–595.2 |
| JIT entry lookup | 456.9 ns | 455.1–459.9 |
| trace-header helper dispatch | 3.70 µs | 3.52–3.81 |
| cache churn | 128,661 evictions/s | 128,372–129,219 |

Working set 8件と24件のcache hit率は100%、100件のthrashing時は92.44%だった。Oldest-only promotion、eviction時のchain unlink、多モジュールUnifiedPC分離の各不変条件も3試行で通過した。

JIT agingの既定値`U=2, O=8`では652 tracesをcompileし608 tracesをpurgeした。agingなしでは745 tracesをcompileし693 tracesをpurgeした。既定値でのaging処理時間は1.33 ms/variantだった。両構成のプログラム結果は一致した。

## 不具合修正と再現

JIT agingベンチマークが通常の`Interpreter`を`RuntimeEngine`へ渡し、C++ dispatchに必要な実行コンテキストを作成していなかったため、`NativeInterpreter`を使うよう修正した。また、0xFCベンチマークもnative側の測定器に`Interpreter`を使っていたため、native側を`NativeInterpreter`へ切り替えた。修正後、統合スイートは3回完走した。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/tier3_executer/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/tier3_executer/jit/build_native.sh
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --project . --offline --no-sync python experiments/pysim/benchmarks/run_all.py \
    > "experiments/pysim/benchmarks/results/run_all_20261001_verified_${trial}.log"
done
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --project . --offline --no-sync python experiments/pysim/benchmarks/interpreter/bench_call_dispatch.py \
    --iterations 10000 --repeats 3 --variant both --workload all \
    > "experiments/pysim/benchmarks/results/bench_call_dispatch_current_20261001_run${trial}.json"
done
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --project . --offline --no-sync python experiments/pysim/benchmarks/interpreter/bench_fc.py \
    > "experiments/pysim/benchmarks/results/bench_fc_final_20261001_run${trial}.json"
done
```

ログとJSONは[統合スイート1回目](results/run_all_20261001_verified_1.log)、[2回目](results/run_all_20261001_verified_2.log)、[3回目](results/run_all_20261001_verified_3.log)、[関数呼び出し計測](results/bench_call_dispatch_current_20261001_run1.json)、[0xFC計測](results/bench_fc_final_20261001_run1.json)に保存した。各ファイル名末尾`run2`、`run3`に残りの試行を保存した。
