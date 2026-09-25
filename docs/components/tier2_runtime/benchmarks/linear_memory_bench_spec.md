# リニアメモリ ベンチマーク仕様書 (Linear Memory Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {FastAddressCheck} {META_RestrictedPhysicalAccess} {GLOBAL_StrictMemoryLimit} {MemoryBoundaryCheck} -->

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) , [`interpreter.md`](docs/components/tier3_executer/interpreter.md) 参考実装: [`bench_linear_memory.py`](experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py)

本ベンチマークは、WASM ゲストのリニアメモリ（Guest RAM、Stage 1: Bit 31 == 0）の高速アクセス性能を計測する。単一比較による統一境界チェック（`FastAddressCheck`）のオーバーヘッドも測定する。8-bit、16-bit、32-bit の各幅で読み書きスループットを実証する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {FastAddressCheck} {MemoryBoundaryCheck} -->

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-MEM-01** | Raw ゲスト RAM 読み書きスループット（ベースライン） | 1 WASM ページ（64KB）のシミュレータ構成、32-bit ワードアクセス | M ops/sec, ns/op | シミュレータ基準値の把握 | [`bench_linear_memory.py`](experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py) |
| **BENCHMARK-MEM-02** | 単一比較 境界チェック (`CMP addr, mem_size`) オーバーヘッド | `addr < guest_ram_size` の単一比較 | ns/op, M ops/sec | 境界チェック遅延が最小限（マスク方式と同等以下）であること | FastAddressCheck, MemoryBoundaryCheck |
| **BENCHMARK-MEM-03** | vMMIO 高速バイパスアクセス (Bit 31 == 0) | `VMMIOController.access` 経由 | M ops/sec, バンド幅 (MB/s) | PTE 探索を一切行わず即座にバイパス完了すること | `{META_RestrictedPhysicalAccess}` |
| **BENCHMARK-MEM-04** | アクセス幅別スループット (8-bit / 16-bit / 32-bit) | 各バイト幅での連続/ストライドアドレス | M ops/sec | 各データ幅で正常に読み書き可能であること | `interpreter.md` |

境界外アドレスのトラップは性能測定項目に含めない。正しさの検証は [`runtime_vmmio_test_spec.md`](docs/qa/tier2_runtime/runtime_vmmio_test_spec.md) の TEST-VMMIO-02/03 で行う。WASMリニアメモリは64KB単位のページ意味論を保ち、vMMIO Stage 1の任意サイズ境界窓を「部分WASMページ」として扱わない。

## 3. 測定手順と計算式

1. **ベースライン測定**:
   - Python `bytearray(65536)` に対し、32-bit ワードの連続書き込みおよび読み出しを $N=250,000$ 回実行。
   - `Throughput = (2 * N) / (dt * 1e6) [M ops/s]`
2. **境界チェック測定**:
   - `addr >= guest_ram_size` の単一比較とアクセスを実行し、純粋なオーバーヘッド時間を算出。
3. **vMMIO バイパス測定**:
   - Bit 31 == 0 のゲストアドレスを `VMMIOController.access()` に投入し、TLB や FlatMap に触れずに完了することを確認。
