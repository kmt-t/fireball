# JIT コンパイラ & ランタイム ベンチマーク仕様書 (JIT Runtime Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {LowLatencyJIT} {META_AccessDictionary} {META_BinarySearch} {ThreadedInterpreter} -->

正本: `docs/components/tier3_jit/jit_compiler.md`, `docs/components/tier3_jit/jit_runtime.md`
参考実装: `experiments/pysim/benchmarks/jit/bench_jit.py`

Copy-and-Patch 方式による JIT コンパイル速度（トレース結合＋リロケーションパッチ）、2-bit カードマーキング表（`bit_view<2>`）による $O(1)$ ホットスポット事前判定、`bswap32` RadixBinaryTreeView による有界二分探索ルックアップ時間、およびインタープリタ対 JIT ネイティブ実行のスループット比を計測する。 `{JIT_CopyAndPatch}` `{JIT_ZeroCompileCostTheorem}` `{LowLatencyJIT}` `{META_AccessDictionary}` `{META_BinarySearch}` `{ThreadedInterpreter}`

## 2. ベンチマーク測定項目一覧

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCH-JIT-01** | Copy-and-Patch コンパイル速度 | 基本ブロック (BasicBlock) 4命令 | Traces/sec, µs/trace | 高速なステンシル結合（ゼロ最適化コスト） | `{JIT_CopyAndPatch}`, `{JIT_ZeroCompileCostTheorem}` |
| **BENCH-JIT-02** | 1命令あたりコンパイル時間 | 各 WASM オプコードのパッチ時間 | ns/opcode | 線形スケール（$O(N)$）でパッチ完了 | `{LowLatencyJIT}` |
| **BENCH-JIT-03** | 2-Bit カードマーキング状態判定 ($O(1)$) | `HotspotBitmap` / `bit_view<2>` | ns/check, M ops/sec | インタープリタ実行ループを阻害しない極低コスト | `jit_runtime.md` §3.1 |
| **BENCH-JIT-04** | `bswap32` RadixBinaryTreeView 区間検索 | 64エントリの JIT エントリインデックス | ns/lookup, M ops/sec | 下位ビット均等分散による有界二分探索 | `{META_BinarySearch}` |
| **BENCH-JIT-05** | ループ演算スループット比 (Interp vs JIT) | 100,000回算術ループ実行 | 実行時間 (ms), Speedup比 | 差分結果が完全一致し、ネイティブ実行が成立すること | `jit_compiler.md` §1 |

## 3. 測定手順

1. **コンパイル速度測定**:
   - `TraceCompiler.compile_trace()` に対し、算術基本ブロックを $N=10,000$ 回コンパイルし、1トレースあたりの平均所要時間を算出。
2. **カードマーキング & Radix テーブル検索測定**:
   - `HotspotBitmap.get_state()` および `RadixBinaryTreeView.find()` の単体スループットを $N=100,000$ 回計測。
3. **実行速度比較 (Differential Execution)**:
   - 同一の WASM 算術ループモジュールを Pure Interpreter (Tier 2) と Hybrid JIT (Tier 3) で実行し、計算結果の等価性と実行所要時間を比較。
