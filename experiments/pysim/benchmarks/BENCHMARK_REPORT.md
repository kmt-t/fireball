# PySIM 統合ベンチマーク詳細レポート (PySIM Performance Benchmark Report)

本レポートは、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を具象化した `pysim` における全 6 系統のベンチマークスイートの実測結果、JITトレース遷移の診断、およびC++23実機実装への移植性・性能予測をまとめる。2026-09-18のWindows測定値と旧Linux測定値は履歴データとして残す。Linuxの旧cycles測定は6.6節、直接マップキャッシュC++化後の測定は6.7節、旧RuntimeEngine境界測定は6.8節、最初にyield条件を揃えたLinux測定は6.9節、hotspot profiling切替は6.10節、共通code chain dispatcher更新後のcycles測定は6.11節に記録する。現行コードで取り直した全6系統の結果は6.12節に記録する。chainはtrace末尾から共通コード領域のchain dispatcherが次trace bodyへtail-jumpする経路を指す。過去に記録した`chain hit`・`JIT Chained Invocations`値は、測定当時の実装とカウンタ定義による履歴値であり、現行chain回数との比較には使わない。現行コードに共通chain dispatcher専用の実行カウンタはない。

Section 1〜5は 2026-09-18 に、JIT Direct-Mapped Folding XORキャッシュを4スロットから16スロットへ拡張した変更（`{DirectMappedJIT16}`, `experiments/pysim/tier1_core/config.py`）の直後に同一ワークスペースで再測定した結果である。Pythonプロセス、OSスケジューリング、およびPythonハンドラ本体呼出しの影響を含むため、絶対値ではなく同一環境での比較値として扱う。5.1節のネイティブCPS経路の数値のみ2026-09-17測定の既存値を保持する（本変更では未再測定のため）。

---

## 1. 測定環境・実行構成

- **プラットフォーム**: Windows (AMD64)
- **ランタイム**: Python 3.14 (pysim) / 4引数CPS ABI
- **ネイティブ実行状況**: `tier3_executer/jit/native_trace_call.pyd` をJITのネイティブ呼び出し経路で使用。LEB128は任意のPythran AOTカーネルを使用し、インタープリタのクラス主体のディスパッチはPython実装として測定。
- **ローカル領域レイアウト**: `WASM_LOCAL_ALIGNMENT_BYTES` を基準に8バイト固定スロットへ統一。Native value stack の物理容量は `NATIVE_VALUE_STACK_CAPACITY`（128 raw words）。
- **実行コマンド**:
  ```bash
  uv run --offline --no-sync python experiments/pysim/benchmarks/run_all.py
  ```
- **AO-Bench単体・チェイニング内部ダンプ実行コマンド**:
  ```bash
  uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/bench_aobench.py --debug
  ```
- **ネイティブJITブリッジの実行コマンド**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File experiments/pysim/tier3_executer/jit/build_native.ps1
  uv run --offline --no-sync python experiments/pysim/aobench.py
  ```

### 1.1 2026-09-18 Direct-Mapped Folding XORキャッシュ 4→16スロット拡張後の再測定

`JIT_CACHE_FAST_SLOT_COUNT` と `RUNTIME_BLOCK_CACHE_SLOT_COUNT` を4から16へ変更した（フォールド段数はそれぞれ既存の3段のまま。JIT側は従来の4段目のXORを1段削除しており、追加の折り畳み演算コストは発生しない）。同一のAO-Bench実行内アクセス系列を4/16/256スロットで再生シミュレーションした結果、想定どおりget_blockキャッシュは36.29%→52.51%、JIT lookupキャッシュは71.78%→91.76%までヒット率が改善することを確認したうえで採用した。

| 区分 | 変更前（4スロット） | 変更後（16スロット） |
| :--- | :--- | :--- |
| AO-Bench 同一実行内速度比 | 1.00x（速度向上なし） | **1.06〜1.16x** |
| get_blockキャッシュ ヒット率 | 36.29% | 52.51%（実アクセス系列再生シミュレーション値） |
| JIT lookupキャッシュ ヒット率 | 71.78% | 91.76%（実アクセス系列再生シミュレーション値） |
| RAM増分 | — | 両キャッシュ合計 約+192B（予算余裕 約5.7KBに対し誤差域） |

pysimユニットテスト25/25、統合シナリオ12/12、`check-src.ps1 -group pysim` はすべて成功した。以下のSection 2〜4は、この変更を適用した状態での全ベンチマーク一括実行（`benchmarks/run_all.py`）の実測値である。

---

## 2. 全ベンチマーク実測結果サマリー

```
================================================================================
        Fireball PySIM Unified Performance Benchmark Suite
================================================================================

[Section 1: Linear Memory & Guest RAM Access]
--------------------------------------------------------------------------------
  * Raw Bytearray 32-bit R/W (Baseline): 4.35 M ops/s  (230.1 ns/op)
  * 8-bit Byte R/W Throughput:          15.56 M ops/s
  * 16-bit Half-Word R/W Throughput:     3.86 M ops/s
  * Single-CMP Bound Check Overhead:    7.41 M ops/s  (135.0 ns/op)
  * vMMIO Fast Bypass (Bit 31 == 0):    1.52 M ops/s  (656.0 ns/op)
  * Linear RAM Bandwidth:               5.81 MB/s

[Section 2: vMMIO Virtual Devices & Address Translation]
--------------------------------------------------------------------------------
  * Direct-Mapped TLB Hit (O(1)):       0.53 M ops/s  (1898.0 ns/hit)
  * Folding XOR Hash Calculation:       4.93 M ops/s  (203.0 ns/op)
  * TLB Miss -> FlatMap Walk (O(logN)): 0.38 M ops/s  (2623.4 ns/walk)
  * TLB Hit / FlatMap Walk Ratio:        1.38x
  * Static Syscall Dispatch (FC=0xC):   0.61 M ops/s  (1652.3 ns/dispatch)
  * RBAC Task Isolation Verification:   0.61 M ops/s  (1649.9 ns/check)

[Section 3: JIT Compiler & Runtime Dispatch]
--------------------------------------------------------------------------------
  * Copy-and-Patch Compile Speed:       13,302 Traces/sec  (75.18 us/trace)
  * Compile Cost per WASM Instruction:  18794.1 ns/opcode
  * 2-Bit Card Marking O(1) Check:      0.68 M ops/s  (1478.7 ns/check)
  * Sparse JIT Entry Binary Search:      0.79 M ops/s  (1271.2 ns/lookup)
  * Arithmetic Loop (100,000 iters):    Interp: 6082.13 ms | JIT: 1192.23 ms
  * Differential Result Check:          Interp=704,982,704 | JIT=704,982,704 (MATCH)
  * Measured JIT Speedup:               5.10x faster
  * PIC Trace-Header Helper Tail Jump:  0.12 M ops/s  (8049.2 ns/dispatch; 10,000 calls)

[Section 4: JIT Cache Metabolism & Corner Cases]
--------------------------------------------------------------------------------
  * Oldest-Only Promotion Invariant:    [PASS] (Warm hits=0 promos, Oldest hit=+1 promo)
  * Small Working Set (N=8 <= Active):  Hit Rate = 100.00%  (1.92 M lookups/s)
  * Medium Working Set (N=24 <= 3Bank): Hit Rate = 100.00%  (1.16 M lookups/s)
  * Large Working Set (N=100 Thrash):   Hit Rate = 92.44%  (0.12 M lookups/s)
  * Cache Metabolism & Churn Rate:      63,551 Evictions / Sec (4976 generations)
  * Dangling Chain Unlinking Safety:    [PASS] (All evicted traces unlinked cleanly)
  * Multi-Module UnifiedPC Collision:   [PASS] (Immunity verified between func_0 and func_1)

[Section 5: 3D Raytracing Ambient Occlusion (AO-Bench)]
--------------------------------------------------------------------------------
  * Resolution & Sampling:              32 x 16 (1,600 rays / frame)
  * Tier 3 Interpreter (Threaded CPS): 7466.05 ms  (214 Rays / Sec)
  * Tier 3 (Hybrid + JIT):              7028.35 ms  (228 Rays / Sec)
  * Measured Speedup:                   1.06x faster
  * Legacy JIT trace transitions:       15,482 / 82,053 (18.9%; old metric)
  * Active JIT Cache Bank Traces:       15 compiled traces
================================================================================
[PASS] All benchmarks completed successfully in 25.31 seconds.
```

Section 5の速度比はプロセス起動やOSスケジューリングの影響で実行毎に変動する（同日の別実行では1.16xも観測）。`bench_aobench.py --debug`（Section 4本文参照）では 6628.59 ms / 7680.15 ms = 1.16x だった。いずれも4スロット時代の1.00x前後から明確に改善しており、単発ノイズでは説明できない一貫した変化である。

---

## 3. ベンチマーク別詳細評価

### 3.1 Linear Memory & Guest RAM Access
- **境界チェック最適化**: 符号なし単一比較（`unsigned(addr + size) <= ram_size`）による境界判定は 7.41 M ops/s（135.0 ns/op）を記録。
- **vMMIO Fast Bypass**: アドレス最上位ビット判定（`addr & 0x8000_0000 == 0`）により、仮想デバイスを介さない通常のリニア RAM アクセスを $O(1)$ で直接バイパスし、1.52 M ops/s を記録。

### 3.2 vMMIO Virtual Devices & Address Translation
- **Direct-Mapped Folding XOR TLB**: Folding XOR ハッシュ計算は 4.93 M ops/s（203.0 ns）。ウォームアップ後のTLBヒットは 0.53 M ops/s（1898.0 ns）、33ページ作業集合によるFlatMap経路は 0.38 M ops/s（2623.4 ns）で、同一実行内の比率は 1.38x となった。ヒット計測区間は `tlb_hits == iterations` かつ `tlb_misses == 0` をassertしている。
- **RBAC & ゼロコピー所有権分離**: タスク間共有メモリ（FC=14）およびシステムコールディスパッチにおける RBAC 検証オーバーヘッドは 0.61 M ops/s（1649.9 ns/check）。`VmmioStatus.OWNER_MISMATCH` を全150,000回検出するassertを通過し、メモリ安全性を確認。

#### 3.2.1 TLB比較測定の妥当性注記
ベンチマークのPTE格納表を64件、TLBを32件として分離した。初期化は静的1ページ・SHM33ページ・passthrough16ページの計50件を登録し、全登録処理で容量超過を `assert` する。`TLB Miss -> FlatMap Walk` は32エントリを超える33ページの有効な作業集合を循環させる測定であり、実測カウンタは `tlb_hits=590,877`、`tlb_misses=9,123`、PTE登録件数は50件だった。これは33ページ中のハッシュ衝突を含むリフィル挙動の測定で、毎回のアクセスを強制ミスさせる値ではない。

### 3.3 JIT Compiler & Runtime Dispatch
- **Copy-and-Patch 高速コンパイル**: ネイティブステンシルのメモリコピーと固定パッチ位置への書き込みを 13,302 Traces/sec（75.18 us/trace）で実行。4命令ブロック換算で 18,794.1 ns/opcode であり、コードバッファ確保も含む。
- **疎なJITエントリ検索**: ソート済みJITエントリを二分探索し、0.79 M ops/s（1,271.2 ns/lookup）を計測した。JIT用Radix索引は使用しない。
- **算術演算ループ差分検証（旧Windows測定）**: 100,000 反復の算術ホットループにおいて、Tier 2 インタープリタ（6082.13 ms）に対して Tier 3 JIT（1192.23 ms）が **5.10x 高速化**し、演算結果（`704,982,704`）が一致した。この旧実装のループ測定値は現行の共通code chain dispatcher経路の性能値ではなく、chain回数を示す根拠にも使わない。
- **Cヘルパー境界**: トレースヘッダの `helper_target_addr` を共通ヘルパー領域から読み、JITフレーム復元後に末尾ジャンプする経路は 8,049.2 ns/dispatch。pysimの `ctypes` コールバックを含むABI回帰値であり、組込みCの性能値ではない。

実行ごとにOSスケジューリング等で時間が変動するため、単発値を絶対性能とは扱わず、同一実行条件内の比較値として扱う。

### 3.4 JIT Cache Metabolism & 3面ローテーション
- **3面リングバッファ代謝 (Active / Warm / Oldest)**:
  - Small Working Set（$N=8 \le$ Active 容量）: ヒット率 **100.00%**（1.92 M lookups/s）。
  - Medium Working Set（$N=24 \le$ 3Bank 合計容量）: ヒット率 **100.00%**（1.16 M lookups/s）。
  - Large Working Set（$N=100$ スラッシング負荷）: ヒット率 **92.44%**（0.12 M lookups/s）を維持。この計測はバンク常駐性（3面ローテーション）を対象とし、Direct-Mapped Folding XORキャッシュの4→16スロット化とは別軸のため数値は変化しない。
- **不変条件検証**:
  - `Oldest-Only Promotion Invariant`: Warm ヒットではプロモーション（Active へのコピー）を発生させず、Oldest ヒット時のみ昇格させることで無駄なキャッシュコピーを完全抑止。
  - `Dangling Chain Unlinking Safety`: バンク破棄時に破棄対象トレースを指す先行トレースの `chain_next` を $O(k)$ 有界時間でアンリンクし、ダングリングポインタを完全防止。

### 3.5 JIT カードエイジング（キャッシュ圧迫下のコールド関数混入）
- **ワークロード**: ホット 8 関数（16 トレース、約 2.4KB。3面キャッシュに収まる）と、コールド 200 関数を、200 パスで実行する。1 パスにつきコールド関数を 8 個ずつ順番に呼ぶ。実行コマンドは `uv run python experiments/pysim/benchmarks/jit/bench_jit_aging.py` である。
- **測定結果**（計数値は決定的に再現する。時間は各変種を3回実行した中央値で、実行環境で変わる）:

| 変種 | コンパイル数 | 追い出し数 | ローテーション数 | JIT 実行割合 | 総時間 (ms) | コンパイル時間 (ms) | エイジング時間 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ホット関数のみ（理想） | 16 | 0 | 1 | 84.6% | 382 | 1.7 | 0.00 |
| エイジングなし | 1,008 | 973 | 111 | 75.6% | 1,065 | 109.5 | 0.00 |
| エイジング U=1 O=4 | 913 | 873 | 101 | 76.2% | 1,018 | 99.4 | 4.33 |
| エイジング U=2 O=8（既定値） | 499 | 471 | 62 | 78.6% | 791 | 50.3 | 3.93 |
| エイジング U=8 O=32 | 127 | 97 | 22 | 80.6% | 628 | 12.2 | 4.66 |

- **読み方**:
  - エイジングなしでは、低頻度のコールド関数が 2 回目の呼び出しで `HOT` になり、コンパイルされてホットトレースを追い出す。
  - 既定値でコンパイル数が約 50% 減り、総時間は約 26% 短くなる（1,065 ms から 791 ms）。
  - 設定値によって効果が大きく変わる。U=8 O=32 では、コンパイル数と総時間が理想に近づく。
- **エイジングの処理時間**: 既定値では 1 ステップあたり約 63 us で、総時間の約 0.5% である。これは Python 実装（カード表を 1 枚ずつ走査する参照実装）の値であり、C++ 実装の値を表さない。
- **未評価**: コールド関数の間隔と関数数の組み合わせは、1 つの構成だけを測定している。ARMv8-Mのコンパイル時間、コード配置、実機性能はTBDである。

---

## 4. 3D AO-Bench & 旧JITトレース遷移診断

`bench_aobench.py --debug` 実行時に採取されたランタイム内部メトリクスおよび旧実装のtrace-link診断結果です。表のchain接続状態と18.9%の値は当時の実装・指標であり、現行共通code chain dispatcherの実行回数を表さない。

この測定では `JIT_CARD_SHIFT=2`（4バイト/カード）を使用し、既定の `min_trace_bytes` も4バイトです。

当時の接続率（18.9%）は旧実装のtrace-link状態から算出した値であり、現行x64物理ヘッダに`chain_next_pc`は存在しない。16スロット化が改善するのは「コンパイル済みトレースへ再入場する際の検索コスト」であり、履歴の接続率を現行chain dispatcherのヒット率と見なさない。

### 4.1 実行サマリー & キャッシュ状態
- **総ブロック実行数**: 164,770 回
  - インタープリタ実行: 82,717 回 (50.2%)
  - JIT ネイティブ実行: 82,053 回 (49.8%)
- **旧JIT trace-link診断**:
  - 当時「chain」と記録された連続JIT遷移: **15,482 回（18.9%）**
  - 当時の計測でInterpreter境界へ戻った回数: 66,571 回
  - いずれも現行共通code chain dispatcherの実行回数ではない。
- **JIT キャッシュ占有率**:
  - Active バンク: 15 トレース（1,763 / 2,048 バイト, 86.1%）
  - Warm バンク: 15 トレース（1,991 / 2,048 バイト, 97.2%）
  - Oldest バンク: 0 トレース（エビクション・プロモーション発生なし）

### 4.1.1 ホットスポット分類
- **Total Blocks in Module**: 88
- **Trackable JIT Candidates**: 34
- **Card Status Distribution**: `COMPILED=30`, `HOT=37`, `EXECUTED=1`, `UNEXECUTED=20`

### 4.2 旧実装のコンパイル済みtrace-link台帳
当時のAO実行では30トレースがActive/Warmに常駐し、15,482回の連続JIT実行と記録された。以下は旧実装の診断結果であり、現行共通code chain dispatcher専用カウンタによる計測ではない。

| バンク | Head PC | Next PC | LoopsTo | ChainNext | 実行回数 | チェイニング診断結果 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Warm | `0x10000` | `0x10009` | None | None | 35,949 | `[UNLINKED]` Target `0x10009` uncompilable (unsupported stencil / non-trackable) |
| Warm | `0x30000` | `0x3000B` | `0x30007` | None | 910 | `[UNLINKED]` Target `0x3000B` resident but unlinked |
| Warm | `0x3000B` | `0x30020` | None | None | 903 | `[UNLINKED]` Target `0x30020` not compiled |
| Warm | `0x30022` | `0x3002A` | `0x30035` | None | 7,682 | `[UNLINKED]` Target `0x3002A` resident but unlinked |
| Warm | `0x3002A` | `0x30022` | None | `0x30022` | 6,778 | `[CHAINED]` -> `0x30022` in Warm |
| Warm | `0x3003E` | `0x3005D` | `0x30048` | None | 7,690 | `[UNLINKED]` Target `0x3005D` resident but unlinked |
| Warm | `0x30048` | `0x30065` | None | `0x30065` | 3,657 | `[CHAINED]` -> `0x30065` in Warm |
| Warm | `0x3005D` | `0x30065` | None | `0x30065` | 4,033 | `[CHAINED]` -> `0x30065` in Warm |
| Warm | `0x30065` | `0x30039` | None | None | 7,691 | `[UNLINKED]` Target `0x30039` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x50030` | `0x50035` | None | None | 112 | `[UNLINKED]` Target `0x50035` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x5005C` | `0x50061` | None | None | 30 | `[UNLINKED]` Target `0x50061` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x50088` | `0x5008D` | None | `0x5008D` | 29 | `[CHAINED]` -> `0x5008D` in Warm |
| Warm | `0x5008D` | `0x500B3` | `0x50094` | None | 1,598 | `[UNLINKED]` Target `0x500B3` resident but unlinked |
| Active | `0x500AD` | `0x500B3` | None | `0x500B3` | 205 | `[CHAINED]` -> `0x500B3` in Warm |
| Warm | `0x500B3` | `0x500C2` | `0x500BE` | None | 1,598 | `[UNLINKED]` Target `0x500C2` uncompilable (unsupported stencil / non-trackable) |
| Warm | `0x6000D` | `0x60014` | `0x601B5` | None | 15 | `[UNLINKED]` Target `0x60014` resident but unlinked |
| Warm | `0x60014` | `0x6001A` | None | None | 14 | `[UNLINKED]` Target `0x6001A` uncompilable (unsupported stencil / non-trackable) |
| Warm | `0x6001C` | `0x60023` | `0x6019C` | None | 526 | `[UNLINKED]` Target `0x60023` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x600DC` | `0x600E4` | None | None | 247 | `[UNLINKED]` Target `0x600E4` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x600FF` | `0x60107` | None | None | 232 | `[UNLINKED]` Target `0x60107` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x60122` | `0x6012A` | None | None | 234 | `[UNLINKED]` Target `0x6012A` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x60145` | `0x6014D` | None | `0x6014D` | 255 | `[CHAINED]` -> `0x6014D` in Active |
| Active | `0x6014D` | `0x6015E` | `0x60159` | None | 270 | `[UNLINKED]` Target `0x6015E` resident but unlinked |
| Active | `0x6015E` | `0x6016A` | `0x60165` | None | 270 | `[UNLINKED]` Target `0x6016A` resident but unlinked |
| Active | `0x60165` | `0x6016A` | None | `0x6016A` | 7 | `[CHAINED]` -> `0x6016A` in Active |
| Active | `0x6016A` | `0x60176` | `0x60171` | None | 270 | `[UNLINKED]` Target `0x60176` resident but unlinked |
| Active | `0x60171` | `0x60176` | None | `0x60176` | 8 | `[CHAINED]` -> `0x60176` in Active |
| Active | `0x60176` | `0x60182` | `0x6017D` | None | 270 | `[UNLINKED]` Target `0x60182` uncompilable (unsupported stencil / non-trackable) |
| Active | `0x6017D` | `0x60182` | None | None | 60 | `[UNLINKED]` Target `0x60182` uncompilable (unsupported stencil / non-trackable) |
| Warm | `0x6018A` | `0x6001C` | None | `0x6001C` | 510 | `[CHAINED]` -> `0x6001C` in Warm |

---

## 5. シミュレータ性能特性と C++23 実機実装への予測

### 5.1 Python シミュレータ上での特性分析
2026-09-17の旧ネイティブCPS経路では、Tier 2が `7235.07 ms`、Tier 3が `7545.16 ms`、速度比は `0.96x` となった。各命令の意味論は既存Pythonハンドラ本体で実行した。Tier 3の共有8KBコード領域へのトレース再配置後も、描画結果は528バイトで完全一致した。今回の単一測定値は同一環境での比較値としてのみ扱う。
1. **JITトレースのC++ブリッジ境界**:
   - 通常命令ではC関数ポインタ表から次ハンドラへ継続し、基本ブロック境界およびジャンプ・分岐・呼出し・戻りでインタープリタ境界へ戻る。今回の統合ベンチマーク出力ではCPS継続回数は個別に公開していないため、チェイン率は主張しない。
2. **オンデマンド・コンパイルと動的解決**:
   - Tier 3ではホットスポット到達時の Copy-and-Patch コンパイル処理が同一スレッド内で逐次実行されるため、フレーム所要時間に含まれる。

### 5.2 ARMv8-M実機性能・資源見積り（TBD）

ARMv8-Mの速度、RAM/ROM適合性、JIT命令列、実機サイクル数はTBDである。x64の測定結果はARMv8-Mへ外挿しない。

## 6. 2026-09-24 Linux 全ベンチマーク再測定

### 6.1 実行環境と手順

- **環境**: Ubuntu 26.04.1 x86-64、CPython 3.14.6、uv 0.12.18、Clang 21.1.8。
- **拡張**: `_interpreter_native` と `native_trace_call` を Clang でビルドした。後者はJITコンパイラが必須 import する拡張であり、インタープリタ拡張とともに事前ビルドする。
- **実行**: リポジトリ内の`.venv`を`uv run --python .venv/bin/python`で指定し、オフラインで実行した。依存同期と一時依存の導入は行っていない。
- **VTune**: Intel VTune Profiler 2026.4.0 の Hotspots をソフトウェアサンプリングで収集した。その収集時の `kernel.yama.ptrace_scope` は0だった。`perf_event_paranoid=4` とサンプリングドライバの未導入により、当時はハードウェアイベントとマイクロアーキテクチャ解析を利用できなかった。

### 6.2 ベンチマーク結果

| 系統 | 指標 | 報告値 |
| :--- | :--- | :--- |
| リニアメモリ | 32-bit 読み書き | 9.58 M ops/s（104.4 ns/op） |
| リニアメモリ | vMMIO RAM バイパス | 3.96 M ops/s（15.09 MB/s） |
| vMMIO | TLB hit / FlatMap miss | 1.35 / 1.19 M ops/s（744.0 / 844.5 ns） |
| vMMIO | 静的デバイス dispatch / RBAC 検証 | 1.49 / 1.49 M ops/s |
| JIT | Copy-and-Patch コンパイル | 55,212 traces/s（18.12 µs/trace） |
| JIT | 2-bit カード判定 / 疎lookup | 1.67 / 2.04 M ops/s |
| JIT | 算術ループ 100,000 回 | Pythonハンドラ 4,385.38 ms / C++インタープリタ 5.55 ms / Hybrid JIT 713.25 ms（3プロセス中央値） |
| JIT | 3バンク大作業集合 hit rate | 92.44% |
| JITエイジング | 既定設定（U=2, O=8） | 499 compiles、471 purges、62 rotations、518.5 ms |
| AO-Bench | インタープリタ / Hybrid JIT | 5,557.60 / 5,121.67 ms（1.09x） |

### 6.3 測定値の解釈

6.2節の算術ループ値は直接マップキャッシュC++化前の基準値であり、`bench_jit.py`を別プロセスで3回起動し、各プロセス内3回測定の中央値をさらに中央値化した。3経路すべてで`704,982,704`が一致した。Pythonハンドラに対してHybrid JITは約6.15倍速いが、C++インタープリタはHybrid JITより約128.5倍速かった。この差だけでは遅さの原因を特定できない。特に、PythonのJIT lookupが支配要因だとは判断しない。

JIT経路は各C++トレース呼出しの後にPythonの`RuntimeEngine`へ復帰し、Tier 3 managerから次の実行先を選ぶ。6.2節の旧診断計測では、trace body実行2,000,196回、旧定義のchain hit 1,000,093回、RuntimeEngine復帰1,000,103回、Interpreter step 48回だった。これらの旧chain hit値は現行の共通code chain dispatcher計数ではない。チェイニング適格性とスケジューラへの協調yield条件は変更していない。C++ Tier 2 composerのビルド検査は構成型選択を検証する。pysimの実際の実行ドライバとJIT lookupは引き続きPythonにある。

AO-Benchは描画結果が一致し、今回の環境ではHybrid JITが約1.09x高速だった。ワークロードにより結果が異なるため、算術ループ側のホストディスパッチとJITトレースの実コストを別途プロファイルする必要がある。

### 6.4 VTune Hotspots の利用状況（現行コードは未収集）

VTune Hotspots はPythonを含むプロセスツリー向けのソフトウェアサンプリングを選択できる。使用版の既定サンプリング間隔は10 msである。

以下のVTuneサンプル表は以前の実装を対象にした記録である。直接マップキャッシュC++化後の再収集も開始できなかった。`kernel.yama.ptrace_scope=0`を確認して再試行したが、VTuneはloopback network interfaceが利用できないと報告した。現行コードのHotspots結果は未取得である。

算術ループの各実行経路だけを反復する [`profile_arithmetic_path.py`](jit/profile_arithmetic_path.py) を追加した。各回で期待結果を照合し、Hybrid JITではトレース実行回数も検査する。Pythonハンドラ、C++インタープリタ拡張、Hybrid JITを別々の収集対象にできる。

2026-09-24に、リポジトリの`.venv`をuvから選択して同じCPython 3.14.6で3経路を収集した。各経路の実行前に`uv run --offline`で結果を検証し、Pythonハンドラを1回、C++インタープリタ拡張を512回、Hybrid JITを4回実行した。すべて `704,982,704` を返し、Hybrid JITでは800,190回のトレース実行と30回のインタープリタstepを記録した。

Hotspotsの上位サンプルは次のとおりである。CPU時間と割合は各プロファイル内のVTuneソフトウェアサンプリング値であり、通常実行の速度比較には使わない。

| 経路 | 上位ホットスポット | CPU時間割合 |
| :--- | :--- | :--- |
| Pythonハンドラ | `_TAIL_CALL_LOAD_ATTR_SLOT`、`_TAIL_CALL_CALL_LEN`、`_TAIL_CALL_LOAD_GLOBAL_BUILTIN`、`vectorcall_method`、`_TAIL_CALL_RETURN_VALUE` | 7.7%、5.1%、4.9%、4.6%、4.4% |
| C++インタープリタ | `h_local_get`、`h_binary`、`h_local_set`、`h_br`、`h_i32_const` | 35.0%、21.8%、19.4%、5.4%、5.1% |
| Hybrid JIT | `_TAIL_CALL_LOAD_ATTR_SLOT`、`_TAIL_CALL_RETURN_VALUE`、`_TAIL_CALL_CALL_PY_EXACT_ARGS`、`_TAIL_CALL_LOAD_GLOBAL_BUILTIN`、`_TAIL_CALL_COMPARE_OP_INT` | 9.9%、9.2%、5.6%、3.9%、3.9% |

C++インタープリタ経路では、命令ハンドラのローカル変数取得、二項演算、ローカル変数設定が主要なサンプルになった。Hybrid JIT経路ではVTuneがCPythonのディスパッチ関数を上位に表示し、JIT生成コードの関数名は解決されなかった。ネイティブ拡張のデバッグ情報もないため、この収集だけではJIT機械語内部のホットスポットを特定できない。

このVTune収集時には `kernel.yama.ptrace_scope=0` を確認した。`perf_event_paranoid=4` で、VTuneからサンプリングドライバ未導入の警告も出たため、得られたのはユーザーモードのソフトウェアサンプリングである。マイクロアーキテクチャ解析やハードウェアイベントの根拠として扱わない。

再実行コマンドは次のとおりである。今回の最終プロファイルは `/tmp/pysim-vtune-step0-uv3146-rebuilt-<path>` に保存した。

```bash
export UV_CACHE_DIR=/tmp/fireball-uv-cache
VTUNE=/opt/intel/oneapi/vtune/2026.4/bin64/vtune
run_id="$(date +%Y%m%d-%H%M%S)-$$"
for path in python-handler native-interpreter hybrid-jit; do
  result_dir="/tmp/pysim-vtune-${run_id}-${path}"
  "$VTUNE" -collect hotspots -knob sampling-mode=sw \
    -knob enable-stack-collection=true -result-dir "$result_dir" -- \
    uv run --offline --no-sync python -u \
    experiments/pysim/benchmarks/jit/profile_arithmetic_path.py --path "$path"
  "$VTUNE" -report hotspots -result-dir "$result_dir" \
    -report-output "/tmp/pysim-vtune-${run_id}-${path}-hotspots.txt"
done
```

### 6.5 AMD uProf Hotspots / IBS（直接マップC++化前）

AMD uProf 5.3.521.0で直接マップキャッシュC++化前のHybrid JIT経路を各12回実行し、HotspotsとIBSを個別に収集した。CPUはAMD Ryzen 5 5500GT with Radeon Graphics（Family 25 / Model 80）、CPythonは3.14.6である。各収集で結果`704,982,704`を照合し、trace body 2,400,198回、旧定義のchain hit 1,200,093回、RuntimeEngine復帰1,200,105回、Interpreter step 54回を記録した。このchain値は現行共通code chain dispatcherの計数ではない。

Hotspotsは10 ms間隔で9.862秒を採取した。全体CPU時間9.32秒のうち、上位CPythonアドレスはシンボル未解決であり、`PyCField_get`は自己時間0.10秒、子を含め0.32秒だった。IBSは約10秒でPythonプロセスから612個の`IBS_ALL_OPS`サンプルを得た。内訳の上位はC++ `run_native_step` 147サンプル、`h_br_if` 48サンプルだった。サンプル数は関数別の精密なコスト比較には不足し、生成JIT機械語の命令別内訳も解決されていない。IBSレポートにはカーネル`kallsyms`アドレス不一致警告が出た。

レポートは`/tmp/pysim-uprof-current-hotspots`と`/tmp/pysim-uprof-current-ibs`に保存した。再実行コマンドは次のとおりである。

```bash
export UV_CACHE_DIR=/tmp/fireball-uv-cache
export UV_OFFLINE=true
export UV_NO_SYNC=true
UPROF=/opt/AMDuProf_5.3-521/bin/AMDuProfCLI
for config in hotspots ibs; do
  "$UPROF" profile --config "$config" -g --detail \
    -o "/tmp/pysim-uprof-current-${config}" \
    uv run --offline --no-sync python \
    experiments/pysim/benchmarks/jit/profile_arithmetic_path.py \
    --path hybrid-jit --repetitions 12
done
```

### 6.6 動的 WASM 命令あたりのホストCPUサイクル数（直接マップC++化前）

Linuxの`perf stat`で`cycles:u`を1イベントだけ計測した旧スナップショットである。再計測日は2026-09-24。ホストはAMD Ryzen 5 5500GT with Radeon Graphics、論理CPU 2へ固定した状態である。実行時の`perf`は7.0.14、`kernel.perf_event_paranoid`は1だった。FIFO制御で各試行の呼び出しバッチ中だけカウンタを有効にし、準備とJITウォームアップは除いた。全試行で結果`704,982,704`を確認した。Hybrid JITの10呼出しでは2,000,196 trace body実行、旧定義のchain hit 1,000,093回、1,000,103回のRuntimeEngine復帰、48回のInterpreter stepを記録した。このchain値を現行code chainの回数として扱わない。

`heavy_loop(100000)` は動的に1,300,012 WASM opcodeを実行する。これは初期化4命令、block/loop 2命令、ループ条件4命令を100,001回、ループ本体9命令を100,000回、終了時2命令の合計である。サイクル数を`wasm_dynamic_instructions`で割り、1 WASM opcodeあたりの平均ホストサイクル数を求めた。各行は独立プロセス3回の試行値と中央値である。

| 経路 | 反復数 / 試行 | 動的 WASM opcode / 試行 | 3試行のcycles/opcode | 中央値 |
| :--- | ---: | ---: | :--- | ---: |
| Pythonハンドラ | 1 | 1,300,012 | 14,577.0 / 14,658.4 / 14,718.8 | 14,658.4 |
| C++インタープリタ | 128 | 166,401,536 | 17.945 / 18.657 / 21.743 | 18.657 |
| Hybrid JIT | 10 | 13,000,120 | 2,353.8 / 2,384.3 / 2,422.4 | 2,384.3 |

この旧構成ではHybrid JITがC++インタープリタより約128倍のホストサイクル/opcodeを要した。JIT trace終端ごとにRuntimeEngineへ復帰し、次のlookupをPython製Tier 3 managerが行う経路は残る。C++制御handlerの直接呼出しは対象テストとIBSサンプルで確認したが、10回の計測で約100万回のRuntimeEngine復帰があり、実行ループ全体のコストが支配的である。値にはPython側の呼出し・検索・実行制御も含まれ、JITが複数のWASM命令をまとめて実行する分を動的WASM opcode数で正規化している。CPU・周波数・OSが異なる値は直接比較せず、組込みCPUの性能値にも読み替えない。

同じ測定を再現するコマンドは[JITベンチマーク仕様書](../../docs/components/tier3_executer/benchmarks/jit_runtime_bench_spec.md)の「Linux ホストサイクル数 / 動的 WASM 命令」に記載した。

### 6.7 JIT 直接マップキャッシュのC++化後

固定16スロットのFolded-XOR直接マップ検索と値の所有をC++拡張へ移した後、`bench_jit.py`をuv経由でCPU 2に固定して3プロセス実行した。各プロセス内の3試行中央値を、下表でさらにプロセス間中央値化した。拡張はClangで再ビルド済みであり、算術結果は全経路で`704,982,704`と一致した。

| 経路 | 各プロセスの中央値 (ms) | 3プロセス中央値 | Pythonハンドラからの速度比 |
| :--- | :--- | ---: | ---: |
| Pythonハンドラ | 6,037.96 / 6,113.20 / 5,223.27 | 6,037.96 ms | 1.00x |
| C++インタープリタ | 5.96 / 7.86 / 6.00 | 6.00 ms | 約1,006x |
| Hybrid JIT | 1,195.19 / 1,103.65 / 973.78 | 1,103.65 ms | 約5.47x |

CPUを固定しても試行間の時間差が大きい。6.2節の以前の値はCPU固定なしの別測定なので、この2つの測定からC++化による総合性能の増減を結論づけない。Hybrid JITの中央値はC++インタープリタの約184倍の時間を要したが、総実行時間の差だけから原因は特定できない。Hybrid JITはこの測定で200,187回のトレース本体を実行し、そのうち100,093回を当時のmetadata-based「ネイティブチェイン」指標で記録した。この値は現行共通chain dispatcherの実行回数ではない。Interpreter stepは21回だった。cycles/opcodeとuProfプロファイルも未取得である。`kernel.yama.ptrace_scope=0`でも、ホスト設定`kernel.perf_event_paranoid=4`がLinux perfとAMD uProfを拒否した。AMD uProfのHotspotsには3以下、IBSには1以下が必要である。これらの権限設定はベンチマーク実行中に変更していない。

算術ループの3プロセス測定は次の手順で再実行できる。

```bash
export UV_CACHE_DIR=/tmp/fireball-uv-cache
export UV_OFFLINE=true
export UV_NO_SYNC=true
for trial in 1 2 3; do
  taskset -c 2 uv run --offline --no-sync python \
    experiments/pysim/benchmarks/jit/bench_jit.py
done
```

固定PCのヒットだけを測る局所ベンチ [`bench_fast_cache.py`](jit/bench_fast_cache.py) では、CPU 2へ固定し、1,000,000 lookupを5回行った中央値がC++ `FastCache`で62.1 ns、Python `StaticVector`のタプルスロットで173.8 nsだった（比0.358）。両経路が同一のtrace objectを返すことを各試行後に確認した。これは直接マップのヒット処理だけを比較した合成測定であり、フルループでの速度向上や残るボトルネックを示す値ではない。再測定は次のコマンドで行う。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  taskset -c 2 uv run --offline --no-sync python \
  experiments/pysim/benchmarks/jit/bench_fast_cache.py
```

### 6.8 共通RuntimeEngine境界への統合後の測定（2026-09-25）

> この節は後方分岐しきい値でC++ dispatchを続ける実装より前の履歴値である。旧測定ではtrace境界ごとにRuntimeEngineへ復帰しており、以下の現行6.9節との速度・cycles比較には使わない。

`RuntimeEngine.run()`を1トレース境界だけ進める共通処理にし、同期呼出しは`SYNCHRONOUS`構成、COOS実行は`COOS`構成に分けた。COOS側は`System.run_guest()`が同じ境界処理の後に世代観測とYieldを行う。ベンチマークのHybrid JIT経路は仕様どおり`JITInterpreter.call()`を通し、C++インタープリタ経路は`Interpreter.call()`を通した。COOSスケジューラはこの算術ループ比較へ混ぜていない。

実行環境はUbuntu 26.04.1、AMD Ryzen 5 5500GT with Radeon Graphics（Family 25 / Model 80）、uv経由のCPython 3.14.6である。拡張は作業ツリーにあるClangビルドを使用した。`kernel.perf_event_paranoid=1`、`kernel.yama.ptrace_scope=0`を確認し、全Python実行は`uv run --offline --no-sync`で行った。

仕様の3経路ベンチマークを1プロセスで実行した結果を示す。各経路はプロセス内の3測定値の中央値である。

| 経路 | 100,000反復の実行時間 | 結果 |
| :--- | ---: | ---: |
| Pythonハンドラ | 4,513.81 ms | `704,982,704` |
| C++インタープリタ | 5.89 ms | `704,982,704` |
| Hybrid JIT | 970.14 ms | `704,982,704` |

同じベンチマーク中のHybrid JITは200,187回のトレース起動、100,093回の旧定義のチェインヒット、21回のInterpreter stepを記録した。個別のcycles計測ではHybrid JITを10回実行し、2,000,196回のトレース起動、1,000,093回の旧定義のチェインヒット、1,000,103回のRuntimeEngine復帰、48回のInterpreter stepを記録した。ここでのチェイン値は現行共通code chain dispatcherの実行回数ではない。

`perf stat`で`cycles:u`を各呼出しバッチだけ有効にし、3独立プロセスから算出した動的WASM命令あたりの値は以下のとおりである。各列の平均と中央値は3試行のcycles/opcodeから算出した。

| 経路 | 反復数 / 試行 | 動的WASM opcode / 試行 | 3試行のcycles/opcode | 平均 | 中央値 |
| :--- | ---: | ---: | :--- | ---: | ---: |
| Pythonハンドラ | 1 | 1,300,012 | 14,234.30 / 14,179.93 / 14,327.59 | 14,247.27 | 14,234.30 |
| C++インタープリタ | 128 | 166,401,536 | 17.78 / 17.87 / 18.27 | 17.97 | 17.87 |
| Hybrid JIT | 10 | 13,000,120 | 3,083.58 / 3,062.58 / 3,148.36 | 3,098.17 | 3,083.58 |

この計測ではHybrid JITはC++インタープリタの約172倍のcycles/opcodeを要した。一方、Hybrid JITのサイクル数はPythonハンドラより約4.6倍少ない。perfカウンタの分母は同一WASM入力から計算した動的命令数であり、JITがまとめて実行した命令も数えている。これはpysimプロセスのホストCPU上の測定値で、組込みCPUの性能値ではない。

AMD uProf 5.3.521.0のHotspotsをHybrid JIT 12回で取得した。報告されたプロセスCPU時間は11.17秒で、Python実行ファイルに11.02秒、`_jit_cache_native.so`に0.02秒、`_interpreter_native.so`に0.01秒だった。Python実行ファイル内の関数シンボルは大半がアドレス表示となる。uProfは`uv`をTarget Pathとして記録したが、対象PythonプロセスのCPU時間を計上した。このため、サンプルはPython側の実行が大半を占めることを示す粗い根拠としてのみ扱い、特定のRuntimeEngineメソッドやlookup関数のコストには割り当てない。詳細レポートは`/tmp/pysim-uprof-hotspots.dcsJU9/hotspots/report.csv`にある。IBSはこの更新後コードでは未収集である。

実測から確認できる差は、通常のC++ Interpreter呼出しが`_call_without_nested_calls()`でnative `run_step`とPython handlerを進める一方、Hybrid JITは各JIT traceの終端後に約10万回RuntimeEngineへ復帰し、Python側で次境界のlookup・スタック適合確認・実行先選択を行うことである。JIT終端の制御命令はC++ Interpreter handlerを呼ぶが、uProfのmodule集計では`_interpreter_native.so`は0.01秒、Python実行ファイルは11.02秒だった。したがって測定はC++制御handler自体が主因という仮説を支持せず、Python側の境界実行とTier 3 manager lookupが有力候補である。ただしuProfの関数シンボル解決が不十分なため、その候補間の内訳は未確定である。チェイニング対象やOS協調境界は変更していない。

再現コマンドは[JITベンチマーク仕様書](../../docs/components/tier3_executer/benchmarks/jit_runtime_bench_spec.md)のLinux `perf stat`節を使う。通常の速度値は`bench_jit.py`、cycles/opcodeは同仕様のFIFO制御手順から取得する。

### 6.9 C++ dispatchの後方分岐yield境界を揃えた測定（2026-09-25）

> この節は診断カウンタの収集が常時有効で、dispatcher snapshotのキャッシュとhotspot profiling切替を入れる前の履歴値である。chain hit 0は当時のmachine-code direct-linkカウンタであり、現行chain定義の実行回数ではない。同一dispatcher内のtrace継続回数は別に数えていない。最新条件との速度比較には6.10節を使う。

C++ InterpreterとHybrid JITの両方を`FB_CONF_RUNTIME_YIELD_THRESHOLD=16`で実行し、C++ handlerが取得済み後方分岐を数え、16回ごとにnative dispatchからPythonへ戻るよう揃えた。Hybrid JITは同じC++ dispatcher内で常駐traceとInterpreter handlerを進める。C++ Interpreter単独経路も同じ回数条件を使う。未対応命令・外部呼出し・trap・関数完了は意味上必要な早期境界である。この旧スナップショットでは旧machine-code direct-linkカウンタが0だった。これは現行chain dispatcherの実行回数を測っていない。

ベンチマークはClang拡張を再ビルドした後、`uv run --offline --no-sync`とCPU 2固定で3独立プロセス実行した。各プロセス内3試行の中央値を採り、さらに3プロセスの中央値を示す。ホストはAMD Ryzen 5 5500GT with Radeon Graphics、Linux x86-64である。全経路のWASM結果は`704,982,704`で一致した。

| 経路 | 100,000反復の実行時間 | Python handlerからの速度比 | 結果 |
| :--- | ---: | ---: | :--- |
| Pythonハンドラ | 4,489.13 ms | 1.00x | `704,982,704` |
| C++ Interpreter | 13.82 ms | 324.75x | `704,982,704` |
| Hybrid JIT | 96.60 ms | 46.47x | `704,982,704` |

この測定でHybrid JITはC++ Interpreterより6.99倍遅かった。各試行でtrace invocationは200,187回、旧machine-code direct-linkカウンタは0回、Interpreter stepは21回だった。結果は今回の同一条件でC++ InterpreterとHybrid JITの復帰条件を揃えたうえでの実測であり、C++ dispatch構成でもHybrid JITがまだC++ Interpreterより遅いことを示す。時間値だけから残る原因は特定しない。旧direct-linkカウンタは現行chain実行回数ではない。

`cycles:u`は取得していない。現在の`kernel.perf_event_paranoid`は4であり、Linux perfのハードウェアカウンタ利用を阻むため、カーネル設定は変更せず実時間測定だけを行った。Intel VTune・AMD uProfの手順は仕様書に残している。サイクル/opcodeが必要な場合は、管理者設定を許可値へ変更した後、同じFIFO制御手順で再計測する。

### 6.10 Hotspot観測を構成で切り替えた算術ループ測定（2026-09-25）

JITコンパイラ本体はClangでビルドしたC++拡張であり、今回の算術ループ測定は常駐traceを事前に準備してコンパイル処理を計時区間から除いた。調べたかったのはPython側JIT managerの動的hotspot観測と、RuntimeEngineの境界処理である。診断カウンタ`FB_CONF_RUNTIME_PROFILE_STATS`は既定OFFとし、速度・cyclesの計時中も無効にした。dispatcher snapshotはcache世代またはtrackable mask世代の変更時だけ構築し、`max_chain_stack_words`と候補PC表を各yieldで再走査する処理を除いた。

Hotspot profilingのON/OFFを切り替え、各プロセスで100,000反復を10回実行した。各試行でWASM結果`704,982,704`、動的命令13,000,120、LOOP後方分岐yieldしきい値16を確認した。統計カウンタは計時中OFFである。AMD Ryzen 5 5500GT、Linux x86-64、CPython 3.14.6、CPU 2固定で実行した。

| Hotspot profiling | 3試行の実時間（10回分） | 中央値（1反復あたり） | cycles/opcode（3試行） | 平均 | 中央値 |
| :--- | :--- | ---: | :--- | ---: | ---: |
| 有効 | 316.745 / 326.225 / 328.620 ms | 32.62 ms | 98.68 / 101.30 / 99.15 | 99.71 | 99.15 |
| 無効（trace準備後） | 213.722 / 216.413 / 205.360 ms | 21.37 ms | 65.27 / 65.93 / 66.06 | 65.76 | 65.93 |

profile観測を無効にした場合、10反復バッチの中央値は34.5%短縮し、cycles/opcode中央値は33.5%減少した。無効化しても常駐trace実行、C++分岐handler、後方分岐しきい値16でのCOOS復帰は維持される。一方、未コンパイルblockの観測、hotness yield、新しい動的コンパイル判断は止まる。これは診断カウンタや外部VTune/uProfのON/OFFではなく、JITの動的hotspot観測機能のON/OFF比較である。

同じCPU・しきい値・命令列で取得した3経路の`cycles:u`を示す。全経路で統計カウンタをOFFにして各経路の正解値を照合した。

| 経路 | 反復数 / 試行 | 動的WASM opcode / 試行 | 3試行のcycles/opcode | 平均 | 中央値 |
| :--- | ---: | ---: | :--- | ---: | ---: |
| Python handler | 1 | 1,300,012 | 14,864.24 / 15,234.33 / 15,441.71 | 15,180.09 | 15,234.33 |
| C++ Interpreter | 128 | 166,401,536 | 44.14 / 43.84 / 44.16 | 44.05 | 44.14 |
| Hybrid JIT、hotspot有効 | 10 | 13,000,120 | 98.68 / 101.30 / 99.15 | 99.71 | 99.15 |
| Hybrid JIT、hotspot無効 | 10 | 13,000,120 | 65.27 / 65.93 / 66.06 | 65.76 | 65.93 |

hotspot観測無効後もHybrid JITはC++ Interpreterよりcycles/opcode中央値で1.49倍遅い。よってhotspot観測の負荷は大きいが、JITとC++ Interpreterの差をすべて説明しない。uProfの関数シンボルが十分に解決されなかったため、残差を個別関数へ割り当てない。

計時区間外で取得したPython `cProfile`は候補を絞る補助証拠である。観測有効時は`RuntimeEngine._run_jit_boundary`が6,260回、Tier 3 managerの`lookup`が6,258回呼ばれ、lookupの累積時間は約0.043秒だった。観測無効時は候補マスク・カード状態を調べる処理を通らない。cProfileの実時間値は通常性能比較に使わない。snapshot cacheを入れる前は`native_dispatch_state`が6,258回呼ばれ累積0.297秒、そのうち`max_chain_stack_words`が0.111秒だった。変更後、このsnapshot再構築は世代が変わらない通常境界で再実行しない。

意味上の遷移数はON/OFFで同じだった。診断実行10反復分ではtrace invocation 2,000,010回、C++ handler後に同一dispatcher内で次のJIT traceへ進んだ回数1,937,510回、RuntimeEngine境界62,510回だった。この1,937,510は`native_dispatch_trace_transitions`に対応するC++ dispatcher指標であり、chainではない。現行chainはtrace末尾から共通コード領域へ入り、chain dispatcherが次trace bodyへtail-jumpする経路である。その専用カウンタはなく、この節の診断実行ではchain回数を取得していない。

### 6.11 共通code chain dispatcher更新後のcycles測定（2026-09-25）

共通コード領域にchain dispatcherを配置し、trace末尾から共通入口へ相対分岐する更新後のコードを測定した。Intel/AMD共通の`perf stat cycles:u`で、CPU 2へ固定し、FIFO制御により実行バッチ中だけカウンタを有効にした。ホストはAMD Ryzen 5 5500GT with Radeon Graphics、Linux x86-64、`perf` 7.0.14、`kernel.perf_event_paranoid=1`である。Clang拡張は通常設定でビルドし、診断カウンタはコンパイルから除外した。hotspot profilingは既定の有効設定、後方分岐yieldしきい値は16である。

Python handlerは100,000反復を1回、C++ Interpreterは128回、Hybrid JITは10回実行した。各呼出しの動的命令数は1,300,012で、全試行の結果は`704,982,704`だった。C++ InterpreterとHybrid JITは共通しきい値16を使った。

| 経路 | 実時間（試行ごと、バッチを1呼出しへ換算） | cycles/opcode（3試行） | 平均 cycles/opcode | 中央値 |
| :--- | :--- | :--- | ---: | ---: |
| Python handler | 4,458.128 / 4,599.736 / 4,667.609 ms | 14,441.92 / 14,948.81 / 15,141.12 | 14,843.95 | 14,948.81 |
| C++ Interpreter | 14.566 / 15.425 / 14.547 ms | 47.26 / 49.84 / 46.99 | 48.03 | 47.26 |
| Hybrid JIT、hotspot有効 | 31.579 / 30.839 / 31.112 ms | 101.98 / 99.73 / 100.76 | 100.82 | 100.76 |

この条件ではHybrid JITはC++ Interpreterより中央値cycles/opcodeで2.13倍多く、呼出し時間で2.14倍遅かった。Python handlerと比べると約147.8倍短い。今回の算術ループは分岐をC++ Interpreter handlerへ委譲する計測であり、共通code chain dispatcherの通過回数や性能を測っていない。chain dispatcher経路の動作はJIT QAで検証したが、専用実行カウンタはなく、chain回数とchain単体の性能値は未取得である。

再現には[JITベンチマーク仕様書](../../docs/components/tier3_executer/benchmarks/jit_runtime_bench_spec.md)のLinux `perf stat`手順を使う。今回の計測値は、共通dispatcher更新後に同じ算術ループ条件で取り直した値であり、6.10節以前の履歴値と混ぜない。

### 6.12 現行コードでの全6系統再測定（2026-09-25）

AMD Ryzen 5 5500GT、Linux 7.0.0-34-generic x86-64、CPython 3.14.6、uv 0.12.18で測定した。既存のネイティブ拡張を使用し、実行統計はコンパイル時に無効だった。CPU固定とハードウェアカウンタ収集は行っていない。次のコマンドで6系統が完走し、実行時間は32.77秒だった。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache uv run --offline --no-sync python experiments/pysim/benchmarks/run_all.py
```

| 系統 | 指標 | 今回の実測値 |
| :--- | :--- | :--- |
| リニアメモリ | 32-bit読み書き / vMMIO RAMバイパス | 9.23 / 3.98 M ops/s（バイパス帯域15.18 MB/s） |
| vMMIO | TLB hit / FlatMap経路 | 1.34 / 1.16 M ops/s（同一実行内比1.16x） |
| JIT | Copy-and-Patchコンパイル | 59,801 traces/s（16.72 µs/trace） |
| JIT | 算術ループ100,000反復 | Python handler 4,399.24 ms / C++ Interpreter 14.47 ms / Hybrid JIT 29.83 ms |
| JITキャッシュ | 大作業集合のhit rate | 92.44% |
| JITエイジング | 既定設定（U=2、O=8） | 656 compiles / 605 purges / 50 rotations / 453 ms |
| AO-Bench | Interpreter / Hybrid JIT（全スイート中の1回） | 5,733.03 / 6,326.06 ms（Hybrid JITは約1.10倍の時間） |

算術ループの3経路はすべて`704,982,704`を返した。Hybrid JITはPython handlerより147.48倍速く、C++ Interpreterより2.06倍の時間を要した。これらは同一ホストのpysim実行時間であり、組み込みCPUの性能値ではない。

JITエイジングは各変種3回の中央値である。コンパイル数などの計数値は各変種の3回で一致した。統計無効の拡張ではJIT実行割合を取得できないため、0%とはせず`N/A`と表示した。

別途、`FIREBALL_BUILD_RUNTIME_PROFILE_STATS=1`でネイティブInterpreter拡張をビルドし、JITエイジング単体を実行した。既定設定のJIT実行割合は80.6%だった。コンパイル数656件、追い出し605件、ローテーション50回は統計無効の測定と一致した。統計有効時の実行時間は計測条件が異なるため、下表の総時間と混ぜない。

| 変種 | compiles | purges | rotations | 総時間 (ms) | コンパイル時間 (ms) | エイジング時間 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| ホット関数のみ | 16 | 0 | 0 | 178 | 0.5 | 0.00 |
| エイジングなし | 741 | 693 | 58 | 461 | 18.6 | 0.00 |
| U=1、O=4 | 656 | 608 | 50 | 444 | 16.4 | 0.74 |
| U=2、O=8（既定値） | 656 | 605 | 50 | 453 | 17.4 | 1.22 |
| U=8、O=32 | 257 | 210 | 20 | 394 | 6.8 | 1.79 |

既定設定のコンパイル数はエイジングなしより11.5%少なかった。総時間の差は今回8 msであり、単発の時間差を設定の性能効果として確定しない。

AO-Benchは全スイート実行時の1回に加え、`uv run --offline --no-sync python experiments/pysim/benchmarks/aobench/bench_aobench.py`を2回実行した。毎回1,600 raysの描画結果がInterpreterとHybrid JITで一致し、JITキャッシュのActiveバンクは4トレースだった。

| 試行 | Interpreter (ms) | Hybrid JIT (ms) | Hybrid JIT / Interpreter |
| :--- | ---: | ---: | ---: |
| 全スイート内 | 5,733.03 | 6,326.06 | 1.103x |
| AO単独1回目 | 5,741.89 | 6,305.45 | 1.098x |
| AO単独2回目 | 5,732.67 | 6,323.02 | 1.103x |
| 中央値 | 5,733.03 | 6,323.02 | 1.103x |

この3試行ではHybrid JITがInterpreterより約10%遅かった。9月24日の別実装・別実行では逆方向の値だったため、旧値との単純な増減評価はしない。実行統計は無効であり、JIT trace遷移数とJIT実行割合は未取得である。

### 6.13 Clang再ビルド後の個別ベンチマーク測定（2026-09-29）
<!-- traceability: {FastAddressCheck} {DirectMappedJIT16} {JIT_CopyAndPatch} {JIT_OldestOnly_Promote} {JIT_CardAgingSweep} -->

AMD Ryzen 5 5500GT、Linux 7.0.0-34-generic x86-64、CPython 3.14.6、uv 0.12.19で測定した。実行前にInterpreter拡張、JIT trace拡張、FastCache拡張をClang 21.1.8で再ビルドした。Interpreter実行統計は無効、JIT hotspot profilingは有効、FastCacheは16スロット構成である。

CPU 2へ固定し、エイジングを除く5系統とFastCache比較を独立プロセスで各3回実行した。表の値はプロセス間中央値であり、括弧内に最小値と最大値を示す。JIT算術ループは各プロセス内でも3回測定し、その中央値を使った。FastCacheは各プロセスで1,000,000 hitを5ラウンド測定した。

Clang再ビルド後に`run_all.py`も実行したが、JITカードエイジングで停止した。当時の実装では、wasmtime 49.0.0が生成したWASMの208個のexportが`FB_CONF_MAX_EXPORTS=64`を超えていた。後続のローダ設計変更で固定件数上限をなくし、アリーナ容量で受理数を制約する方式へ移行した。エイジング系はこの報告時点では未計測である。

| 系統 | 指標 | 中央値（3プロセスの範囲） |
| :--- | :--- | :--- |
| リニアメモリ | 32-bit 読み書き | 9.02 M ops/s（8.68–9.31） |
| リニアメモリ | 8-bit / 16-bit 読み書き | 32.09 / 9.24 M ops/s（28.09–33.02 / 8.64–9.25） |
| リニアメモリ | 境界検査 / vMMIO RAMバイパス | 15.82 / 3.40 M ops/s（13.80–15.91 / 3.33–3.49） |
| リニアメモリ | vMMIO RAMバイパス帯域 | 12.97 MB/s（12.72–13.30） |
| vMMIO | TLB hit / FlatMap walk | 1.05 / 0.92 M ops/s（1.00–1.07 / 0.92–0.95） |
| vMMIO | hit / walk 比 | 1.13x（1.08–1.14） |
| vMMIO | 静的デバイス dispatch / RBAC 検査 | 1.32 / 1.27 M ops/s（1.30–1.33 / 1.24–1.27） |
| JIT | Copy-and-Patchコンパイル | 53,430 traces/s、18.72 µs/trace（51,921–55,073 traces/s、18.16–19.26 µs/trace） |
| JIT | カード検査 / 疎なentry検索 | 615.9 / 508.0 ns（593.7–616.5 / 490.6–511.1） |
| JIT FastCache | C++ FastCache / Python StaticVector hit | 62.6 / 176.8 ns（62.5–63.1 / 175.5–184.0）、比0.354 |
| JIT | 算術ループ100,000反復 | Python 4,936.03 ms / C++ Interpreter 15.36 ms / Hybrid JIT 30.71 ms |
| JITキャッシュ | 小 / 中 / 大作業集合のhit rate | 100.00% / 100.00% / 92.44% |
| JITキャッシュ | 追い出し処理速度 | 165,759 evictions/s（159,696–166,402） |
| AO-Bench | Interpreter / Hybrid JIT | 6,255.22 / 6,643.39 ms（6,174.75–6,514.52 / 6,574.33–6,694.15） |

算術ループの3経路はすべて`704,982,704`を返した。Hybrid JITはPython handlerより160.73倍速く、C++ Interpreterより約2.00倍の時間を要した。InterpreterとJITのネイティブ拡張は通常設定でビルドしたため、JIT trace起動数とdispatcher遷移数は取得していない。

JIT FastCacheのC++経路とPython経路は毎回同じtrace objectを返した。C++経路のhit時間はPython経路の0.354倍だった。この合成測定は固定PCのhit処理だけを比較し、JIT全体の速度差を示す値ではない。

AO-Benchの描画結果は毎回InterpreterとHybrid JITで一致した。Hybrid JITはInterpreterより約1.06倍の時間を要した。JIT cacheのActiveバンクには各回4トレースが存在した。

この環境には`perf`、Intel VTune、AMD uProfがなく、`perf_event_paranoid`は4だった。cycles/opcodeと外部ホットスポットプロファイルは未取得である。6.12節はCPU固定なしの統合実行であり、実行条件が異なるため、今回の数値との差を性能変化として扱わない。

再ビルドと測定では`UV_CACHE_DIR=/tmp/fireball-uv-cache`、`UV_OFFLINE=true`、`UV_NO_SYNC=true`を設定した。ビルドには次のスクリプトを使った。

```bash
bash experiments/pysim/tier3_executer/interpreter/build_native.sh
bash experiments/pysim/tier3_executer/jit/build_native.sh
```

測定ログは`/tmp/pysim_benchmark_20260929_rebuilt_1.log`から`rebuilt_3.log`に保存した。統合ランナーの失敗ログは`/tmp/pysim_benchmark_20260929_rebuilt_run_all.log`にある。

### 6.14 エクスポート件数上限の除去後に行ったJITエイジング測定（2026-09-29）
<!-- traceability: {JIT_CardAgingSweep} {Runtime_BumpAllocator} -->

ローダの固定エクスポート数上限を削除し、セクション件数を先に走査して必要な `StaticVector` を親ランタイムの専用バンプアリーナから確保する方式へ変更した。PySIMの既定アリーナは32 KiBである。ベンチマークでは同じアリーナをパーサーと `RuntimeEngine` に渡した。wasmtime 49.0.0が生成する8 hot + 200 cold関数（208 export）のワークロードを使用し、CPU 2へ固定して各variantを3回実行した。以下はvariant内の中央値である。

| Variant | Compile requests | Evictions | Rotations | Time (ms) | Compile (ms) | Aging (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| hot only (ideal) | 16 | 0 | 0 | 187 | 0.6 | 0.00 |
| no aging | 741 | 693 | 58 | 489 | 21.6 | 0.00 |
| aging U=1 O=4 | 656 | 608 | 50 | 460 | 18.0 | 0.81 |
| aging U=2 O=8 (config default) | 656 | 605 | 50 | 462 | 19.4 | 1.37 |
| aging U=8 O=32 | 257 | 210 | 20 | 405 | 8.3 | 1.83 |

設定既定値では `no aging` と比べてcompile requestsが11.5%減り、実行時間は5.5%短縮した。エイジング処理は1stepあたり27.3 µsで、このvariantの実行時間の0.30%を占めた。全variantの結果checksumは一致し、ベンチマーク内の決定性条件を満たした。JIT実行割合はネイティブ実行統計が無効なため取得していない。

## 6.15 C++ Interpreter の 0xFC 命令測定（2026-09-30）
<!-- traceability: {WasmInstructionSet} {DirectBytecodeExecution} -->

Clang 21.1.8でInterpreter拡張をビルドし、CPython 3.14.6、uv 0.12.19、Linux 7.0.0-34-generic、AMD Ryzen 5 5500GT上で測定した。計測は3ラウンドを実行し、1命令または1転送あたりの中央値を記録した。CPU固定、ハードウェアカウンタ測定はしていない。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/tier3_executer/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  uv run --offline --no-sync python experiments/pysim/benchmarks/interpreter/bench_fc.py
```

| 経路 | ワークロード | C++拡張あり | Python参照 | 相対速度 |
| :--- | :--- | ---: | ---: | ---: |
| 飽和数値変換 `0xFC 0x00..0x07` | 各2,048命令 | 263〜284 ns/命令 | 41.5〜44.5 µs/命令 | 152〜165倍 |
| リニア `memory.copy` | 16 B、8,192回 | 250 ns/回 | 45.8 µs/回 | 183倍 |
| リニア `memory.copy` | 256 B、2,048回 | 286 ns/回 | 56.3 µs/回 | 197倍 |
| リニア `memory.copy` | 4,096 B、128回 | 862 ns/回 | 237.8 µs/回 | 276倍 |
| リニア `memory.fill` | 16 B、8,192回 | 252 ns/回 | 43.6 µs/回 | 173倍 |
| リニア `memory.fill` | 256 B、2,048回 | 278 ns/回 | 50.7 µs/回 | 182倍 |
| リニア `memory.fill` | 4,096 B、128回 | 861 ns/回 | 157.3 µs/回 | 183倍 |

飽和変換の意味論38ケース（NaN、無限大、有限境界、符号付き・符号なし）と、リニア `memory.copy` / `memory.fill` のPython・C++両経路を確認した。C++計測は公開 `Interpreter.call()` からネイティブ命令ハンドラへ入る経路である。

vDMAについては、合成コピーを使わず、各試行で生成した `System` の実 vMMIO PTEと `System.vdma_transfer()` を通した。各ルートは64 Bを512回転送し、転送回数、アドレス、転送後のバイト列を検証した。非リニアアドレスではC++命令ハンドラからPython実行境界へ戻るため、下表のC++拡張ありの値はネイティブInterpreterからPysimのPython vDMAサービスへ渡る境界を含む。これはvDMAハードウェア単体の性能値ではない。

| ルート（source FC → destination FC） | C++拡張あり | Python参照 | 相対速度 |
| :--- | ---: | ---: | ---: |
| リニア → DYNAMIC（0 → 13） | 14.67 µs/転送 | 54.87 µs/転送 | 3.74倍 |
| SHM → リニア（14 → 0） | 14.79 µs/転送 | 55.12 µs/転送 | 3.73倍 |
| DYNAMIC → PASSTHROUGH（13 → 15） | 17.05 µs/転送 | 57.42 µs/転送 | 3.37倍 |
| PASSTHROUGH → SHM（15 → 14） | 19.24 µs/転送 | 60.69 µs/転送 | 3.15倍 |

この計測はPysimの命令実装、vMMIOの許可・物理アドレス解決、およびSystemの転送サービスを検証する。DYNAMIC PTEが参照する `System.phys_mem` と `HalBufferPool` のスロットストレージが同一のデータ領域を共有することまでは確認していない。したがって、HALバッファの実データ連携と実機vDMA帯域は別の検証項目として残る。C++拡張ありの数値も、組み込みCPUや製品ランタイムの性能値とは扱わない。

再現結果の全数値と実行環境は [`bench_fc_20260930.json`](results/bench_fc_20260930.json) に保存した。

## 6.16 C++ Interpreter・JIT 統合スイート再測定（2026-09-30）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} {RuntimeHotspotProfiler} -->

Interpreter、JIT、FastCacheをClang 21.1.8で再ビルドした後、CPU 2へ固定して `run_all.py` を独立プロセスで3回実行した。環境はAMD Ryzen 5 5500GT、Linux 7.0.0-34-generic、CPython 3.14.6、uv 0.12.19である。各試行は6系統すべて完走し、各ログに `[PASS]` が記録された。VTune、AMD uProf、Linux `perf` が利用できないため、時間計測のみを行いハードウェアイベントは取得していない。

```bash
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/tier3_executer/interpreter/build_native.sh
UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
  bash experiments/pysim/tier3_executer/jit/build_native.sh
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --offline --no-sync python experiments/pysim/benchmarks/run_all.py \
    > experiments/pysim/benchmarks/results/run_all_20260930_${trial}.log
done
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --offline --no-sync python experiments/pysim/benchmarks/interpreter/bench_fc.py \
    > experiments/pysim/benchmarks/results/bench_fc_20260930_run${trial}.json
done
```

| 指標 | 3試行の中央値 | 最小〜最大 |
| :--- | ---: | ---: |
| 100,000回算術ループ: Python handler | 4,761.10 ms | 4,742.55〜4,784.37 ms |
| 100,000回算術ループ: C++ Interpreter | 20.82 ms | 20.66〜21.22 ms |
| 100,000回算術ループ: Hybrid JIT | 37.65 ms | 37.14〜38.15 ms |
| Copy-and-Patch compile throughput | 55,337 traces/s | 46,366〜58,290 traces/s |
| JIT aging既定値の総時間 | 488 ms | 474〜510 ms |
| AO-Bench: Interpreter | 6,158.89 ms | 6,144.68〜6,256.92 ms |
| AO-Bench: Hybrid JIT | 6,884.01 ms | 6,765.63〜7,129.61 ms |
| 全スイート所要時間 | 42.27 s | 42.19〜43.35 s |

算術ループの返却値は3経路すべて `704,982,704` で一致し、各試行のRuntime統計も200,001 trace invocations、193,751 dispatcher trace transitions、3 interpreter stepsで一致した。Python handlerに対してC++ Interpreterは約228.7倍、Hybrid JITは約126.4倍速かった。一方、このワークロードではHybrid JITはC++ Interpreterより約1.81倍遅く、AO-BenchでもInterpreterより約1.12倍遅かった。小さいトレースでのdispatcher・実行境界コストが原因候補だが、プロファイルを取得できていないため原因は未確定であり、この結果だけでJIT全般の優劣は判断しない。

0xFC専用ベンチマークも同じCPU固定条件で3プロセス実行した。各プロセス内では3ラウンドの中央値を採り、以下はそのプロセス間中央値である。

| 経路 | C++拡張あり | Python参照 | 相対速度 |
| :--- | ---: | ---: | ---: |
| 飽和数値変換8種 | 267〜277 ns/命令 | 42.0〜45.0 µs/命令 | 153〜165倍 |
| `memory.copy` 16 / 256 / 4,096 B | 252 / 283 / 897 ns | 45.8 / 56.3 / 239.9 µs | 182 / 199 / 268倍 |
| `memory.fill` 16 / 256 / 4,096 B | 250 / 281 / 973 ns | 44.5 / 49.9 / 156.3 µs | 178 / 178 / 161倍 |
| vDMA 64 B、4ルート | 14.4〜20.2 µs/転送 | 55.6〜61.2 µs/転送 | 3.0〜3.9倍 |

vDMAは各ルート512転送/試行を記録した。各試行で変換38ケース、リニアcopy/fill 4ケース、vDMAルート4種の意味論チェックを通過した。非リニア転送の計測値はPysimのPython vDMAサービスを含み、ハードウェア転送時間を表さない。DYNAMIC PTEとHALスロットストレージの共有関係に関する6.15節の制約も引き続き残る。

全6系統の生ログは [`run_all_20260930_1.log`](results/run_all_20260930_1.log)、[`run_all_20260930_2.log`](results/run_all_20260930_2.log)、[`run_all_20260930_3.log`](results/run_all_20260930_3.log) に、0xFC各試行のJSONは [`bench_fc_20260930_run1.json`](results/bench_fc_20260930_run1.json)、[`bench_fc_20260930_run2.json`](results/bench_fc_20260930_run2.json)、[`bench_fc_20260930_run3.json`](results/bench_fc_20260930_run3.json) に保存した。

6.16節のAO-Benchで「Interpreter」とした値は、当時の`Interpreter.call()`がネストしたWASM関数呼出しを検出するとC++ dispatchを使わず、Python `_step()`へ進んでいた経路の測定だった。この値をC++ Interpreterの性能証跡として扱わず、以下でPython参照、C++ Interpreter、Hybrid JITを分けて再測定した。

## 6.17 AO-BenchのPython・C++ Interpreter・JIT比較（2026-09-30）
<!-- traceability: {ThreadedInterpreter} {JIT_CopyAndPatch} -->

AO-Benchを32×16、4 AO samples/hitで実行し、1600 ray/frameの同じWASM入力をPython参照インタープリタ、C++ Interpreter、Hybrid JITで処理した。Python参照とC++ Interpreterは別々の`System`・WASIメモリで実行した。C++ Interpreterは`NativeInterpreter`を明示して選択し、C++命令ディスパッチ未対応opcodeをPython命令ハンドラへ渡さず失敗させる。WASM `call` とhost importはランタイム境界で解決する。AO-Benchで使う`i32.store`と`i32.store8`はC++ハンドラで実行する。3経路の出力は各測定で完全一致した。別の検証実行でも528バイトをassertした。

各測定はCPU 2へ固定した独立プロセスで3回実行した。表は試行間中央値と最小〜最大である。VTune、AMD uProf、Linux `perf`が利用できなかったため、ハードウェアカウンタは取得していない。

```bash
for trial in 1 2 3; do
  taskset -c 2 env UV_CACHE_DIR=/tmp/fireball-uv-cache UV_OFFLINE=true UV_NO_SYNC=true \
    uv run --offline --no-sync python -c 'import json,sys; sys.path.insert(0,"experiments/pysim/benchmarks/aobench"); from bench_aobench import run_aobench; print(json.dumps(run_aobench(),sort_keys=True))' \
    > "experiments/pysim/benchmarks/results/aobench_native_cpp_20260930_run${trial}.json"
done
```

| 経路 | 中央値 (ms/frame) | 最小〜最大 (ms/frame) | 中央値 (rays/s) |
| :--- | ---: | ---: | ---: |
| Python参照Interpreter | 6,045.15 | 6,017.58〜6,101.75 | 265 |
| C++ Interpreter | 2,807.37 | 2,764.88〜2,829.45 | 570 |
| Hybrid JIT | 6,643.55 | 6,595.24〜6,648.53 | 241 |

C++ InterpreterはPython参照の約2.15倍速かった。Hybrid JITはC++ Interpreterの約0.42倍の速度（約2.37倍遅い）で、Python参照に対しても約0.91倍だった。JITは各試行で5 traceを生成し、診断実行のinterpreter blocksは82,960、JIT invocationsは82,015、native dispatcher trace transitionsは44,734、trace exits to interpreterは93,043で一致した。この測定ではJITが遅い事実を記録する。プロファイルなしでは遅延原因は確定しない。

3回の性能結果は [`aobench_native_cpp_20260930_run1.json`](results/aobench_native_cpp_20260930_run1.json)、[`aobench_native_cpp_20260930_run2.json`](results/aobench_native_cpp_20260930_run2.json)、[`aobench_native_cpp_20260930_run3.json`](results/aobench_native_cpp_20260930_run3.json) に、出力長528バイトを含む検証結果は [`aobench_native_cpp_20260930_validation.json`](results/aobench_native_cpp_20260930_validation.json) に保存した。
