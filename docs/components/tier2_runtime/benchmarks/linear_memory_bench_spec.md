# リニアメモリ ベンチマーク仕様書 (Linear Memory Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {FastAddressCheck} {META_RestrictedPhysicalAccess} {GLOBAL_StrictMemoryLimit} {MemoryBoundaryCheck} -->

正本: `docs/components/tier2_runtime/runtime_vmmio.md` §1, `docs/components/tier2_runtime/runtime_interpreter.md` §3.3
参考実装: `experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py`

WASM ゲストのリニアメモリ（Guest RAM, Stage 1: Bit 31 == 0）に対する高速アクセス性能、単一比較による統一境界チェック（`FastAddressCheck`）のオーバーヘッド、およびメモリ幅（8-bit / 16-bit / 32-bit）ごとの読み書きスループットを計測・実証する。 `{FastAddressCheck}` `{META_RestrictedPhysicalAccess}` `{GLOBAL_StrictMemoryLimit}` `{MemoryBoundaryCheck}`

## 2. ベンチマーク測定項目一覧

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCH-MEM-01** | Raw ゲスト RAM 読み書きスループット（ベースライン） | 64KB RAM, 32-bit ワードアクセス | M ops/sec, ns/op | シミュレータ基準値の把握 | §1, `experiments/pysim/benchmarks/linear_memory/bench_linear_memory.py` |
| **BENCH-MEM-02** | 単一比較 境界チェック (`CMP addr, mem_size`) オーバーヘッド | `addr < guest_ram_size` の単一比較 | ns/op, M ops/sec | 境界チェック遅延が最小限（マスク方式と同等以下）であること | `{FastAddressCheck}` `{MemoryBoundaryCheck}` |
| **BENCH-MEM-03** | vMMIO 高速バイパスアクセス (Bit 31 == 0) | `VMMIOController.access` 経由 | M ops/sec, バンド幅 (MB/s) | PTE 探索を一切行わず即座にバイパス完了すること | `{META_RestrictedPhysicalAccess}` |
| **BENCH-MEM-04** | アクセス幅別スループット (8-bit / 16-bit / 32-bit) | 各バイト幅での連続/ストライドアドレス | M ops/sec | 各データ幅で正常に読み書き可能であること | `runtime_interpreter.md` §3.3 |
| **BENCH-MEM-05** | 部分ページ (8KB/16KB) 境界外アクセストラップ | `guest_ram_size = 8192` | トラップ発生検証 | `addr >= 8192` で即座に `TRAP_MEMORY_OUT_OF_BOUNDS` 検出 | `{FastAddressCheck}` |

## 3. 測定手順と計算式

1. **ベースライン測定**:
   - Python `bytearray(65536)` に対し、32-bit ワードの連続書き込みおよび読み出しを $N=250,000$ 回実行。
   - `Throughput = (2 * N) / (dt * 1e6) [M ops/s]`
2. **境界チェック測定**:
   - `addr >= guest_ram_size` の単一比較とアクセスを実行し、純粋なオーバーヘッド時間を算出。
3. **vMMIO バイパス測定**:
   - Bit 31 == 0 のゲストアドレスを `VMMIOController.access()` に投入し、TLB や FlatMap に触れずに完了することを確認。
