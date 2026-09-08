# メモリマネージャ 抽象契約 コンポーネント設計書 {VERIFY_LLM}
<!-- evidence:
     test: tests/system_memory_test_spec.md
-->

## 1. コンセプト
<!-- traceability: {META_3TierSeparation} {GLOBAL_Policy_Memory} {ConsolidatedHeap} {GLOBAL_IndependentHeap} {GLOBAL_StrictMemoryLimit} -->
メモリマネージャ（`memory-manager`）は、システム全体の統合物理メモリプール（`ConsolidatedHeap`）を基礎とし、そこからホスト（WASMランタイム）用のヒープ、および各VM/タスク用のヒープを、それぞれ物理的・領域的に完全に独立した別個のヒープ（`GLOBAL_IndependentHeap`）として切り出して貸与するという**パーティション貸与ポリシー**の抽象契約（`co_mem`）を定義する。これにより、特定のVMでのメモリ不足が他のVMやホストランタイムを道連れにしてクラッシュすることを防止する。

本コンポーネントは「何を提供するか（契約）」のみを定義する。`system_allocator`（システムコンテナ用）および `shm_allocator`（IPC共有メモリ用）による dlmalloc ベースの具体的な実装は、Tier 2 の [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を正本とする（`document_structure.md` §2.1-4「契約/実装分割パターン」）。 `{META_3TierSeparation}` `{GLOBAL_Policy_Memory}` `{ConsolidatedHeap}` `{GLOBAL_IndependentHeap}` `{GLOBAL_StrictMemoryLimit}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 1 (主要システムコンポーネント: Primary Component)** に属し、システム全体のメモリパーティション貸与ポリシーおよび独立ヒープ不変条件を定義する抽象契約を担当する。実装（`system_allocator`/`shm_allocator`）は Tier 2 の [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) が担う。 `{META_3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
- **`MemoryManager`**: パーティション管理とアロケーションロジックの契約をカプセル化する抽象インターフェース。具体的なアロケータ実装（`system_allocator`/`shm_allocator`）は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を参照。
- **`partition_info`**: パーティションの境界と使用状況の可視化。

### 3.2 依存関係 (Zero-cost DI)
- `initialize` メソッドにより、管理対象の物理メモリプールの基点アドレスとサイズを受け取る。実装側は `runtime_memory.md` の `system_allocator`/`shm_allocator` によりこれを具体化する。

## 4. インターフェース設計

<!-- traceability: {GLOBAL_Policy_Memory} -->
本コンポーネントの公開APIは `{META_StaticDI}` が定義する `co_mem` インターフェース契約そのものである。実装側（`runtime_memory.md`）は本契約をそのまま実現し、契約自体（メソッド名・引数・戻り値の意味）に食い違いがあれば本節を正とする。

WITインターフェース名は kebab-case で定義されるが、C++の公開APIバインディングにおいては、`fireball` 名前空間の下に `snake_case`（例: `fireball::init_manager`）として実装・公開される。

#### 初期化

<!-- traceability: {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {Size_15KLOC} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | メモリマネージャを初期化する。 |
| シグネチャ | `init-manager(pool-base: address, pool-size: byte-count) -> operation-result`<br>(C++マッピング: `fireball::init_manager`) |
| 引数 | `pool-base`: 物理メモリプールの基点アドレス<br>`pool-size`: プールのバイトサイズ |
| 戻り値 | 操作結果 |

#### パーティションの貸与（acquire-partition）

<!-- traceability: {GLOBAL_Policy_Memory} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | タスク固有の静的メモリパーティションを貸与する。**汎用ヒープAPIではない**: `{CooperativeMultitasking}`が明示するとおり、`size_t`指定の任意サイズ確保も`void*`の返却も提供せず、コンパイル時に確定した固定長パーティションのみを貸し出す。 |
| シグネチャ | `acquire-partition(owner: task-id) -> result<partition-view, memory-error>`<br>(C++マッピング: `fireball::co_mem::acquire_partition`) |
| 引数 | `owner`: パーティションの貸与先タスクID |
| 戻り値 | 成功時は `partition-view`（`std::span<std::byte>` 相当の非所有ビュー）。失敗時は `memory-error` |
| 事後条件 | 返却された `partition-view` の範囲は他タスクのパーティションと重複しない |

#### パーティションの返却（release-partition）

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | `acquire-partition` で貸与されたパーティションを返却する。 |
| シグネチャ | `release-partition(owner: task-id) -> void`<br>(C++マッピング: `fireball::co_mem::release_partition`) |
| 引数 | `owner`: 返却元タスクID |
| 不変条件 | 所有者以外からの呼び出しは無効（返却されない） |

#### 型付きプールスロットの貸与・返却（acquire-slot / release-slot）

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 静的プール内の型付きスロットを貸与・返却する。`partition-view`同様、動的サイズ指定は行わない。 |
| シグネチャ | `acquire-slot<T>() -> result<pool-ref<T>, memory-error>`<br>`release-slot<T>(ref: pool-ref<T>) -> void`<br>(C++マッピング: `fireball::co_mem::acquire_slot<T>` / `release_slot<T>`) |
| 戻り値 | 成功時は `pool-ref<T>`（静的プール内スロットへの型付きハンドル） |

##### `allocate-shared` (IPC転送データ専用)
<!-- traceability: {OwnershipTransfer} {Shm_Allocator} -->
IPC転送のための共有メモリブロック確保は、上記の `acquire-partition`/`acquire-slot` とは別のライフサイクルを持つ。実装側（`runtime_memory.md`）の `shm_allocator` を介して、指定されたバイト数（`size`）の可変長バッファを切り出す。所有権の移動が `{ThreeStageRouting}` の Revoke → Rendezvous → Grant と連動し、`shared-block` はこの上位仕様が管理する状態を物理メモリ側で保持するRAIIラッパーであり、独自の所有権管理を並行して持つものではない。`release()`/`claim()` の呼び出しは、`{ThreeStageRouting}` のRevoke/Grantフェーズおよび対応する vMMIO PTE のアンマップ／再マッピング（および TLB フラッシュ）と連動する。 `{OwnershipTransfer}` `{Shm_Allocator}`

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC転送用の共有メモリブロックを割り当て、RAII所有権を持つリソースを返す。 |
| シグネチャ | `allocate-shared(size: byte-count) -> result<shared-block, recovery-strategy>` |
| 引数 | `size`: 割り当てサイズ |
| 戻り値 | 成功時は `shared-block` リソース |
| 事後条件 | 対応する vMMIO FC=14 ページが呼び出し元タスクの仮想アドレス空間にマッピング登録される（`map_shm_page` 相当） |
| 補足 | なお、HAL デバイス通信用のバッファは本共有メモリとは直交し、HAL 自身が管轄する固定長の HALバッファプール（vMMIO DYNAMIC 領域）から切り出される。 |

#### 所有権要求（claim）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC経由で受け取った共有メモリIDから、所有権を持つリソースを取得する。 |
| シグネチャ | `claim(id: shm-id) -> result<shared-block, recovery-strategy>` |
| 引数 | `id`: 共有メモリID（`{Syscall_Mapping}` / `coos_system.wit` の `shm-handle.handle` と同一の `(page_idx << 8) | slot_idx` 形式） |
| 戻り値 | 成功時は `shared-block` リソース |
| 事前条件 | `{ThreeStageRouting}` のGrantフェーズが完了済み（対応するvMMIO PTEが受領側タスク空間にマッピング登録済み）であること |

#### 解放（deallocate）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | `acquire-partition`/`acquire-slot` で確保したローカルメモリを解放する。 |
| シグネチャ | `deallocate(addr: address) -> void` |
| 引数 | `addr`: 解放するメモリアドレス |
| 補足 | 共有メモリは `shared-block` のデストラクタで自動解放される。 |

## 5. 制約達成の方策
<!-- traceability: {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {GLOBAL_IndependentHeap} -->

### 5.1 性能制約と不変条件
- **目標**: 決定論的 $O(1)$ または有界 $O(\log n)$ のメモリ割り当て・解放。
- **方策**: 契約としては貸与単位（固定長パーティション・型付きスロット・可変長共有ブロック）のみを規定し、具体的な計算量保証の実現手段（バンプアロケータ、dlmalloc アリーナ等）は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を正本とする。

### 5.2 メモリ制約と方策
<!-- traceability: {GLOBAL_StrictMemoryLimit} {GLOBAL_IndependentHeap} -->
- **目標**: 総メモリ消費を有界化し、タスク間のヒープ干渉を完全に防止。
- **方策**:
  - `{GLOBAL_StrictMemoryLimit}`: システム全体の総割当量をコンパイル時定数（`FB_CONF_MEMORY_POOL_SIZE`、定義は `system_config.md` 正本）以内に厳格制限する契約とする。
  - `{GLOBAL_IndependentHeap}`: 各タスク・各ランタイムに独立した静的パーティションを割り当て、ヒープ干渉を物理的に排除する契約とする。共有メモリは 4KB ページ単位で完全に分離する（物理実装は `runtime_memory.md` の `{ADR_PageGranularPermissionIsolation}` を参照）。

### 5.3 安全性制約と方策
- **目標**: 不正アクセス・二重解放の完全排除。
- **方策**: 所有者以外からの操作を無効とする契約を課す。ハードウェア（MPU/vMMIO）による具体的な執行方式は `runtime_memory.md` を正本とする。

## 6. 所有権追跡と共有メモリライフサイクル
<!-- traceability: {GLOBAL_Policy_Memory} {META_FaultIsolation} {OwnershipTransfer} {VmmioShmDelegation} -->

### 6.1 所有権追跡仕様
各メモリブロックは `memory-info.owner` で割り当て元 task-id を追跡する。本コンポーネントが提供する `acquire-partition`/`acquire-slot`/`deallocate` や `RAII`/`drop` による解放は、無秩序なシステム全体の野良ヒープ確保ではなく、用途別に事前確保された独立パーティションから有界に切り出し、使用後に返却・合体する安全なメモリ管理契約を指す。具体的なアロケータ（`shm_allocator`, `system_allocator` 等）は `runtime_memory.md` を正本とする。 `{GLOBAL_Policy_Memory}`

- **自動設定**: `acquire-partition` / `acquire-slot` / `allocate-shared` 時に呼び出し元タスクIDが自動設定される。
- **所有者限定操作**: `deallocate`（`release-partition`/`release-slot` 相当）は所有者タスクのみが実行可能。
- **RAII自動返却**: `shared-block` リソースの RAII / drop によるプールへの自動返却。

### 6.2 共有メモリマッピングと仮想化リスナーへのコールバック委譲（設計原則）
<!-- traceability: {VmmioShmDelegation} {OwnerMismatchTrap} -->
メモリマネージャは、クリーンアーキテクチャ（依存性逆転の原則: DIP）に従い、特定の上位仮想化ハードウェア（vMMIO 等）の内部シンボルや特定の仮想アドレス体系（`0xE000_0000`）に直接依存しない。
メモリマネージャは物理ページマッピングのライフサイクルイベントを通知するイベント通知インターフェース（リスナー機構）を契約として提供し、仮想化層（vMMIO コントローラ等）がこれを購読・登録する設計とする。 `{VmmioShmDelegation}`

- **通知されるライフサイクルイベント**:
  - **ページ割り当て**: 物理ページの確保と初期所有者・読み書き権限の確定時
  - **所有権移譲**: タスク間でのブロック受け渡し（Grant 等）に伴う所有タスクIDの変更時
  - **所有権回収（Revoke）**: メッセージ送信開始等に伴う所有権の一時無効化（移譲中状態の設定およびTLBフラッシュ契機）
  - **ページ解放**: 共有ブロック破棄に伴う物理ページの解放時
- 物理実装（イベント発火の具体的トリガー・4KBページ粒度での執行手順）は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) §6 を正本とする。 `{OwnerMismatchTrap}`

### 6.3 共有メモリライフサイクルと権限遷移プロトコル（契約レベル）
<!-- traceability: {OwnershipTransfer} {META_FaultIsolation} -->
`shared-block` リソースが物理メモリ側での所有権の単位である。アクセス可否の執行は、マッピング有無（vMMIO FC=14 の PTE 存在・VALID）によって行われる。`shared-block`の`release()`/`claim()`は、`{ThreeStageRouting}` のRevoke→Rendezvous→Grantと1対1で対応する物理層の操作であり、独立した二重の所有権管理を行うものではない。 `{META_FaultIsolation}` `{OwnershipTransfer}`

##### ライフサイクルフェーズ遷移表
| ステップ | フェーズ | 実行API / イベント | 送信元(Task A) | 受信先(Task B) |
| :---: | :--- | :--- | :--- | :--- |
| 1 | 確保 | `allocate_shared(size)` | 所有 (`TaskA`) | - |
| 2 | 書込 | `shm.write_*` | 書込可能 | - |
| 3 | 送信開始 | `shm.release()` | **無効化** (ハンドル返却) | - |
| 4 | メッセージ化 | `shm-id` を kv_pair に格納 | - | - |
| 5 | ランデブー | `ipc.send(chan, msg)` | サスペンド待機 | - |
| 6 | 認可・受信 | `ipc.recv(chan)` | 待機解除 | 受信完了 |
| 7 | 所有権取得 | `claim(shm-id)` | - | **所有** (`TaskB`) |
| 8 | 読出 | `shm.read_*` | - | 読出可能 |
| 9 | 自動解放 | `shared-block` の RAII drop | - | **解放** |

物理層（vMMIO PTE / TLB）での各フェーズの具体的な執行内容は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) §6.4 を正本とする。

- **非所有タスク操作の完全遮断 (`MEM-GOTCHA-02`)**: 共有メモリブロックの操作時、ブロックの所有タスク ID を厳格に照合し、非所有タスクからの操作は即座にトラップ（`ShmTrap` / `ERR_PERMISSION_DENIED`）で遮断する。
- **障害時回復**: Rendezvous中に通信が中断された場合、`rollback_transfer(original_sender_id, shm_id)` により送信元タスクへ所有権を再マッピングし、リソースのダングリングを防止する。

## 7. 設計判断 (ADR)
<!-- traceability: {ADR_SharedBlockRaii} {ADR_MemoryManagerMinimalSurface} -->
このコンポーネントのADRは `{ADR_SharedBlockRaii}` および `{ADR_MemoryManagerMinimalSurface}` のキーワードで参照される。物理実装に関する `{ADR_PageGranularPermissionIsolation}` は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を正本とする。

- **決定事項**: `{ADR_SharedBlockRaii}` (2026-02-17)
  - **背景**: IPC転送用の共有メモリを、単なる`shm-id`（整数）として扱うか、所有権を持つリソース型として扱うかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: `shm-id`を単なる整数IDとし、明示的な`release_shm(id)`/`acquire_shm(id)`関数で操作する。実装は単純だが、解放忘れやダングリング参照を型システムで防げない。
    - 案2: `shm-id`をRAII所有権を持つ`shared-block`リソースとして設計し、`release()`/`claim()`で所有権移動を明示し、デストラクタで自動解放する。
  - **結論**: 案2を採用する。
  - **理由**: `release()`で送信側が無効化、`claim()`で受信側が取得する設計により、ダングリングポインタを構造的に防止できる。デストラクタでの自動解放により手動`deallocate`忘れも排除できる。`to-shm`/`to-address`のような対称的な変換名より`release`/`claim`の方が所有権移動という意図を明確に表す。この所有権移動は独立した機構ではなく、`ipc_router.md`のRevoke/Grant（vMMIO PTE アンマップ／再マッピング）と完全連動する（`{OwnershipTransfer}`）。

- **決定事項**: `{ADR_MemoryManagerMinimalSurface}` (2026-02-17)
  - **背景**: メモリマネージャのAPIに、確保済みブロックの情報を問い合わせる`query(addr) -> memory-info`と、所有権を確認する`check-ownership(addr, task-id) -> bool`を含めるかどうかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: 両APIを提供し、呼び出し側が任意のアドレスについて情報・所有権を問い合わせられるようにする。汎用的だが、`shared-block`が既に保持している情報を別経路でも問い合わせ可能にする冗長な公開面を作り、`{META_FaultIsolation}`が要求する「所有権はshared-block経由でのみ確認できる」という単一の経路を弱める。
    - 案2: 両APIを削除する。サイズはkernel/task用途では呼び出し側（`allocate`時に記録済み）が、shared用途では`shared_block.get_size()`/`get_owner()`が代替する。
  - **結論**: 案2を採用する。
  - **理由**: `query()`は`allocate`時に呼び出し側がサイズを記録すれば冗長であり、`check-ownership()`は`shared_block.get_owner()`で代替可能かつ、vMMIO側の許可チェック（ソート済みPTEに対する二分探索、`runtime_vmmio.md`正本）と二重の判定経路を作らずに済む。生ポインタを直接やり取りする経路が存在しない設計（すべて`shared_block`リソース経由）とも整合する。

## 8. 参考実装リスト
なし
