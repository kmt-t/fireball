# JIT キャッシュ代謝 & コーナーケース ベンチマーク仕様書 (JIT Cache Metabolism Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {HistoryBuffer} {SimpleJITArchitecture} {META_AccessDictionary} {LowLatencyJIT} {WasmCodeSectionPC} -->

正本: [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md) jit_runtime.md (Cache Ring), [`jit_cache_model.py`](docs/components/tier3_plugins/formal/jit_cache_model.py)
参考実装: [`bench_jit_cache_metabolism.py`](experiments/pysim/benchmarks/jit/bench_jit_cache_metabolism.py)

Fireball の JIT 実行基盤における **3面循環コードキャッシュ（`Active` / `Warm` / `Oldest`）の代謝メカニズム**、**Oldestのその場実行**、**局所アンリンク安全性**、および極端なワークロードにおける**コーナーケース性能**を定量的に計測・検証する。

## 2. コーナーケース・測定項目一覧

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-METAB-01** | WarmとOldest世代のトレース参照 | コードアドレス, 常駐バイト数, 世代 | 参照前後でコードアドレス、常駐バイト数、世代が変わらない | {JIT_OldestExecuteOnly} |
| **BENCHMARK-METAB-02** | 作業集合別のキャッシュヒット率 | cache hit rate (%) | 局所集合で100%、中規模集合で90%以上、容量超過時も安全に世代交代する | {JIT_MultiBuffer_Cache} |
| **BENCHMARK-METAB-03** | キャッシュ循環時の退役率 | rotation count, evictions/sec | メモリリークや断片化なく循環する | `jit_cache_model.py` |
| **BENCHMARK-METAB-04** | 局所アンリンクとダングリングチェインの解消 | unlink success rate, dangling reference count | 解除率100%、ダングリング参照0件となる | `jit_runtime.md` GOTCHA-JITR-05 |
| **BENCHMARK-METAB-05** | 複数関数・モジュールのCode-section PC検索 | incorrect hits, correct module/PC hit rate | 誤ヒット0件、正しいmodule/PCのヒット率100%となる | `jit_runtime.md` |
