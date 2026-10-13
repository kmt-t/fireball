# PySIM ベンチマークレポート（2026-10-14）
<!-- traceability: {ThreadedInterpreter} {Wasm32Only} {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {MemoryBoundaryCheck} -->

以下の第1〜6節は前回計測の履歴である。計測時のsource revisionは `29dc47c9e0fb243ba30c73ea2c25f1febd9699be`、worktreeはmodifiedだった。前回の数値と証跡は [`前回データ`](results/20261014/pre_req017_latest/metadata.json) に保持し、REQ-017の最新結果は第7節に記録する。
数値はLinux x86-64上の参照実装の結果である。ARMv8-Mの速度や物理RAM・ROM適合性を示さない。

## 1. 実行環境と証跡

ホストはAMD Ryzen 5 5500GT with Radeon Graphicsである。Pythonは3.13.15、コンパイラはUbuntu clang version 21.1.8 (6ubuntu1)である。
統合スイートはassert有効のO3・ThinLTOビルドを使う。WAMR比較はO3・NDEBUG・ThinLTOのreleaseビルドを使う。
統合スイートの時間とWAMR比較の時間は、入力サイズと準備実行が異なるため直接比較しない。

計測ログ、全サンプル、ソースとライブラリのSHA-256は[最新データ](results/20261014/pre_req017_latest/metadata.json)に対応付ける。
過去の設計判断で参照する証跡は各設計文書の参照先に保持する。

## 2. 統合ベンチマーク

登録済み6系統を同一条件で3回実行し、すべて完走した。総実行時間の中央値は30.77秒、min/maxは30.61–31.19秒である。
以下の値は3回の実行結果の中央値で、括弧内に実行間のmin/maxを示す。
[統合スイートの全出力](results/20261014/pre_req017_latest/unified.log)には3回分の全指標と差分検査結果を記録する。

### メモリアクセスとvMMIO

| 測定項目 | 中央値（3回のmin–max） |
| :--- | ---: |
| 32bit読書きの基準 | 9.65 M ops/s (9.33–9.76); 103.6 ns/op (102.4–107.2) |
| 8bit読書き | 28.27 M ops/s (27.24–28.71) |
| 16bit読書き | 9.73 M ops/s (9.56–9.73) |
| 境界検査 | 13.09 M ops/s (12.76–13.14); 76.4 ns/op (76.1–78.3) |
| vMMIOのリニア経路 | 2.95 M ops/s (2.76–2.96); 338.5 ns/op (338.2–362.6) |
| リニアRAM帯域 | 11.27 MB/s (10.52–11.28) |
| TLB hit | 0.89 M ops/s (0.88–0.89); 1129.8 ns/hit (1122.7–1130.9) |
| XOR縮約 | 9.42 M ops/s (8.64–9.69); 106.2 ns/op (103.2–115.8) |
| TLB miss時の検索 | 0.80 M ops/s (0.79–0.81); 1248.7 ns/walk (1238.1–1270.8) |
| syscall配送 | 1.15 M ops/s (1.14–1.20); 867.0 ns/dispatch (835.5–874.3) |
| タスク分離検査 | 1.09 M ops/s (1.07–1.10); 916.8 ns/check (908.9–934.7) |

### JITコンパイルと算術ループ

| 測定項目 | 中央値（3回のmin–max） |
| :--- | :--- |
| traceコンパイル | 32,315 Traces/sec (31,846–32,812); 30.95 us/trace (30.48–31.40) |
| WASM命令あたりのコンパイル費用 | 7736.4 ns/opcode (7619.2–7850.3) |
| カード状態検査 | 0.60 M ops/s (0.58–0.61); 1653.4 ns/check (1642.9–1711.4) |
| 常駐entry検索 | 1.98 M ops/s (1.93–2.03); 505.4 ns/lookup (493.4–519.0) |
| 10万回の算術ループ | Python: 3595.27 ms (3595.16–3692.36) / Native interp: 22.98 ms (22.46–23.98) / JIT: 22.00 ms (21.57–22.16) |
| Python・Interpreter・JITの結果 | Python=704,982,704 / Native interp=704,982,704 / JIT=704,982,704 (MATCH) |
| 診断用の実行回数 | 200,001 trace invocations, 198,439 dispatcher trace transitions, 4 interpreter steps |

算術ループは各実行系を3回計時し、中央値を報告する。診断カウンタは計時とは別に採取する。

### キャッシュ代謝

| 測定項目 | 中央値（3回のmin–max） |
| :--- | :--- |
| 世代hitでコードを移動しない不変条件 | [PASS] (True) |
| 作業集合8件 | Hit Rate = 100.00% (3回すべて); 0.04 M lookups/s (3回すべて) |
| 作業集合24件 | Hit Rate = 100.00% (3回すべて); 0.01 M lookups/s (3回すべて) |
| 作業集合100件 | Hit Rate = 99.93% (3回すべて); 0.01 M lookups/s (3回すべて) |
| 追い出し速度 | 7,133 Evictions / Sec (7,132–7,194; 624 generations) |
| 退役traceへのchain解除 | [PASS] (All evicted traces unlinked cleanly) |
| モジュールごとのPC所有権 | [PASS] (each module owns its PC lookup) |

### カードエイジング

ホット4関数、コールド200関数、200パス、各パスのコールド8呼出しを使う。各変種は3回実行する。
計数値の一致を検査し、時間は3回の中央値を示す。ホット関数だけの基準では追い出しが0件である。

| 設定 | コンパイル数 | 追い出し数 | ローテーション数 | JIT割合 | 総時間の中央値 (min–max) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| hot only (ideal) | 8 | 0 | 1 | 80.5% | 60 ms (58–62) |
| no aging | 1580 | 1568 | 394 | 34.4% | 182 ms (179–197) |
| aging U=1 O=4 | 1325 | 1316 | 331 | 37.6% | 176 ms (175–192) |
| aging U=2 O=8 (config default) | 957 | 948 | 239 | 46.1% | 174 ms (174–178) |
| aging U=8 O=32 | 290 | 280 | 72 | 60.1% | 176 ms (172–178) |

全汚染変種で結果checksumが一致した。エイジング有効時の処理回数はローテーション数と一致した。
既定値のコンパイル数はエイジングなし以下である。時間とJIT割合は合否判定に使わない。

### 統合スイートのAO-Bench

| 測定項目 | 中央値（3回のmin–max） |
| :--- | :--- |
| 入力サイズとray数 | 32 x 16 (1,600 rays / frame) |
| Python参照実装 | 4335.29 ms (4312.72–4363.37) |
| NativeInterpreter | 11.84 ms (10.89–13.00); 135,079 Rays / Sec (123,084–146,912) |
| Hybrid JIT | 13.62 ms (13.27–21.78); 117,446 Rays / Sec (73,451–120,585) |
| 診断用trace遷移数 | 54,069 (3回すべて) |

Python・NativeInterpreter・Hybrid JIT・診断用実行の出力をbyte単位で照合した。

## 3. AO-BenchとWAMRのrelease比較

CPU 2へ固定し、各解像度・実行系を3独立プロセスで実行した。各プロセスは3回準備実行し、7回計時した。
実行系の順序を各試行で回転した。45プロセス、315回の計時である。
代表値は各プロセスの中央値を取り、その3値の中央値とする。括弧内はプロセス中央値の最小値と最大値である。

| サイズ | NativeInterpreter ms (min–max) | Hybrid JIT ms (min–max) | WAMR Fast ms (min–max) | Interpreter/WAMR | JIT/WAMR |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 64×36 | 15.689 (15.582–15.984) | 18.576 (18.573–18.756) | 4.743 (4.727–4.793) | 3.31 | 3.92 |
| 128×72 | 61.609 (61.345–62.034) | 72.519 (72.043–72.618) | 18.893 (18.806–18.906) | 3.26 | 3.84 |
| 192×108 | 137.732 (136.983–139.213) | 163.038 (160.627–164.436) | 42.837 (42.094–42.901) | 3.22 | 3.81 |
| 256×144 | 243.298 (242.491–255.278) | 288.875 (287.170–290.786) | 75.206 (75.165–75.950) | 3.24 | 3.84 |
| 320×180 | 381.931 (379.845–385.380) | 451.661 (449.452–456.985) | 118.322 (117.315–119.874) | 3.23 | 3.82 |

320×180のJITはInterpreterより18.3%遅い。WAMR FastはInterpreterより3.23倍、JITより3.82倍速い。
全解像度の全準備実行・計時実行で、3実行系の出力SHA-256が一致した。
計時対象は`NativeInterpreter.call()`または`wasm_runtime_call_wasm()`である。ロード、初期化、出力読取りとhash照合は計時に含めない。
Fireballのyieldしきい値は4096とした。JIT候補は準備実行後に処理し、計時中の`idle_budget`は0とした。
FireballはPython WASI・HAL・StreamTransportを使う。WAMRはC WASIからメモリ上のファイルへ出力する。
ホストの出力処理は計時に含む。この結果はホスト統合を含むワークロード比較である。
WAMRはFast InterpreterとWASIを有効にし、AOT・LLVM JIT・Fast JITを無効にした。固定commitは`f5f57c09aee623436f5fb87a90798fdd2cdf39fd`である。
全サンプル、build設定、source・library・WASM hashは[WAMR比較データ](results/20261014/pre_req017_latest/aobench_wamr.json)に保存する。

## 4. 呼出しと選択0xFC命令の追加計測

呼出し比較はCPU 2固定、各条件10,000反復、3回計時とした。表は各条件の中央値で、括弧内は3サンプルのmin–maxである。PythonとNativeInterpreterの結果値を独立した期待値へ照合した。

| 実行系 | 負荷 | 中央値 (min–max) |
| :--- | :--- | ---: |
| python | baseline | 401.971 ms (400.386–403.330) |
| python | direct | 837.032 ms (822.176–839.614) |
| python | indirect | 895.638 ms (893.462–898.737) |
| native | baseline | 2.360 ms (2.332–2.445) |
| native | direct | 2.689 ms (2.682–2.692) |
| native | indirect | 2.740 ms (2.699–2.846) |

呼出し比較はassert有効の通常ビルドを使う。[全サンプル](results/20261014/pre_req017_latest/call_dispatch.json)を保存する。
選択0xFC命令はCPU 2固定、`-O3 -DNDEBUG -flto=thin`のreleaseビルドで3回計測し、各回の内部3ラウンドの中央値を比較した。以下は実行間の中央値とmin/maxである。飽和変換38ケース、copy/fill境界4ケース、vDMA経路4種類の意味論を検査した。

| 操作 | 16 B | 256 B | 4096 B |
| :--- | ---: | ---: | ---: |
| Native copy (ns/op) | 234.9 (227.0–239.6) | 313.8 (309.2–319.6) | 2075.1 (1996.7–2188.6) |
| Native fill (ns/op) | 223.0 (221.6–251.1) | 305.4 (303.8–340.2) | 1871.9 (1834.6–1873.9) |
[0xFC計測データ](results/20261014/pre_req017_latest/fc.json)は各入力サイズの時間と結果検査を保持する。
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
通常ネイティブライブラリのELF section、ソース規模、frameごとの計測は[資源一覧](results/20261014/pre_req017_latest/resource_inventory.json)に保存する。
割当起点、callごとのpeak、独立領域、出力hashは[Runtimeメモリ計測](results/20261014/pre_req017_latest/runtime_memory.json)に保存する。

## 6. 検証結果と再実行

コンポーネント単体は30/30グループ、1284テストが成功した。結合4スイートと12シナリオも成功した。
WASMワークロードは3/3スイート成功した。公式Core選択セットは37/37ファイル、15,230アサーション、skip 0である。
通常・`-O`・`-OO`の動作比較は単体スイートに含む。`NDEBUG` copy/fill 回帰は2件成功した。製品型検査はエラー0件、検証ツールは172テスト成功した（警告2件）。
全ソースゲートは0警告で合格した。文書ゲートはエラー0件、警告79件、義務44/44件を履行した。全結果は[最終検証記録](results/20261014/pre_req017_latest/validation.json)に記録する。文書ゲートの警告は合格と区別して記録する。

```bash
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier2_runtime/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/uv-cache bash experiments/pysim/native/tier3_plugins/jit/build_native.sh
.venv/bin/python experiments/pysim/benchmarks/run_all.py
.venv/bin/python experiments/pysim/benchmarks/memory/bench_runtime_memory.py --output /tmp/runtime_memory.json
.venv/bin/python experiments/pysim/benchmarks/memory/bench_resource_inventory.py --output /tmp/resource_inventory.json
```

WAMR比較の準備・実行コマンドとビルド条件は[再実行コマンド](results/20261014/pre_req017_latest/commands.md)に記載する。
各計測ファイルのSHA-256は[チェックサム](results/20261014/pre_req017_latest/SHA256SUMS)に固定する。

## 7. REQ-017 最新計測（2026-10-14）

計測対象はHEAD `506d1213e2d3045fb2b7ccec9016ed2e0d1c0849`で、開始時のworktreeはcleanだった。WAMRはcommit `f5f57c09aee623436f5fb87a90798fdd2cdf39fd`を使った。計測前の負荷平均は0.25、0.53、0.44で、CPU idleは96.7%だった。

AO、WAMR、0xFCは`-O3 -DNDEBUG -flto=thin`のrelease build、呼出しと統合測定はassert有効の通常buildを使った。AOと呼出しはCPU 2、統合・vMMIOと資源測定はCPU 3へ固定した。統合スイートと0xFCは独立実行を各3回行い、中央値とmin/maxを記録した。

### 7.1 統合スイートとAO/WAMR

統合スイートは3回すべて完走し、実行時間は30.77秒（30.70–30.87秒）だった。内包する32×16 AO-BenchはPython 4,303.82 ms（4,297.75–4,323.36）、NativeInterpreter 12.03 ms（10.57–12.10）、Hybrid JIT 13.49 ms（13.46–13.63）だった。3回ともJIT trace transitionは54,069回だった。

既存のAO/WAMR比較器は64×36から320×180までの5解像度を固定している。依頼にある32×16も除外しないよう、同じCPU・release build・各3プロセス・各3 warmup・7計時で追加した。値は各engineのプロセス中央値の中央値で、括弧内にプロセス中央値のmin/maxを示す。

| サイズ | NativeInterpreter ms | Hybrid JIT ms | WAMR Fast ms |
| :--- | ---: | ---: | ---: |
| 32×16（追加） | 4.210 (4.169–4.214) | 4.928 (4.907–4.935) | 1.167 (1.149–1.191) |
| 64×36 | 15.896 (15.882–16.002) | 18.678 (18.634–18.690) | 4.800 (4.756–4.883) |
| 128×72 | 61.381 (61.021–61.720) | 72.608 (72.406–73.468) | 19.095 (19.079–19.412) |
| 192×108 | 137.694 (137.211–138.718) | 164.393 (163.324–180.365) | 42.322 (42.165–43.372) |
| 256×144 | 246.837 (246.576–247.663) | 290.057 (287.738–293.599) | 77.037 (75.738–79.119) |
| 320×180 | 387.356 (385.509–390.298) | 452.974 (452.490–459.395) | 120.962 (118.308–121.641) |

全プロセスで出力hashが一致した。全サンプルとbuild・source・library情報は[AO/WAMR結果](results/latest/aobench_wamr.json)と[32×16追加結果](results/latest/aobench_wamr_32x16.json)、統合スイートの全出力は[unified.log](results/latest/unified.log)に保存した。

### 7.2 呼出しと0xFC

呼出し計測はCPU 2固定、各条件10,000反復、3回計時とした。各行は中央値（min–max）である。

| 実行系 | 負荷 | 時間 |
| :--- | :--- | ---: |
| Python | baseline | 401.266 ms (399.720–402.592) |
| Python | direct | 843.902 ms (829.980–853.645) |
| Python | indirect | 889.503 ms (889.169–891.047) |
| Native | baseline | 2.345 ms (2.296–2.367) |
| Native | direct | 2.771 ms (2.696–2.794) |
| Native | indirect | 2.693 ms (2.688–2.711) |

0xFC計測はCPU 2固定、release buildで3プロセスを実行し、各プロセスの内部3ラウンド中央値から実行間の中央値（min–max）を求めた。

| 操作 | 16 B | 256 B | 4096 B |
| :--- | ---: | ---: | ---: |
| Native copy (ns/op) | 224.8 (212.3–227.8) | 303.9 (292.8–305.7) | 1,959.0 (1,940.0–1,991.1) |
| Native fill (ns/op) | 218.5 (216.3–220.7) | 301.4 (298.1–303.5) | 1,948.1 (1,935.7–1,970.4) |

飽和変換38ケース、copy/fill境界4ケース、vDMA経路4種類を検査した。全サンプルは[呼出し結果](results/latest/call_dispatch.json)と[0xFC結果](results/latest/fc.json)にある。

### 7.3 アリーナ、JIT ROM、compile stack、SLOC

アリーナは各入力の3呼出しで同じ最大値を観測した。ゲスト線形メモリは論理サイズであり、物理RAMへ加算しない。

| 入力 | 関数数 / 基本ブロック数 | load | instantiate | Native arena peak | Hybrid arena peak | guest linear memory | JIT要求領域 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| arithmetic | 1 / 7 | 390 B | 390 B | 3,184 B (3,184–3,184) | 3,184 B (3,184–3,184) | 0 B | 12,288 B |
| indirect_calls | 4 / 24 | 1,263 B | 1,268 B | 4,296 B (4,296–4,296) | 4,296 B (4,296–4,296) | 0 B | 12,288 B |
| kernel_suite | 28 / 885 | 51,911 B | 51,956 B | 56,712 B (56,712–56,712) | 56,712 B (56,712–56,712) | 65,536 B | 12,288 B |
| AO-Bench | 7 / 107 | 5,741 B | 5,741 B | 8,968 B (8,968–8,968) | 8,968 B (8,968–8,968) | 65,536 B | 12,288 B |

build時に生成するJIT stencil raw binaryはtrace 665 B、operations 1,438 B、common 69 B、合計2,172 Bだった。これらは`libtrace_compiler.so`へ埋め込まれているため、ELF section値へ重ねて加算しない。

一時directoryで`-O2 -g -fstack-usage`を使って計測した既知のcompile call chainは、`compile_queued_candidates` 56 B、`compile_one_candidate` 1,944 B、`fb_jit_compile_block` 120 B、`compile_wasm_trace` 20,664 B、合計22,784 Bだった。この値はx86_64 hostで既知の関数frameを合計したものであり、ABIや追加caller/callee frameを含む全経路の上限ではない。

通常buildでのhost ELF `.text + .rodata`はprintk 921 B、interpreter 370,992 B、JIT 185,597 B、合計557,510 Bだった。Firmware SLOCは3,704/20,000、PySIM Python参照実装は75ファイル・15,652物理行・11,974 SLOCだった。詳細は[資源一覧](results/latest/resource_inventory.json)、[メモリ結果](results/latest/runtime_memory.json)、[SLOC](results/latest/sloc.json)、[stencil ROM](results/latest/stencil_rom.json)、[stack usage](results/latest/stack_usage.json)を参照する。

フル検証では単体30/30グループ・1,284件、結合12/12シナリオ、WASM workload 3/3、公式Core 37/37ファイル・15,230 assertionsが成功した。Pyrightは0 error、全ソースゲートは0 warning、spec-integratorは172 passed（warning 2件）、文書ゲートは0 error・79 warningで44/44義務を履行した。文書ゲートのwarningは合格数と分けて扱う。

これらはx86_64ホスト値であり、Cortex-M33の速度、物理RAM、ROM適合を示さない。全テスト・ゲートの結果は[validation.json](results/latest/validation.json)、再実行条件は[commands.md](results/latest/commands.md)、証跡hashは[SHA256SUMS](results/latest/SHA256SUMS)に保存した。
