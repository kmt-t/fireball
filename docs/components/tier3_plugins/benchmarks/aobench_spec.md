# 3D レイトレーシング (AO-Bench) ベンチマーク仕様書 (AO-Bench Specification)

## 1. 目的と対象範囲
<!-- traceability: {Wasm32Only} {LowLatencyJIT} {ThreadedInterpreter} {JIT_AssemblyStencilCodegen} -->

正本: [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md), [`wasm_instruction_set.md`](docs/specs/wasm_instruction_set.md)
参考実装: [`bench_aobench.py`](experiments/pysim/benchmarks/aobench/bench_aobench.py)

本仕様は、組み込み WASM 実行環境上の3D Ambient Occlusion レイトレーシング（AO-Bench）を対象とする。ベンチマークは Q8.8 固定小数点数演算を使う。

実ワークロードで Tier 2 スレッド化インタープリタと Tier 3 assembly stencil trace JIT の総合演算性能およびレイ描画スループットを測定する。両実行系の出力を差分検証（Differential Check）する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {Wasm32Only} -->

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-AO-01** | プライマリレイと球・平面の交差判定 | 交差数, ms/frame | 球との交差を272画素分検出する | [`bench_aobench.py`](experiments/pysim/benchmarks/aobench/bench_aobench.py) |
| **BENCHMARK-AO-02** | 半球サンプリングと遮蔽積分 | 総レイトレース数 | 固定小数点誤差の蓄積を防ぐ | {Wasm32Only} |
| **BENCHMARK-AO-03** | レンダリング出力の差分検証 | byte, B | Tier 2とTier 3の出力が528 byteで完全一致し、NULを含まない | {LowLatencyJIT} |
| **BENCHMARK-AO-04** | フレーム描画スループット | ms/frame, Rays/sec | インタープリタとJITの双方で安定して完了する | {ThreadedInterpreter} |
