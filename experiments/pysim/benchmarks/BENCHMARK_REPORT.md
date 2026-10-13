# PySIM ベンチマークレポート（2026-10-13）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {MemoryBoundaryCheck} -->

このレポートは現行ソースの最新計測結果を示す。計測条件が異なる結果は別の表に記載する。
数値はLinux x86-64上の参照実装の結果である。ARMv8-Mの速度や物理RAM・ROM適合性を示さない。

## 1. 実行環境と証跡

ホストはAMD Ryzen 5 5500GT with Radeon Graphicsである。Pythonは3.13.15、コンパイラはUbuntu clang version 21.1.8 (6ubuntu1)である。
統合スイートはassert有効のO3・ThinLTOビルドを使う。WAMR比較はO3・NDEBUG・ThinLTOのreleaseビルドを使う。
統合スイートの時間とWAMR比較の時間は、入力サイズと準備実行が異なるため直接比較しない。

計測ログ、全サンプル、ソースとライブラリのSHA-256は[最新データ](results/latest/metadata.json)に対応付ける。
過去の設計判断で参照する証跡は各設計文書の参照先に保持する。

## 2. 統合ベンチマーク

登録済み6系統はすべて完走した。総実行時間は30.75秒である。
[統合スイートの全出力](results/latest/unified.log)には全指標と差分検査結果を記録する。

### メモリアクセスとvMMIO

| 測定項目 | 最新結果 |
| :--- | ---: |
| 32bit読書きの基準 | 9.46 M ops/s  (105.7 ns/op) |
| 8bit読書き | 27.74 M ops/s |
| 16bit読書き | 9.45 M ops/s |
| 境界検査 | 12.81 M ops/s  (78.1 ns/op) |
| vMMIOのリニア経路 | 2.87 M ops/s  (348.8 ns/op) |
| リニアRAM帯域 | 10.94 MB/s |
| TLB hit | 0.90 M ops/s  (1111.0 ns/hit) |
| XOR縮約 | 9.46 M ops/s  (105.7 ns/op) |
| TLB miss時の検索 | 0.81 M ops/s  (1230.3 ns/walk) |
| syscall配送 | 1.19 M ops/s  (842.9 ns/dispatch) |
| タスク分離検査 | 1.12 M ops/s  (894.1 ns/check) |

### JITコンパイルと算術ループ

| 測定項目 | 最新結果 |
| :--- | :--- |
| traceコンパイル | 31,429 Traces/sec  (31.82 us/trace) |
| WASM命令あたりのコンパイル費用 | 7954.3 ns/opcode |
| カード状態検査 | 0.59 M ops/s  (1700.0 ns/check) |
| 常駐entry検索 | 2.02 M ops/s  (495.4 ns/lookup) |
| 10万回の算術ループ | Python: 3604.71 ms / Native interp: 23.26 ms / JIT: 23.71 ms |
| Python・Interpreter・JITの結果 | Python=704,982,704 / Native interp=704,982,704 / JIT=704,982,704 (MATCH) |
| 診断用の実行回数 | 200,001 trace invocations, 198,439 dispatcher trace transitions, 4 interpreter steps |

算術ループは各実行系を3回計時し、中央値を報告する。診断カウンタは計時とは別に採取する。

### キャッシュ代謝

| 測定項目 | 最新結果 |
| :--- | :--- |
| 世代hitでコードを移動しない不変条件 | [PASS] (True) |
| 作業集合8件 | Hit Rate = 100.00%  (0.04 M lookups/s) |
| 作業集合24件 | Hit Rate = 100.00%  (0.01 M lookups/s) |
| 作業集合100件 | Hit Rate = 99.93%  (0.01 M lookups/s) |
| 追い出し速度 | 6,928 Evictions / Sec (624 generations) |
| 退役traceへのchain解除 | [PASS] (All evicted traces unlinked cleanly) |
| モジュールごとのPC所有権 | [PASS] (each module owns its PC lookup) |

### カードエイジング

ホット4関数、コールド200関数、200パス、各パスのコールド8呼出しを使う。各変種は3回実行する。
計数値の一致を検査し、時間は3回の中央値を示す。ホット関数だけの基準では追い出しが0件である。

| 設定 | コンパイル数 | 追い出し数 | ローテーション数 | JIT割合 | 総時間 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| hot only (ideal) | 8 | 0 | 1 | 80.5% | 53 ms |
| no aging | 1580 | 1568 | 394 | 34.4% | 161 ms |
| aging U=1 O=4 | 1325 | 1316 | 331 | 37.6% | 157 ms |
| aging U=2 O=8 (config default) | 957 | 948 | 239 | 46.1% | 155 ms |
| aging U=8 O=32 | 290 | 280 | 72 | 60.1% | 164 ms |

全汚染変種で結果checksumが一致した。エイジング有効時の処理回数はローテーション数と一致した。
既定値のコンパイル数はエイジングなし以下である。時間とJIT割合は合否判定に使わない。

### 統合スイートのAO-Bench

| 測定項目 | 最新結果 |
| :--- | :--- |
| 入力サイズとray数 | 32 x 16 (1,600 rays / frame) |
| Python参照実装 | 4239.42 ms |
| NativeInterpreter | 11.51 ms  (138,980 Rays / Sec) |
| Hybrid JIT | 14.35 ms  (111,522 Rays / Sec) |
| 診断用trace遷移数 | 54,069 |

Python・NativeInterpreter・Hybrid JIT・診断用実行の出力をbyte単位で照合した。

## 3. AO-BenchとWAMRのrelease比較

CPU 2へ固定し、各解像度・実行系を3独立プロセスで実行した。各プロセスは3回準備実行し、7回計時した。
実行系の順序を各試行で回転した。45プロセス、315回の計時である。
代表値は各プロセスの中央値を取り、その3値の中央値とする。括弧内はプロセス中央値の最小値と最大値である。

| サイズ | NativeInterpreter | Hybrid JIT | WAMR Fast | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 15.424 ms（15.266–15.497） | 18.215 ms（18.084–18.255） | 4.760 ms（4.701–4.879） | 3.24 | 3.83 |
| 128×72 | 61.290 ms（60.239–61.491） | 72.184 ms（72.031–72.384） | 18.801 ms（18.670–18.961） | 3.26 | 3.84 |
| 192×108 | 136.677 ms（134.311–136.864） | 160.321 ms（159.588–163.516） | 42.448 ms（41.883–42.524） | 3.22 | 3.78 |
| 256×144 | 244.325 ms（243.413–247.002） | 288.039 ms（286.917–288.122） | 75.917 ms（75.317–76.451） | 3.22 | 3.79 |
| 320×180 | 377.567 ms（375.752–381.286） | 446.310 ms（443.840–453.294） | 120.449 ms（120.055–122.030） | 3.13 | 3.71 |

320×180のJITはInterpreterより18.2%遅い。WAMR FastはInterpreterより3.13倍、JITより3.71倍速い。
全解像度の全準備実行・計時実行で、3実行系の出力SHA-256が一致した。
計時対象は`NativeInterpreter.call()`または`wasm_runtime_call_wasm()`である。ロード、初期化、出力読取りとhash照合は計時に含めない。
Fireballのyieldしきい値は4096とした。JIT候補は準備実行後に処理し、計時中の`idle_budget`は0とした。
FireballはPython WASI・HAL・StreamTransportを使う。WAMRはC WASIからメモリ上のファイルへ出力する。
ホストの出力処理は計時に含む。この結果はホスト統合を含むワークロード比較である。
WAMRはFast InterpreterとWASIを有効にし、AOT・LLVM JIT・Fast JITを無効にした。固定commitは`f5f57c09aee623436f5fb87a90798fdd2cdf39fd`である。
全サンプル、build設定、source・library・WASM hashは[WAMR比較データ](results/latest/aobench_wamr.json)に保存する。

## 4. 呼出しと選択0xFC命令の追加計測

呼出し比較はCPU 2固定、各条件10,000反復、3回計時とした。PythonとNativeInterpreterの結果値を独立した期待値へ照合した。

| 実行系 | 負荷 | 中央値 |
| :--- | :--- | ---: |
| python | baseline | 423.738 ms |
| python | direct | 782.724 ms |
| python | indirect | 846.771 ms |
| native | baseline | 2.390 ms |
| native | direct | 2.810 ms |
| native | indirect | 2.803 ms |

呼出し比較はassert有効の通常ビルドを使う。[全サンプル](results/latest/call_dispatch.json)を保存する。
選択0xFC命令はreleaseビルドで実行した。飽和変換38ケース、copy/fill境界4ケース、vDMA経路4種類の意味論を検査した。
[0xFC計測データ](results/latest/fc.json)は各入力サイズの時間と結果検査を保持する。
`NDEBUG`専用の回帰テストは、copy/fillの注釈カーソル更新と後続命令の実行、周辺メモリの保持を検査する。

## 5. 資源計測

アリーナ値は参照実装の論理割当量である。PythonのRSS、guestリニアメモリ、JITプラグイン領域と区別する。

| 負荷 | Loader | Nativeのアリーナpeak | Hybridのアリーナpeak | guestリニアメモリ | JITプラグイン要求領域 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| arithmetic | 390 B | 3184 B | 3184 B | 0 B | 12288 B |
| indirect_calls | 1263 B | 4296 B | 4296 B | 0 B | 12288 B |
| kernel_suite | 51911 B | 56712 B | 56712 B | 65536 B | 12288 B |
| aobench | 5741 B | 8968 B | 8968 B | 65536 B | 12288 B |

ホストABI上のJIT Runtime状態は1656 Bである。コンパイルscratchは1588 Bである。
O2・stack-usage計測で、既知のWASMコンパイル呼出し列のframe合計は22784 Bである。
これは既知frameの合計であり、経路全体のstack上限ではない。scratchとemitter配列はframeに含まれるため重複加算しない。
通常ネイティブライブラリのELF section、ソース規模、frameごとの計測は[資源一覧](results/latest/resource_inventory.json)に保存する。
割当起点、callごとのpeak、独立領域、出力hashは[Runtimeメモリ計測](results/latest/runtime_memory.json)に保存する。

## 6. 検証結果と再実行

コンポーネント単体は30/30グループ、1267テストが成功した。結合4スイートと12シナリオも成功した。
WASMワークロードは3/3スイート成功した。公式Core選択セットは37/37ファイル、15,230アサーション、skip 0である。
通常・`-O`・`-OO`の動作比較は単体スイートに含む。製品型検査はエラー0件、検証ツールは169テスト成功した。
全ソースゲートと文書ゲートは[最終検証記録](results/latest/validation.json)に対応付ける。文書ゲートの警告は合格と区別して記録する。

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
.venv/bin/python experiments/pysim/benchmarks/run_all.py
.venv/bin/python experiments/pysim/benchmarks/memory/bench_runtime_memory.py --output /tmp/runtime_memory.json
.venv/bin/python experiments/pysim/benchmarks/memory/bench_resource_inventory.py --output /tmp/resource_inventory.json
```

WAMR比較の準備・実行コマンドとビルド条件は[再実行コマンド](results/latest/commands.md)に記載する。
各計測ファイルのSHA-256は[チェックサム](results/latest/SHA256SUMS)に固定する。
