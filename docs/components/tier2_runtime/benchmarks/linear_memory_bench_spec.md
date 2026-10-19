# リニアメモリ ベンチマーク仕様書 (Linear Memory Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {FastAddressCheck} {META_RestrictedPhysicalAccess} {GLOBAL_StrictMemoryLimit} {MemoryBoundaryCheck} -->

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) , [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) 参考実装: [`bench_linear_memory.py`](experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py)

本ベンチマークは、WASM ゲストのリニアメモリ（Guest RAM、Stage 1: Bit 31 == 0）の高速アクセス性能を計測する。単一比較による統一境界チェック（`FastAddressCheck`）のオーバーヘッドも測定する。8-bit、16-bit、32-bit の各幅で読み書きスループットを実証する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {FastAddressCheck} {MemoryBoundaryCheck} {VDMA} {WasmFCSubset} -->

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-MEM-01** | ゲストRAMの読み書きスループット | M ops/sec, ns/op | 基準値を記録する | `runtime_vmmio.md`, `interpreter.md` |
| **BENCHMARK-MEM-02** | 統一境界チェックのアクセス遅延 | ns/op, M ops/sec | 単一比較の遅延が最小となり、マスク方式と同等以下である | {FastAddressCheck}, {MemoryBoundaryCheck} |
| **BENCHMARK-MEM-03** | ゲストRAM領域のvMMIOバイパス性能 | M ops/sec, MB/s | PTE探索を行わずに完了する | {META_RestrictedPhysicalAccess} |
| **BENCHMARK-MEM-04** | 8-bit / 16-bit / 32-bitアクセスのスループット | M ops/sec | 各幅の読み書きが正常に完了する | `interpreter.md` |
| **BENCHMARK-MEM-05** | `memory.copy`のCPU経路 | 完了遅延, MB/s | WASM意味論との一致を確認し、CPU経路の性能を測定する。DMA選択しきい値は定義しない | {WasmFCSubset} |

境界外アドレスのトラップは性能測定対象に含めない。正しさは [`runtime_vmmio_test_spec.md`](docs/qa/tier2_runtime/runtime_vmmio_test_spec.md) の TEST-VMMIO-02/03 で検証する。WASMリニアメモリは `FB_CONF_WASM_PAGE_SIZE` 単位のページ意味論を保ち、vMMIO Stage 1の任意サイズ境界窓を「部分WASMページ」として扱わない。
