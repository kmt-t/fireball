# JIT コンパイラ & ランタイム ベンチマーク仕様書 (JIT Runtime Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_CopyAndPatch} {JIT_ZeroCompileCostTheorem} {LowLatencyJIT} {META_AccessDictionary} {META_BinarySearch} {ThreadedInterpreter} -->

正本: [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md), [`jit_runtime.md`](docs/components/tier3_jit/jit_runtime.md)
参考実装: [`bench_jit.py`](experiments/pysim/benchmarks/jit/bench_jit.py)

Copy-and-Patch方式によるJITコンパイル速度（トレース結合＋リロケーションパッチ）、2-bitカードマーキング表（`bit_view<2>`）による$O(1)$ホットスポット事前判定、少数の疎なJITエントリをソート配列から二分探索するlookup時間、およびインタープリタ対JITネイティブ実行のスループット比を計測する。

## 2. ベンチマーク測定項目一覧

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-JIT-01** | Copy-and-Patch コンパイル速度 | 基本ブロック (BasicBlock) 4命令 | Traces/sec, µs/trace | 高速なステンシル結合（ゼロ最適化コスト） | `{JIT_CopyAndPatch}`, `{JIT_ZeroCompileCostTheorem}` |
| **BENCHMARK-JIT-02** | 1命令あたりコンパイル時間 | 各 WASM オプコードのパッチ時間 | ns/opcode | 線形スケール（$O(N)$）でパッチ完了 | `{LowLatencyJIT}` |
| **BENCHMARK-JIT-03** | 2-Bit カードマーキング状態判定 ($O(1)$) | `HotspotBitmap` / `bit_view<2>` | ns/check, M ops/sec | インタープリタ実行ループを阻害しない極低コスト | `jit_runtime.md` |
| **BENCHMARK-JIT-04** | 少数JITエントリのソート配列二分探索 | 3バンクに収まる疎なエントリ数（上限は設定容量から算出） | ns/lookup, M ops/sec | 正確なキー検索。JIT用Radix索引は使用しない | `{META_BinarySearch}` |
| **BENCHMARK-JIT-05** | ループ演算スループット比 (Interp vs JIT) | 100,000回算術ループ実行 | 実行時間 (ms), Speedup比 | 差分結果が完全一致し、ネイティブ実行が成立すること | `jit_compiler.md` |
| **BENCHMARK-JIT-06** | コンテキスト所有Cヘルパー末尾遷移 | `ctx + 0x40` の関数ポインタ、空スタック境界 | ns/dispatch, M dispatch/sec, 副作用回数 | 移転先アドレスをコードへ埋め込まず、PICコピー後も同じヘルパーへ到達すること | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) `{PositionIndependentCode}` |

## 3. 測定手順

1. **コンパイル速度測定**:
   - `TraceCompiler.compile_trace()` に対し、算術基本ブロックを $N=10,000$ 回コンパイルし、1トレースあたりの平均所要時間を算出。
2. **カードマーキング & JITエントリ二分探索測定**:
   - `HotspotBitmap.get_state()` と3バンク内のソート済みエントリ配列に対するbinary lookupの単体スループットを $N=100,000$ 回計測。
3. **実行速度比較 (Differential Execution)**:
   - 同一の WASM 算術ループモジュールを Pure Interpreter (Tier 2) と Hybrid JIT (Tier 3) で実行し、計算結果の等価性と実行所要時間を比較。
4. **複雑処理の委譲測定**:
   - `TraceCompiler` に空のネイティブスタック境界と `tail_context_helper=True` を指定し、`WASMContext.set_jit_helpers()` で命令別CPS関数ポインタ配列を設定する。
   - 同一のトレースバイナリを別の実行可能バッファへコピーして呼び出し、コンテキスト内の関数へ到達することと副作用を直接 `assert` する。
   - pysimでは `ctypes` コールバックのPython遷移コストを含むため、組込みCの性能値とは分離して報告する。
