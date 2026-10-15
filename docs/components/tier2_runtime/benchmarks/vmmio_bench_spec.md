# vMMIO アドレス変換 ベンチマーク仕様書 (vMMIO Benchmark Specification)

## 1. 目的と対象範囲
<!-- traceability: {vMMIO_TrapAndEmulate} {PhysicalPassthrough} {DynamicMmap} {UnifiedAccessModel} {META_FlatMapIndexed} {META_RestrictedPhysicalAccess} -->

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)
参考実装: [`bench_vmmio.py`](experiments/pysim/benchmarks/vmmio/bench_vmmio.py)

本ベンチマークは、vMMIO仮想アドレス空間（Bit 31 == 1、Stage 2 / 3）を対象とする。ソフトウェアTLBのヒット遅延、TLBミス時のPTE探索、およびFunction Codeごとのディスパッチ遅延を測定する。

## 2. ベンチマーク測定項目一覧
<!-- traceability: {GOTCHA-VMMIO-02} -->

| ベンチマーク ID | 測定対象 | 計測単位 | 合格基準 | 紐付け |
| :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK-VMMIO-01** | Direct-Mapped TLBのヒット遅延 | ns/hit, M ops/sec | ヒット解決が $O(1)$ で完了する | `runtime_vmmio.md` |
| **BENCHMARK-VMMIO-02** | Folding XORハッシュの計算遅延 | ns/op, M ops/sec | ハッシュ計算が均等分散し、低コストである | `runtime_vmmio.md` GOTCHA-VMMIO-02 |
| **BENCHMARK-VMMIO-03** | TLBミス時のPTE探索と再登録 | ns/walk, M ops/sec | 二分探索によるPTE解決を計測する | {META_FlatMapIndexed} |
| **BENCHMARK-VMMIO-04** | TLBヒットとPTE探索の相対性能 | Ratio | TLBヒットがPTE探索より高速である | `runtime_vmmio.md` {META_RestrictedPhysicalAccess} |
| **BENCHMARK-VMMIO-05** | 静的デバイスのシステムコールディスパッチ | ns/dispatch | ハンドラ呼出しの遅延を記録する | `runtime_vmmio.md` |
| **BENCHMARK-VMMIO-06** | マッピング解除後の未登録ページ検出 | ns/check, trap rate | `TRAP_UNREGISTERED_PAGE`を検出してアクセスを遮断する | {META_RestrictedPhysicalAccess} |

通常アクセスではPTEの`owner_id`と現在タスクIDを照合し、マッピング解除ではPTEとTLBを削除して未登録ページとして遮断する。動作の正本は [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) とする。
