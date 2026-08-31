# 3D レイトレーシング (AO-Bench) ベンチマーク仕様書 (AO-Bench Specification)

## 1. 目的と対象範囲
<!-- traceability: {Wasm32Only} {LowLatencyJIT} {ThreadedInterpreter} {JIT_CopyAndPatch} -->

正本: `docs/components/tier3_jit/jit_compiler.md`, `docs/specs/wasm_instruction_set.md`
参考実装: `experiments/pysim/benchmarks/aobench/bench_aobench.py`

組込み WASM 実行環境上で動作する実機 3D Ambient Occlusion レイトレーシングベンチマーク（AO-Bench, Q8.8 固定小数点数演算）を通じて、実ワークロードにおける Tier 2 スレッド化インタープリタおよび Tier 3 Copy-and-Patch JIT の総合演算性能、レイ描画スループット、および差分検証（Differential Check）を実施する。 `{Wasm32Only}` `{LowLatencyJIT}` `{ThreadedInterpreter}` `{JIT_CopyAndPatch}`

## 2. ベンチマーク測定項目一覧

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCH-AO-01** | プライマリレイ・球・平面交差判定 | 32 x 16 グリッド (512レイ) | レイ交差数, ms/frame | 272画素の球交差を正確に検出 | `experiments/pysim/benchmarks/aobench/bench_aobench.py` |
| **BENCH-AO-02** | 半球サンプリング・遮蔽積分 (AO Shading) | 4 サンプル/交差点 (1,088レイ) | 総レイトレース数 (1,600レイ) | 陰影計算の固定小数点誤差蓄積防止 | `{Wasm32Only}` |
| **BENCH-AO-03** | レンダリング出力の差分検証 | WASI `fd_write` 出力バッファ | バイト一致 (528 B, 0 NUL) | Tier 2 と Tier 3 の描画結果がバイト完全一致 | `{LowLatencyJIT}` |
| **BENCH-AO-04** | フレームスループット (Rays / Sec) | 1フレーム描画所要時間 | ms/frame, Rays/sec | インタープリタおよび JIT で安定完走 | `{ThreadedInterpreter}` |

## 3. 測定手順

1. **WASM バイナリ展開**:
   - `aobench.wasm`（Q8.8 固定小数点数版）を `wasm_reader.parse()` で解析。
2. **Tier 2 インタープリタ実行**:
   - `Interpreter.call()` で 32x16 グリッドを描画し、WASI stdout 出力文字列および所要時間を記録。
3. **Tier 3 JIT ハイブリッド実行**:
   - 2-bit カードマーキングによりホットスポットを検知し、`idle_hook` 経由で JIT トレースをコンパイルして実行。
4. **差分照合**:
   - 両エンジンの描画結果を完全照合（Exact byte-for-byte match）し、不変性を確認。
