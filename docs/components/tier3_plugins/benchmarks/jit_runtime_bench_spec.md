# JIT コンパイラ & ランタイム ベンチマーク仕様書 (JIT Runtime Benchmark Specification)
<!-- evidence:
     formal: ../formal/jit_trace_execution_model.py
-->

## 1. 目的と対象範囲
<!-- traceability: {JIT_AssemblyStencilCodegen} {LowLatencyJIT} {META_AccessDictionary} {META_BinarySearch} {ThreadedInterpreter} -->

正本: [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), [`jit_runtime.md`](docs/components/tier3_plugins/jit_runtime.md)
参考実装: [`bench_jit.py`](experiments/pysim/benchmarks/jit/bench_jit.py)

trace構成と実行性能、ホットスポット状態判定、BasicBlock参照、Runtime API呼出し、およびInterpreterとJITの結果一致を測定する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {META_BinarySearch} {Challenge_JITCacheEfficiency} {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} -->

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-JIT-01** | assembly stencilからのnative trace構成 | traces/sec, µs/trace, bytes | 適格traceのサイズとコンパイル遅延を記録する | {JIT_AssemblyStencilCodegen}, {LowLatencyJIT} |
| **BENCHMARK-JIT-02** | trace長別コンパイル時間 | ns/WASM opcode, µs/trace | trace長とコンパイル時間の関係を記録する。固定patch時間は仮定しない | {LowLatencyJIT} |
| **BENCHMARK-JIT-03** | ホットスポット状態判定とtrace検索 | ns/check, M ops/sec, ns/lookup | カード判定とtrace検索の遅延を記録する | `jit_runtime.md` |
| **BENCHMARK-JIT-04** | 製品経路のBasicBlock参照とtrace検索 | ns/lookup, M ops/sec | Loader metadataに基づくBasicBlock参照を測定する | `jit_runtime.md` |
| **BENCHMARK-JIT-05** | 同一WASM入力のInterpreter/JIT実行性能 | ms/call, host cycles/WASM instruction | 3実行経路の結果が一致し、実行時間を個別に記録する | `{ThreadedInterpreter}`, `jit_compiler.md`, `jit_runtime.md` |
| **BENCHMARK-JIT-06** | 共通コード領域のRuntime API table呼出し | ns/API call, M calls/sec, side effect count | opcode対応API tableから呼び出し、正常継続時に後続JIT operationへ進む | [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md), {JIT_RuntimeAPI}, {PositionIndependentCode} |
