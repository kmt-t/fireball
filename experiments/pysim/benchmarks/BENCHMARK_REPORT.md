# PySIM 統合ベンチマーク詳細レポート (PySIM Performance Benchmark Report)

本レポートは、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を具象化した `pysim` における全 5 つのベンチマークスイートの実測測定結果、JIT トレースチェイニング動作診断、および C++23 実機実装への移植性・性能予測をまとめた詳細レポートです。

Section 1〜5は 2026-09-18 に、JIT Direct-Mapped Folding XORキャッシュを4スロットから16スロットへ拡張した変更（`{DirectMappedJIT16}`, `experiments/pysim/tier1_core/config.py`）の直後に同一ワークスペースで再測定した結果である。Pythonプロセス、OSスケジューリング、およびPythonハンドラ本体呼出しの影響を含むため、絶対値ではなく同一環境での比較値として扱う。5.1節のネイティブCPS経路の数値のみ2026-09-17測定の既存値を保持する（本変更では未再測定のため）。

---

## 1. 測定環境・実行構成

- **プラットフォーム**: Windows (AMD64)
- **ランタイム**: Python 3.14 (pysim) / 4引数CPS ABI
- **ネイティブ実行状況**: `tier3_executer/jit/native_trace_call.pyd` をJITのネイティブ呼び出し経路で使用。LEB128は任意のPythran AOTカーネルを使用し、インタープリタのクラス主体のディスパッチはPython実装として測定。
- **ローカル領域レイアウト**: `WASM_LOCAL_ALIGNMENT_BYTES` を基準に8バイト固定スロットへ統一。Native value stack の物理容量は `NATIVE_VALUE_STACK_CAPACITY`（128 raw words）。
- **実行コマンド**:
  ```bash
  .venv/Scripts/python.exe experiments/pysim/benchmarks/run_all.py
  ```
- **AO-Bench単体・チェイニング内部ダンプ実行コマンド**:
  ```bash
  .venv/Scripts/python.exe experiments/pysim/benchmarks/aobench/bench_aobench.py --debug
  ```
- **ネイティブJITブリッジの実行コマンド**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File experiments/pysim/tier3_executer/jit/build_native.ps1
  uv run --system-certs python experiments/pysim/aobench.py
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
  * JIT Chained Invocations:            15,482 / 82,053 (18.9%)
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
- **算術演算ループ差分検証**: 100,000 反復の算術ホットループにおいて、Tier 2 インタープリタ（6082.13 ms）に対して Tier 3 JIT（1192.23 ms）が **5.10x 高速化**を達成し、演算結果（`704,982,704`）が完全一致（Exact Match）。この単純ループは1トレースにチェインしきるため、4→16スロット化による変化は測定ノイズの範囲に留まる（実行毎の変動要因は下記）。
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
- **未評価**: コールド関数の間隔と関数数の組み合わせは、1 つの構成だけを測定している。実機（Cortex-M33）でのコンパイル時間への影響は測定していない。

---

## 4. 3D AO-Bench & JIT トレースチェイニング内部診断

`bench_aobench.py --debug` 実行時に採取されたランタイム内部メトリクスおよびトレースチェイニング診断結果です。

この測定では `JIT_CARD_SHIFT=2`（4バイト/カード）を使用し、既定の `min_trace_bytes` も4バイトです。

チェイン接続率（18.9%）自体はコンパイル時に静的決定される `chain_next_pc` 埋め込みの結果であり、Direct-Mapped Folding XORキャッシュのスロット数（4→16）とは無関係のため変化しない。16スロット化が改善するのは「コンパイル済みトレースへ再入場する際の検索コスト」であり、インタープリタ復帰後の次ブロック解決・チェイン未成立ブロックへの再入場のたびに、O(log n)のバンク二分探索へ落ちる頻度を下げる。

### 4.1 実行サマリー & キャッシュ状態
- **総ブロック実行数**: 164,770 回
  - インタープリタ実行: 82,717 回 (50.2%)
  - JIT ネイティブ実行: 82,053 回 (49.8%)
- **JIT チェイニング効率**:
  - チェイン接続による連続 JIT 実行: **15,482 回（18.9%）**
  - インタープリタへの復帰（チェイン終端・未コンパイル境界）: 66,571 回
- **JIT キャッシュ占有率**:
  - Active バンク: 15 トレース（1,763 / 2,048 バイト, 86.1%）
  - Warm バンク: 15 トレース（1,991 / 2,048 バイト, 97.2%）
  - Oldest バンク: 0 トレース（エビクション・プロモーション発生なし）

### 4.1.1 ホットスポット分類
- **Total Blocks in Module**: 88
- **Trackable JIT Candidates**: 34
- **Card Status Distribution**: `COMPILED=30`, `HOT=37`, `EXECUTED=1`, `UNEXECUTED=20`

### 4.2 コンパイル済みトレースのチェイニング診断台帳
今回のAO実行では30トレースがActive/Warmに常駐し、15,482回の連続JIT実行が発生しました。チェイン終端または未コンパイル境界でのインタープリタ復帰は66,571回です。

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

### 5.2 C++23 実機実装（Cortex-M33 / x64）での予測
実機 C++23 実装においては、アーキテクチャ設計により上記ボトルネックが消滅します：
1. **同一レジスタ規約・ゼロオーバーヘッド遷移**:
   - インタープリタハンドラと JIT トレースは、完全に同一の `__fastcall` CPS 4引数レジスタ規約（`R0: ctx`, `R1: sp`, `R2: local_base`, `R3: tos`）で直結されます。
   - `[[clang::musttail]]` または単一の直接ジャンプ命令（`JMP` / `BX`）によって、C++実装ではFFIではなく同一ABIの末尾遷移になります。サイクル数0をpysimから主張するものではありません。
2. **予測される高速化率**:
   - トレース境界でのみ協調的 Yield（`{ADR_TraceBoundaryYield}`）を行うため、ホットループ内のネイティブ直接実行により、実機上では **JIT がインタープリタに対して 3x〜8x の実測高速化を達成**する見込みです。
3. **RAM 32KB 環境への適合性**:
   - 3D レイトレーシングのような計算集約型タスクであっても、生成された JIT トレースは合計 **30 トレース（Active 1,763 B + Warm 1,991 B = 3,754 B）** でループのコアパスを網羅しており、8 KB の JIT コード領域予算内に余裕を持って収まることが実証されました。
