# Tier 3 Executer — コンセプトコード
<!-- traceability: {JIT_Encoder} {JIT_RegisterMapping} -->

WASM interpreterの概念コードは [`interpreter_concept.py`](docs/components/tier3_executer/concepts/interpreter_concept.py) を参照する。

x64 JITの実行可能な実装とテストは `experiments/pysim/tier3_executer/jit/` と `experiments/pysim/qa/tier3_executer/jit/` に置く。ARMv8-MのJIT物理仕様はTBDであり、本ディレクトリにはARMv8-Mの概念実装を置かない。
