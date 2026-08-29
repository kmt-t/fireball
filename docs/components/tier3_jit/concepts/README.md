# Tier 3 JIT — コンセプトコード

- [`jit_copy_patch_concept.py`](jit_copy_patch_concept.py): Copy-and-Patch エンジンと MPU W^X トランザクション単体。
- 統合ランタイム（インタプリタ + トレーシング JIT + 3面キャッシュ + MPU W^X）の正本は
  [`../../tier2_runtime/concepts/runtime_engine_concept.py`](../../tier2_runtime/concepts/runtime_engine_concept.py) に置く。
  実行モデル全体を跨ぐため Tier 2 Runtime 側を単一の正本とし、ここでは複製しない。
- [`stack_cache_concept.py`](stack_cache_concept.py): スタックトップキャッシング版ステンシル。
  `{JIT_RegisterMapping}` の実装。素朴なステンシル 23 命令に対し 12 命令
  （PUSH/POP を完全に消去、ベンチマーク証跡: [`../benchmarks/zero_runtime_overhead_bench.py`](../benchmarks/zero_runtime_overhead_bench.py) 参照）。`i32.load` / `i32.store` の境界チェックを含む。
