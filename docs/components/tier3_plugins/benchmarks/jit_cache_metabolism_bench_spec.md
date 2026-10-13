# JIT キャッシュ代謝 & コーナーケース ベンチマーク仕様書 (JIT Cache Metabolism Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {HistoryBuffer} {SimpleJITArchitecture} {META_AccessDictionary} {LowLatencyJIT} {WasmCodeSectionPC} -->

正本: [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) jit_runtime.md (Cache Ring), [`jit_cache_model.py`](docs/components/tier3_plugins/formal/jit_cache_model.py)
参考実装: [`bench_jit_cache_metabolism.py`](experiments/pysim/benchmarks/jit/bench_jit_cache_metabolism.py)

Fireball の JIT 実行基盤における **3面循環コードキャッシュ（`Active` / `Warm` / `Oldest`）の代謝メカニズム**、**Oldestのその場実行**、**局所アンリンク安全性**、および極端なワークロードにおける**コーナーケース性能**を定量的に計測・検証する。

## 2. コーナーケース・測定項目一覧

| ベンチマーク ID | コーナーケース / 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-METAB-01** | WarmとOldestのその場実行 | 各世代に常駐traceがある | コードアドレス、常駐バイト数、世代の変化 | lookup前後でコードアドレス、常駐バイト数、世代が変わらない | `{JIT_OldestExecuteOnly}` |
| **BENCHMARK-METAB-02** | 作業集合 (Working Set) 別キャッシュヒット率 | N=8 (局所), N=24 (中規模), N=120 (超過) | キャッシュヒット率 (%) | 局所時 100%, 中規模時 90% 以上, 超過時も安全に循環代謝 | `{JIT_MultiBuffer_Cache}` |
| **BENCHMARK-METAB-03** | キャッシュスラッシング時の代謝速度 (Metabolism Rate) | 3バンク総容量 (48トレース) を超える 200 トレース連続実行 | ローテーション回数, 代謝速度 (Evictions/sec) | メモリリークや断片化なく一定の代謝速度で循環すること | `jit_cache_model.py` |
| **BENCHMARK-METAB-04** | 局所アンリンク & ダングリングチェイン解消 | トレース A $\to$ B チェイン中、B の属するバンクが Oldest 満杯でパージ | アンリンク成功率 (100%), ダングリング参照数 (0件) | Oldest パージ時に被チェイン元 A のリンクが安全にスタブへ復帰解除されること | `jit_runtime.md` `GOTCHA-JITR-05` |
| **BENCHMARK-METAB-05** | 多関数Code-section PCのキャッシュルックアップ | 複数関数・複数モジュールの基本ブロックを混在実行 | 誤ヒット数 (0件), 正しいmodule/PCペアのヒット率 (100%) | モジュール内はCode section payload相対PCで検索し、モジュール間は`(module_id, pc)`で独立解決 | `jit_runtime.md` |

## 3. 測定手順と判定基準

1. **WarmとOldestのその場実行の検証**:
   - トレースをActiveへ挿入し、`rotate()`でWarmへ移してlookupする。コードアドレスと常駐バイト数が変わらないことを確認する。
   - 再度`rotate()`でOldestへ移してlookupする。コードアドレス、常駐バイト数、世代が変わらないことを確認する。
2. **作業集合スケーラビリティ検証**:
   - 容量内のホットループでは 100% ヒット、容量超過時は FIFO/世代交代により非ヒット（Cold）トレースが自然代謝されることを確認。
3. **ダングリングチェイン防止検証**:
   - バンク破棄時に `inbound_sources` の逆引きインデックスを用いてハッシュ衝突を含む候補を照合して退役targetへのリンクを解除し、無効化されたメモリへの参照が一切残らないことを確認。
