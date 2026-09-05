# Tier 3 JIT — コンセプトコード & 検証スクリプト

- [`jit_copy_patch_concept.py`](docs/components/tier3_jit/concepts/jit_copy_patch_concept.py): `{JIT_CopyAndPatch}` Copy-and-Patch エンジンと MPU W^X トランザクション単体。
- [`jit_assembler_constexpr_concept.py`](docs/components/tier3_jit/concepts/jit_assembler_constexpr_concept.py): `{JIT_Encoder}` C++20 constexpr Thumb-2 アセンブラ概念実装。
- [`stack_cache_concept.py`](docs/components/tier3_jit/concepts/stack_cache_concept.py): `{JIT_RegisterMapping}` スタックトップキャッシング版ステンシル（素朴ステンシル 23 命令 $\to$ 12 命令に削減）。
- [`thumb2_stencil_semantic_verifier.py`](docs/components/tier3_jit/concepts/thumb2_stencil_semantic_verifier.py): Unicorn ARMv8-M エミュレータによるステンシルバイナリの実機意味論検証。
- [`jit_trace_execution_verifier.py`](docs/components/tier3_jit/concepts/jit_trace_execution_verifier.py): Unicorn ARMv8-M 上での JIT トレース出力マシンコード実実行検証。
- 統合ランタイム（インタープリタ + トレーシング JIT + 3面キャッシュ + MPU W^X）の正本は
  [`runtime_engine_concept.py`](docs/components/tier2_runtime/concepts/runtime_engine_concept.py) に置く。
  実行モデル全体を跨ぐため Tier 2 Runtime 側を単一の正本とし、ここでは複製しない。
