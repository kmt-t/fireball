# JIT カードエイジング ベンチマーク仕様書 (JIT Card Aging Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_CardAgingSweep} {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {LowLatencyJIT} -->

正本: [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)（エイジングスイープ）
参考実装: [`bench_jit_aging.py`](experiments/pysim/benchmarks/jit/bench_jit_aging.py)

本ベンチマークは、エイジングスイープが不要なコンパイルと追い出しを抑える効果を測定する。低頻度関数の実行によるキャッシュ状態の変化を対象とする。

## 2. 測定項目一覧
<!-- traceability: {JIT_CardAgingSweep} -->

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-AGING-01** | コールド関数によるキャッシュ汚染 | compile count, eviction count, rotation count, JIT execution ratio | 既定構成のコンパイル数がエイジングなし以下であり、全変種で結果値が一致する | {JIT_CardAgingSweep} |
| **BENCHMARK-AGING-02** | エイジング設定への感度 | compile count, eviction count, JIT execution ratio | 有効な変種でエイジングのステップ数がローテーション数と一致する | {JIT_CardAgingSweep} |
| **BENCHMARK-AGING-03** | コールド関数なしの基準との差 | compile count ratio, eviction count | 基準実行の追い出し数が0である | {JIT_MultiBuffer_Cache} |
| **BENCHMARK-AGING-04** | 総処理時間と処理時間内訳 | total time, compile time, aging time, time per aging step | コンパイル時間とエイジング時間の和が総時間以下である。時間は参考値であり合否に使わない | {JIT_CardAgingSweep} |

処理時間は参考値として記録し、合否判定には使用しない。
