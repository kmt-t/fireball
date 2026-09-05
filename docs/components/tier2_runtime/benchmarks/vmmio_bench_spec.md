# vMMIO アドレス変換 ベンチマーク仕様書 (vMMIO Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {vMMIO_TrapAndEmulate} {PhysicalPassthrough} {DynamicMmap} {UnifiedAccessModel} {META_FlatMapIndexed} {META_RestrictedPhysicalAccess} -->

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)
参考実装: [`bench_vmmio.py`](experiments/pysim/benchmarks/vmmio/bench_vmmio.py)

vMMIO 仮想アドレス空間（Bit 31 == 1, Stage 2/3）における、ダイレクトマップ方式ソフトウェア TLB（16エントリ, Folding XOR Hash）の $O(1)$ キャッシュヒット性能、TLB ミス時の FlatMap（ソート済み PTE 配列, `fireball::flat_map_view`）二分探索ルックアップ時間、および各 Function Code（FC=12 静的デバイス / FC=14 SHM / FC=15 PASSTHROUGH）のディスパッチレイテンシを計測する。 `{vMMIO_TrapAndEmulate}` `{PhysicalPassthrough}` `{DynamicMmap}` `{UnifiedAccessModel}` `{META_FlatMapIndexed}` `{META_RestrictedPhysicalAccess}`

## 2. ベンチマーク測定項目一覧

| ベンチマーク ID | 測定項目 | 前提条件 / 設定 | 計測指標 | 目標性能 / 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCH-VMMIO-01** | Direct-Mapped TLB ヒット レイテンシ ($O(1)$) | 同一 SHM ページ (FC=14) への局所アクセス | ns/hit, M ops/sec | 完全 $O(1)$ で高速解決されること | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md), [`bench_vmmio.py`](experiments/pysim/benchmarks/vmmio/bench_vmmio.py) |
| **BENCH-VMMIO-02** | Folding XOR ハッシュ計算オーバーヘッド | 20-bit VPN $\to$ 4-bit スロット | ns/op, M ops/sec | 均等分散かつ極低コストなビット演算 | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) `{VMMIO-GOTCHA-02}` |
| **BENCH-VMMIO-03** | TLB ミス $\to$ FlatMap 探索 & リフィル ($O(\log N)$) | 32ページ循環アクセス (TLB容量超過) | ns/walk, M ops/sec | 二分探索による安定した PTE 解決 | `{META_FlatMapIndexed}` |
| **BENCH-VMMIO-04** | TLB 加速比 (Hit vs Miss) | TLB ヒット時間 vs FlatMap Walk 時間 | 加速倍率 (Ratio) | TLB ヒットが FlatMap walk より高速であること | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) `{META_RestrictedPhysicalAccess}` |
| **BENCH-VMMIO-05** | 静的デバイス (FC=12) システムコールディスパッチ | `map_static_device` 登録済みハンドラ | ns/dispatch | ハンドラ呼出オーバーヘッドが最小であること | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) |
| **BENCH-VMMIO-06** | RBAC タスク分離・所有権検証コスト | 非所有タスクからの SHM アクセス | ns/check, トラップ率 | `TRAP_OWNER_MISMATCH` を即座に検出し遮断 | `{META_RestrictedPhysicalAccess}` |

## 3. 測定手順

1. **TLB ヒット測定**:
   - 登録済み SHM ページ（FC=14）に対して $N=150,000$ 回の連続アクセスを実行し、TLB ヒット率 100% 時のレイテンシを算出。
2. **TLB ミス & FlatMap 探索測定**:
   - TLB 容量（16エントリ）を超える 32 ページをストライド走査し、TLB ミスに伴う FlatMap（`vmmio_ptes`）二分探索とリフィルのオーバーヘッドを計測。
3. **セキュリティゲート測定**:
   - `caller_task_id` 不一致の不正アクセスを投入し、PTE `owner_id` チェックによる安全なトラップ判定コストを算出。
