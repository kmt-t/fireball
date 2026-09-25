# メモリマネージャ 物理実装 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md)（契約は [`system_memory.md`](docs/components/tier1_interface/system_memory.md) を正本とする）
参考実装: [`runtime_memory_concept.py`](docs/components/tier2_runtime/concepts/runtime_memory_concept.py)

`system_allocator`/`shm_allocator` のソフトウェア契約、共有ブロック所有権、および vMMIO PTE/TLB 通知を検証する。ARMv8-MのMPU/W^X物理実装はTBDであり、この仕様書の受け入れ対象に含めない。契約レベルの公開API振る舞い（TEST-MEM-01〜13）は [`system_memory_test_spec.md`](docs/qa/tier1_interface/system_memory_test_spec.md) の責務とする。

## 2. テストケース一覧

### ページ単位権限分離の物理実装

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-MEM-14 | 4KB仮想予約スロットの所有権分離 | タスク1とタスク2が`allocate-shared`実行 | 割り当てられた`page_idx`を比較 | 各ブロックは異なる4KB仮想予約スロット（異なる`page_idx`）を占有し、物理SHMバイト予算は要求サイズ分だけ増える | 「4KB仮想予約と可変長物理バック領域」, pysim `test_mem_14_page_granular_permission_isolation` |
| TEST-MEM-15 | vMMIO FC=14マッピングとTLBフラッシュ連動 | 共有メモリ操作 | `allocate_shared`/`release`/`claim`/`drop`実行 | vMMIO FC=14 PTEのマップ／アンマップと連動して対応VPNのTLBスロットが即座にフラッシュされる | 「共有メモリマッピングと仮想化リスナーへのコールバック委譲」, pysim `test_mem_15_vmmio_fc14_tlb_sync` |
| TEST-MEM-16 | 他タスク所有SHMページへのvMMIOアクセス遮断 | タスク1がSHM確保 | タスク2のコンテキスト（未マッピング状態）でvMMIO経由アクセス | `TRAP_UNREGISTERED_PAGE`（未登録ページフォルト）により安全に遮断される | runtime_vmmio.md, pysim `test_mem_15_vmmio_fc14_tlb_sync`（同テスト内でTask 2の`OWNER_MISMATCH`遮断として検証済み） |
| TEST-MEM-10b | 送信中共有ブロックのPTE/TLB遮断とclaim再マッピング | 送信タスクが`release()`を実行 | 送信中にアクセスし、受信タスクが`claim()` | 送信中は旧PTE/TLBが無効化され、`claim()`後は受信側へ再マッピングされる | `runtime_memory_concept.py` `test_mem_10b_shared_block_vmmio_pte_flight_and_claim` |
| TEST-MEM-10c | 転送失敗時の所有権・PTEロールバック | `release()`済みで転送失敗 | `rollback_transfer(original_sender_id, shm_id)`を実行 | 所有権とPTEが送信元へ復元される | `runtime_memory_concept.py` `test_mem_10c_rollback_transfer_restores_mapping` |

### ARMv8-M物理メモリ保護（TBD）

物理メモリ保護機構、領域配置、実行可能メモリの権限遷移、命令キャッシュ同期、実機受け入れ条件はすべてTBDであり、本書ではテスト期待値を定めない。

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {OwnerMismatchTrap} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-MEM-01 | 4KB仮想予約と物理SHM予算の分離 | タスクAとタスクBが共有メモリブロックを要求 | `page_idx`、物理基点、実サイズ、`shm_allocated_bytes`を検証 | 異なるブロックは異なる4KB仮想予約スロットを使い、物理バック領域は要求サイズだけを消費する。4KBの仮想予約自体は4KB物理RAMの消費として計上しない | `runtime_memory.md` {PageGranularPermissionIsolation} |
| GOTCHA-MEM-02 | RAII 解放時の所有者タスク検証（不正解放の完全遮断） | タスクAが共有ブロックを所有 | タスクBのコンテキストで該当ブロックの `release()`/`get_address()`/`get_size()` を試行 | `SharedBlock` は所有タスクIDを保持し、呼び出し元タスクIDとの不一致時に操作を拒否する（`runtime_memory_concept.py` `test_mem_gotcha_02_shared_block_release_owner_only`、pysim `test_mem_gotcha_02b_release_owner_only` で検証）。**実装の勘所**: 共有メモリ ID さえ知っていれば誰でも解放・アクセスできる素朴なアロケータ設計にすると、他タスクのブロックを勝手に解放・読み書きする悪意ある攻撃やバグを防げない | `runtime_memory.md` {OwnershipTransfer} |
| GOTCHA-MEM-03 | 送信中状態のアンマップとロールバック保護 | 送信タスクが `release()` を実行 | 送信中ブロックに対する送受信双方からのアクセスを試行 | vMMIO から PTE がアンマップされ、TLB が即時破棄されているため、いかなるタスクからのアクセスも `TRAP_UNREGISTERED_PAGE` で遮断される。転送失敗時は `rollback_transfer()` により安全に送信元空間へ再マッピングされる。**実装の勘所**: 送信中リソースへの書き込みを許すと、受信側が破損データを読み取る TOCTOU（Time-of-Check to Time-of-Use）脆弱性が発生する | `runtime_memory.md` OwnerMismatchTrap |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（物理実装レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- ARMv8-Mの物理メモリ保護、領域配置、権限遷移、命令キャッシュ同期、および実機検証はすべてTBD。
- 契約レベルの公開API振る舞い（TEST-MEM-01〜13）は [`system_memory_test_spec.md`](docs/qa/tier1_interface/system_memory_test_spec.md) を参照。
