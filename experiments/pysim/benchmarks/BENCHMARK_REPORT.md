# PySIM 統合ベンチマーク詳細レポート (PySIM Performance Benchmark Report)

本レポートは、Fireball Hypervisor の全層（Tier 1 Core OS / Tier 2 Runtime / Tier 3 JIT & Platform）を具象化した `pysim` における全 5 つのベンチマークスイートの実測測定結果、JIT トレースチェイニング動作診断、および C++23 実機実装への移植性・性能予測をまとめた詳細レポートです。

以下の最新値は 2026-09-13 に同一ワークスペースで再実行した結果です。Pythonプロセス、OSスケジューリング、`ctypes`境界の影響を含むため、絶対値ではなく同一環境での比較値として扱います。

---

## 1. 測定環境・実行構成

- **プラットフォーム**: Windows (AMD64)
- **ランタイム**: Python 3.14 (pysim) / ctypes CPS 4-argument C-Calling Convention
- **Cython実行状況**: `tier3_jit/native_trace_call.pyd` をJITのネイティブ呼び出し経路で使用。`tier2_runtime/interpreter.pyd` は今回の実行環境に存在しないため、インタープリタ本体はPython実装で測定。
- **ローカル領域レイアウト**: `WASM_LOCAL_ALIGNMENT_BYTES` を基準に8バイト固定スロットへ統一。Native value stack の物理容量は `NATIVE_VALUE_STACK_CAPACITY`（128 raw words）。
- **実行コマンド**:
  ```bash
  .venv/Scripts/python.exe experiments/pysim/benchmarks/run_all.py
  ```
- **AO-Bench 単体・チェイニング内部ダンプ実行コマンド**:
  ```bash
  .venv/Scripts/python.exe experiments/pysim/benchmarks/aobench/bench_aobench.py --debug
  ```

---

## 2. 全ベンチマーク実測結果サマリー

```
================================================================================
                     ALL BENCHMARK RESULTS SUMMARY                       
================================================================================

[Section 1: Linear Memory & Guest RAM Access]
--------------------------------------------------------------------------------
  * Raw Bytearray 32-bit R/W (Baseline): 3.40 M ops/s  (294.4 ns/op)
  * 8-bit Byte R/W Throughput:          11.98 M ops/s
  * 16-bit Half-Word R/W Throughput:     3.71 M ops/s
  * Single-CMP Bound Check Overhead:    5.78 M ops/s  (173.0 ns/op)
  * vMMIO Fast Bypass (Bit 31 == 0):    1.49 M ops/s  (669.1 ns/op)
  * Linear RAM Bandwidth:               5.70 MB/s

[Section 2: vMMIO Virtual Devices & Address Translation]
--------------------------------------------------------------------------------
  * Direct-Mapped TLB Hit (O(1)):       0.52 M ops/s  (1930.7 ns/hit)
  * Folding XOR Hash Calculation:       5.52 M ops/s  (181.1 ns/op)
  * TLB Miss -> FlatMap Walk (O(logN)): 0.42 M ops/s  (2409.2 ns/walk)
  * TLB Hit / FlatMap Walk Ratio:        1.25x
  * Static Syscall Dispatch (FC=0xC):   0.47 M ops/s  (2121.0 ns/dispatch)
  * RBAC Task Isolation Verification:   0.56 M ops/s  (1774.7 ns/check)

[Section 3: JIT Compiler & Runtime Dispatch]
--------------------------------------------------------------------------------
  * Copy-and-Patch Compile Speed:       14,600 Traces/sec  (68.50 us/trace)
  * Compile Cost per WASM Instruction:  17123.9 ns/opcode
  * 2-Bit Card Marking O(1) Check:      1.35 M ops/s  (739.0 ns/check)
  * bswap32 Radix Tree Section Search:  0.39 M ops/s  (2553.1 ns/lookup)
  * Arithmetic Loop (100,000 iters):    Interp: 7370.91 ms | JIT: 1038.69 ms
  * Differential Result Check:          Interp=704,982,704 | JIT=704,982,704 (MATCH)
  * Measured JIT Speedup:               7.10x faster
  * PIC Context Helper Tail Jump:       0.11 M ops/s  (8743.5 ns/dispatch; 10,000 calls)

[Section 4: JIT Cache Metabolism & Corner Cases]
--------------------------------------------------------------------------------
  * Oldest-Only Promotion Invariant:    [PASS] (Warm hits=0 promos, Oldest hit=+1 promo)
  * Small Working Set (N=8 <= Active):  Hit Rate = 100.00%  (1.49 M lookups/s)
  * Medium Working Set (N=24 <= 3Bank): Hit Rate = 100.00%  (1.12 M lookups/s)
  * Large Working Set (N=100 Thrash):   Hit Rate = 92.44%  (0.31 M lookups/s)
  * Cache Metabolism & Churn Rate:      135,982 Evictions / Sec (4976 generations)
  * Dangling Chain Unlinking Safety:    [PASS] (All evicted traces unlinked cleanly)
  * Multi-Module UnifiedPC Collision:   [PASS] (Immunity verified between func_0 and func_1)

[Section 5: 3D Raytracing Ambient Occlusion (AO-Bench)]
--------------------------------------------------------------------------------
  * Resolution & Sampling:              32 x 16 (1,600 rays / frame)
  * Tier 2 (Threaded CPS):              6943.03 ms  (230 Rays / Sec)
  * Tier 3 (Hybrid + JIT):              6256.85 ms  (256 Rays / Sec)
  * Measured Speedup:                   1.11x faster
  * JIT Chained Invocations:            33,106 / 84,835 (39.0%)
  * Active JIT Cache Bank Traces:       9 compiled traces
================================================================================
```

---

## 3. ベンチマーク別詳細評価

### 3.1 Linear Memory & Guest RAM Access
- **境界チェック最適化**: 符号なし単一比較（`unsigned(addr + size) <= ram_size`）による境界判定は 5.78 M ops/s（173.0 ns/op）を記録。
- **vMMIO Fast Bypass**: アドレス最上位ビット判定（`addr & 0x8000_0000 == 0`）により、仮想デバイスを介さない通常のリニア RAM アクセスを $O(1)$ で直接バイパスし、1.49 M ops/s を記録。

### 3.2 vMMIO Virtual Devices & Address Translation
- **Direct-Mapped Folding XOR TLB**: Folding XOR ハッシュ計算は 5.52 M ops/s（181.1 ns）。ウォームアップ後のTLBヒットは 0.52 M ops/s（1930.7 ns）、33ページ作業集合によるFlatMap経路は 0.42 M ops/s（2409.2 ns）で、同一実行内の比率は 1.25x となった。ヒット計測区間は `tlb_hits == iterations` かつ `tlb_misses == 0` をassertしている。
- **RBAC & ゼロコピー所有権分離**: タスク間共有メモリ（FC=14）およびシステムコールディスパッチにおける RBAC 検証オーバーヘッドは 0.56 M ops/s（1774.7 ns/check）。`VmmioStatus.OWNER_MISMATCH` を全150,000回検出するassertを通過し、メモリ安全性を確認。

#### 3.2.1 TLB比較測定の妥当性注記
ベンチマークのPTE格納表を64件、TLBを32件として分離した。初期化は静的1ページ・SHM33ページ・passthrough16ページの計50件を登録し、全登録処理で容量超過を `assert` する。`TLB Miss -> FlatMap Walk` は32エントリを超える33ページの有効な作業集合を循環させる測定であり、実測カウンタは `tlb_hits=590,877`、`tlb_misses=9,123`、PTE登録件数は50件だった。これは33ページ中のハッシュ衝突を含むリフィル挙動の測定で、毎回のアクセスを強制ミスさせる値ではない。

### 3.3 JIT Compiler & Runtime Dispatch
- **Copy-and-Patch 高速コンパイル**: ネイティブステンシルのメモリコピーと固定パッチ位置への書き込みを 14,600 Traces/sec（68.50 us/trace）で実行。4命令ブロック換算で 17,123.9 ns/opcode であり、コードバッファ確保も含む。
- **実行時パイプライン**: カード判定は712.3ns、Radix検索は2316.4ns。トレース本体ではなく、JIT候補判定・エントリ検索のPython実装コストである。
- **算術演算ループ差分検証**: 100,000 反復の算術ホットループにおいて、Tier 2 インタープリタ（7370.91 ms）に対して Tier 3 JIT（1038.69 ms）が **7.10x 高速化**を達成し、演算結果（`704,982,704`）が完全一致（Exact Match）。
- **Cヘルパー境界**: 命令別 `jit_helper_ptrs[]` をコンテキスト内の固定スロットから直接読み、JITフレーム復元後に末尾ジャンプする経路は 8743.5ns/dispatch。pysimの `ctypes` コールバックを含むABI回帰値であり、組込みCの性能値ではない。

同一ターゲットを統合ベンチマークで測定した結果は、インタープリタ `7370.91 ms`、JIT `1038.69 ms`、速度比 `7.10x` となった。実行ごとにOSスケジューリング等で時間が変動するため、単発値を絶対性能とは扱わず、同一実行条件内の比較値として扱う。

### 3.4 JIT Cache Metabolism & 3面ローテーション
- **3面リングバッファ代謝 (Active / Warm / Oldest)**:
  - Small Working Set（$N=8 \le$ Active 容量）: ヒット率 **100.00%**（1.49 M lookups/s）。
  - Medium Working Set（$N=24 \le$ 3Bank 合計容量）: ヒット率 **100.00%**（1.12 M lookups/s）。
  - Large Working Set（$N=100$ スラッシング負荷）: ヒット率 **92.44%**（0.29 M lookups/s）を維持。
- **不変条件検証**:
  - `Oldest-Only Promotion Invariant`: Warm ヒットではプロモーション（Active へのコピー）を発生させず、Oldest ヒット時のみ昇格させることで無駄なキャッシュコピーを完全抑止。
  - `Dangling Chain Unlinking Safety`: バンク破棄時に破棄対象トレースを指す先行トレースの `chain_next` を $O(k)$ 有界時間でアンリンクし、ダングリングポインタを完全防止。

---

## 4. 3D AO-Bench & JIT トレースチェイニング内部診断

`bench_aobench.py --debug` 実行時に採取されたランタイム内部メトリクスおよびトレースチェイニング診断結果です。

この測定では `JIT_CARD_SHIFT=2`（4バイト/カード）を使用し、既定の `min_trace_bytes` も4バイトです。前回の8バイト設定では、チェインに必要な短いブロックが候補から除外され、連続JIT実行が0回になっていました。

### 4.1 実行サマリー & キャッシュ状態
- **総ブロック実行数**: 164,770 回
  - インタープリタ実行: 79,935 回 (48.5%)
  - JIT ネイティブ実行: 84,835 回 (51.5%)
- **JIT チェイニング効率**:
  - チェイン接続による連続 JIT 実行: **33,106 回（39.0%）**
  - インタープリタへの復帰（チェイン終端・未コンパイル境界）: 51,729 回
- **JIT キャッシュ占有率**:
  - Active バンク: 9 トレース（657 / 2,048 バイト, 32.1%）
  - Warm バンク: 22 トレース（1,992 / 2,048 バイト, 97.3%）
  - Oldest バンク: 0 トレース（エビクション・プロモーション発生なし）

### 4.1.1 ホットスポット分類
- **Total Blocks in Module**: 88
- **Trackable JIT Candidates**: 35
- **Card Status Distribution**: `COMPILED=67`, `HOT=0`, `EXECUTED=1`, `UNEXECUTED=20`

### 4.2 コンパイル済みトレースのチェイニング診断台帳
今回のAO実行では31トレースがActive/Warmに常駐し、33,106回の連続JIT実行が発生しました。チェイン終端または未コンパイル境界でのインタープリタ復帰は51,729回です。

| バンク | Head PC | Next PC | LoopsTo | ChainNext | 実行回数 | チェイニング診断結果 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Warm | `0x10000` | `0x10009` | None | None | 35,949 | `[UNLINKED]` Target `0x10009` uncompilable |
| Warm | `0x20009` | `0x20012` | None | None | 2,782 | `[UNLINKED]` Target `0x20012` uncompilable |
| Warm | `0x30000` | `0x3000B` | `0x30007` | `0x3000B` | 910 | `[CHAINED]` |
| Warm | `0x3000B` | `0x30020` | None | `0x30022` | 903 | `[CHAINED]` |
| Warm | `0x30022` | `0x3002A` | `0x30035` | `0x3002A` | 7,682 | `[CHAINED]` |
| Warm | `0x3002A` | `0x30022` | None | `0x30022` | 6,778 | `[CHAINED]` |
| Warm | `0x3003E` | `0x3005D` | `0x30048` | `0x3005D` | 7,690 | `[CHAINED]` |
| Warm | `0x30048` | `0x30065` | None | `0x30065` | 3,657 | `[CHAINED]` |
| Warm | `0x3005D` | `0x30065` | None | `0x30065` | 4,033 | `[CHAINED]` |
| Warm | `0x30065` | `0x30039` | None | None | 7,691 | `[UNLINKED]` Target `0x30039` uncompilable |
| Warm | `0x50030` | `0x50035` | None | None | 112 | `[UNLINKED]` Target `0x50035` uncompilable |
| Active | `0x5005C` | `0x50061` | None | None | 30 | `[UNLINKED]` Target `0x50061` uncompilable |
| Active | `0x50088` | `0x5008D` | None | `0x5008D` | 29 | `[CHAINED]` |
| Warm | `0x5008D` | `0x500B3` | `0x50094` | `0x500B3` | 1,598 | `[CHAINED]` |
| Active | `0x500AD` | `0x500B3` | None | `0x500B3` | 205 | `[CHAINED]` |
| Warm | `0x500B3` | `0x500C2` | `0x500BE` | None | 1,598 | `[UNLINKED]` Target `0x500C2` uncompilable |
| Warm | `0x6000D` | `0x60014` | `0x601B5` | `0x60014` | 15 | `[CHAINED]` |
| Warm | `0x60014` | `0x6001A` | None | `0x6001C` | 14 | `[CHAINED]` |
| Warm | `0x6001C` | `0x60023` | `0x6019C` | None | 526 | `[UNLINKED]` Target `0x60023` uncompilable |
| Warm | `0x600DC` | `0x600E4` | None | None | 247 | `[UNLINKED]` Target `0x600E4` uncompilable |
| Warm | `0x600FF` | `0x60107` | None | None | 232 | `[UNLINKED]` Target `0x60107` uncompilable |
| Warm | `0x60122` | `0x6012A` | None | None | 234 | `[UNLINKED]` Target `0x6012A` uncompilable |
| Warm | `0x60145` | `0x6014D` | None | `0x6014D` | 255 | `[CHAINED]` |
| Warm | `0x6014D` | `0x6015E` | `0x60159` | `0x6015E` | 270 | `[CHAINED]` |
| Active | `0x6015E` | `0x6016A` | `0x60165` | `0x6016A` | 270 | `[CHAINED]` |
| Active | `0x60165` | `0x6016A` | None | `0x6016A` | 7 | `[CHAINED]` |
| Active | `0x6016A` | `0x60176` | `0x60171` | `0x60176` | 270 | `[CHAINED]` |
| Active | `0x60171` | `0x60176` | None | `0x60176` | 8 | `[CHAINED]` |
| Active | `0x60176` | `0x60182` | `0x6017D` | None | 270 | `[UNLINKED]` Target `0x60182` uncompilable |
| Active | `0x6017D` | `0x60182` | None | None | 60 | `[UNLINKED]` Target `0x60182` uncompilable |
| Warm | `0x6018A` | `0x6001C` | None | `0x6001C` | 510 | `[CHAINED]` |

---

## 5. シミュレータ性能特性と C++23 実機実装への予測

### 5.1 Python シミュレータ上での特性分析
AO-Bench の今回の統合実測結果は `1.11x`（インタープリタ 6943.03 ms に対し JIT 6256.85 ms）でした。4バイトカード（`card_shift=2`）へ統一したことで、連続JIT実行は33,106回（39.0%）となり、チェイン終端・未コンパイル境界からの復帰は51,729回でした。
1. **Python $\leftrightarrow$ Ctypes FFI 境界オーバーヘッド**:
   - AO-Bench では 84,835 回のJIT呼び出しのうち33,106回がチェイン接続されました。残る境界復帰ではPython側ランタイムのフレーム同期・探索コストが発生します。
2. **オンデマンド・コンパイルと動的解決**:
   - ホットスポット到達時の Copy-and-Patch コンパイル処理が同一スレッド内で逐次実行されるため、フレーム所要時間に含まれます。

### 5.2 C++23 実機実装（Cortex-M33 / x64）での予測
実機 C++23 実装においては、アーキテクチャ設計により上記ボトルネックが消滅します：
1. **同一レジスタ規約・ゼロオーバーヘッド遷移**:
   - インタープリタハンドラと JIT トレースは、完全に同一の `__fastcall` CPS 4引数レジスタ規約（`R0: ctx`, `R1: sp`, `R2: local_base`, `R3: tos`）で直結されます。
   - `[[clang::musttail]]` または単一の直接ジャンプ命令（`JMP` / `BX`）によって、C++実装ではFFIではなく同一ABIの末尾遷移になります。サイクル数0をpysimから主張するものではありません。
2. **予測される高速化率**:
   - トレース境界でのみ協調的 Yield（`{ADR_TraceBoundaryYield}`）を行うため、ホットループ内のネイティブ直接実行により、実機上では **JIT がインタープリタに対して 3x〜8x の実測高速化を達成**する見込みです。
3. **RAM 32KB 環境への適合性**:
   - 3D レイトレーシングのような計算集約型タスクであっても、生成された JIT トレースは合計わずか **31 トレース（約 2.65 KB）** でループのコアパスを網羅しており、目標とする 6.63 KB の JIT キャッシュ予算内に余裕を持って収まることが実証されました。
