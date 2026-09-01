# JIT キャッシュ代謝 & コーナーケース ベンチマーク仕様書 (JIT Cache Metabolism Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestOnly_Promote} {HistoryBuffer} {SimpleJITArchitecture} {META_AccessDictionary} {LowLatencyJIT} -->

正本: `docs/components/tier3_jit/jit_runtime.md` §3.1, §4.1, `docs/components/tier3_jit/formal/jit_cache_model.py`
参考実装: `experiments/pysim/benchmarks/jit/bench_jit_cache_metabolism.py`

Fireball の JIT 実行基盤における **3面循環コードキャッシュ（`Active` / `Warm` / `Oldest`）の代謝メカニズム**、**Oldest 限定昇格（`{JIT_OldestOnly_Promote}`）**、**局所アンリンク安全性**、および極端なワークロードにおける**コーナーケース性能**を定量的に計測・検証する。 `{JIT_MultiBuffer_Cache}` `{JIT_OldestOnly_Promote}` `{HistoryBuffer}` `{SimpleJITArchitecture}` `{META_AccessDictionary}` `{LowLatencyJIT}`

## 2. コーナーケース・測定項目一覧

| ベンチマーク ID | コーナーケース / 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCH-METAB-01** | Oldest 限定昇格 (Oldest-Only Promotion) 特性 | Warm ヒット vs Oldest ヒット | 昇格コピー数, ヒット率 (%) | Warm ヒット時は昇格せず、Oldest ヒット時のみ Active へ昇格すること | `{JIT_OldestOnly_Promote}` |
| **BENCH-METAB-02** | 作業集合 (Working Set) 別キャッシュヒット率 | N=8 (局所), N=24 (中規模), N=120 (超過) | キャッシュヒット率 (%) | 局所時 100%, 中規模時 90% 以上, 超過時も安全に循環代謝 | `{JIT_MultiBuffer_Cache}` |
| **BENCH-METAB-03** | キャッシュスラッシング時の代謝速度 (Metabolism Rate) | 3バンク総容量 (48トレース) を超える 200 トレース連続実行 | ローテーション回数, 代謝速度 (Evictions/sec) | メモリリークや断片化なく一定の代謝速度で循環すること | `jit_cache_model.py` §26 |
| **BENCH-METAB-04** | 局所アンリンク & ダングリングチェイン解消 | トレース A $\to$ B チェイン中、B の属するバンクが Oldest 満杯でパージ | アンリンク成功率 (100%), ダングリング参照数 (0件) | Oldest パージ時に被チェイン元 A のリンクが安全にスタブへ復帰解除されること | `jit_runtime.md` §4.1 (5) |
| **BENCH-METAB-05** | 多関数 (`UnifiedPC`) PC 衝突防止キャッシュルックアップ | 同一オフセットを持つ func_0 と func_1 の混在実行 | 誤ヒット数 (0件), 分離ヒット率 (100%) | `UnifiedPC`（`func_idx << 16 | offset`）により衝突なしで独立解決 | `jit_runtime.md` §3.1 |

## 3. 測定手順と判定基準

1. **Oldest 限定昇格の検証**:
   - トレースを Active に投入し、`rotate()` で Warm へ遷移させた状態でアクセス $\to$ 昇格回数が 0 であることを確認。
   - 再度 `rotate()` で Oldest へ遷移させた状態でアクセス $\to$ 即時に Active へ昇格され、昇格回数が +1 されることを確認。
2. **作業集合スケーラビリティ検証**:
   - 容量内のホットループでは 100% ヒット、容量超過時は FIFO/世代交代により非ヒット（Cold）トレースが自然代謝されることを確認。
3. **ダングリングチェイン防止検証**:
   - バンク破棄時に `inbound_sources` の逆引きインデックスを用いて被チェイン元のみを $O(k)$ でアンパッチし、無効化されたメモリへの参照が一切残らないことを確認。
