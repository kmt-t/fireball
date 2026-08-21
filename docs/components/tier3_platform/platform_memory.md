# COOS メモリマネージャ コンポーネント設計書

## 1. コンセプト
<!-- traceability: {META_3TierSeparation} {GLOBAL_Policy_Memory} {ConsolidatedHeap} {GLOBAL_IndependentHeap} -->
メモリマネージャ（`memory-manager`）は、システム全体の統合物理メモリプール（`ConsolidatedHeap`）を基礎とし、そこからホスト（WASMランタイム）用のヒープ、および各VM/タスク用のヒープを、それぞれ物理的・領域的に完全に独立した別個のヒープ（`GLOBAL_IndependentHeap`）として切り出して管理する。これにより、特定のVMでのメモリ不足が他のVMやホストランタイムを道連れにしてクラッシュすることを防止する。 `{META_3TierSeparation}` `{GLOBAL_Policy_Memory}` `{ConsolidatedHeap}` `{GLOBAL_IndependentHeap}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {WasmPageAlignment} -->
本コンポーネントは **Tier 3 (プラットフォーム / リーフコンポーネント: Leaf Component)** に属し、システム統合物理メモリプールおよび独立ヒープパーティションの静的アロケーション管理を担当する。 `{META_3TierSeparation}` `{WasmPageAlignment}`

## 3. 静的モデル

### 3.1 データ構造
- **`MemoryManager`**: パーティション管理とアロケーションロジックをカプセル化。
- **`partition_info`**: パーティションの境界と使用状況の可視化。

### 3.2 依存関係 (Zero-cost DI)
- `initialize` メソッドにより、管理対象の物理メモリプールの基点アドレスとサイズを受け取る。

## 4. インターフェイス設計
WITインターフェース名は kebab-case で定義されるが、C++の公開APIバインディングにおいては、`fireball` 名前空間の下に `snake_case`（例: `fireball::init_manager`）として実装・公開される。

#### 初期化

<!-- traceability: {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {Size_15KLOC} -->


| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | メモリマネージャを初期化する。 |
| シグネチャ | `init-manager(pool-base: address, pool-size: byte-count) -> operation-result`<br>(C++マッピング: `fireball::init_manager`) |
| 引数 | `pool-base`: 物理メモリプールの基点アドレス<br>`pool-size`: プールのバイトサイズ |
| 戻り値 | 操作結果 |

#### 割り当て（allocate）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 指定されたカテゴリ（kernel/task）からローカルメモリを割り当て、アドレスを返す。 |
| シグネチャ | `allocate(size: byte-count, category: partition-category) -> result<address, recovery-strategy>` |
| 引数 | `size`: 割り当てサイズ<br>`category`: パーティションカテゴリ |
| 戻り値 | 成功時は割り当てられた `address` |
| 補足 | ローカルアクセスは返された `address` を `binary_view` (`std::span`) で直接使用する。`shm_id` は関与しない。 |

#### `allocate-shared` (IPC転送データ専用)
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC転送用の共有メモリブロックを割り当て、RAII所有権を持つリソースを返す。 |
| シグネチャ | `allocate-shared(size: byte-count) -> result<shared-block, recovery-strategy>` |
| 引数 | `size`: 割り当てサイズ |
| 戻り値 | 成功時は `shared-block` リソース |

#### 所有権要求（claim）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC経由で受け取った共有メモリIDから、所有権を持つリソースを取得する。 |
| シグネチャ | `claim(id: shm-id) -> result<shared-block, recovery-strategy>` |
| 引数 | `id`: 共有メモリID |
| 戻り値 | 成功時は `shared-block` リソース |

#### 解放（deallocate）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | `allocate` で確保したローカルメモリを解放する。 |
| シグネチャ | `deallocate(addr: address) -> void` |
| 引数 | `addr`: 解放するメモリアドレス |
| 補足 | 共有メモリは `shared-block` のデストラクタで自動解放される。 |

## 5. 制約と不変条件
<!-- traceability: {GLOBAL_StrictMemoryLimit} {WasmPageAlignment} -->


- `∀m ∈ Allocations : ¬dynamic(m) ∧ is_heap_less(m)`
- `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE` `{GLOBAL_StrictMemoryLimit}`
- `∀block ∈ allocated : block.owner != 0` (task-idと必ず紐付く)
- ゲストRAMに使用する `pool-base` アドレスはWASMページ境界（64KBアライメント）に配置すること。vMMIOおよびインタープリタはこのアライメントを前提として単一比較命令での高速RAMアクセス判定を行う。 `{WasmPageAlignment}`

## 6. 所有権追跡
<!-- traceability: {GLOBAL_Policy_Memory} -->
各メモリブロックは `memory-info.owner` で割り当て元task-idを追跡する。本コンポーネントが提供する `allocate`/`deallocate` や `RAII`/`drop` による解放は、実行時の動的ヒープ確保・解放を意味するものではなく、コンパイル時に固定確保された静的メモリプールから領域を論理的に切り出して貸し出し、使用後にプールへ返却（Placement new およびデストラクタ明示的呼び出しによるバッファ再利用）する「静的パーティショニング」を指す。 `{GLOBAL_Policy_Memory}`

- `allocate` / `allocate-shared` 時に呼び出し元タスクIDが自動設定
- kernel/task: `deallocate` は所有者タスクのみが実行可能
- shared: `shared-memory` リソースのRAII / drop による自動返却（プールへの返却）

## 7. 共有メモリ (shared-block) のライフサイクル
<!-- traceability: {META_FaultIsolation} {OwnershipTransfer} -->
`shared-block` リソースが所有権の単位。IPC転送時に `release` → `claim` で所有権が移動する。 `{META_FaultIsolation}` `{OwnershipTransfer}`

大きなデータを転送する場合、`shm-id` をkv_pairの `value` フィールドに `data-type = handle` で格納し、通常のIPCメッセージとして送信する。

1. タスクAが `allocate-shared(size)` → `shared-block` リソースを取得
2. `shm.get-address()` でローカルアドレスを取得、データを書き込み
3. `shm.release()` → `shm-id` を取得。リソースはA側で無効化
4. `shm-id` を kv_pair (`dtype=handle, key=任意, value=shm-id`) に格納
5. `ipc.send(chan, message(kv_pairs))` で送信
6. タスクBが `ipc.recv(chan)` → kv_pair から `shm-id` を取り出す
7. `claim(shm-id)` → 新 `shared-block` リソースを取得（所有権移動）
8. `shm.get-address()` でデータを読み取り
9. B側の `shared-block` が drop されるとメモリ自動解放

@see `memory.wit` shared-block, `types.wit` kv_pair data-type

## 8. 設計判断の記録

### 8.1 shared-block のリソース化 (2026-02-17)

**判断**: `shm-id` を単なるIDではなく、RAII所有権を持つ `shared-block` リソースとして設計。

**理由**:
- **所有権の明確化**: `release()` で送信側が無効化、`claim()` で受信側が取得。ダングリングポインタを防止
- **自動解放**: デストラクタで自動的にメモリ解放。手動 `deallocate` 不要
- **セマンティクスの明示**: `to-shm/to-address` より `release/claim` の方が所有権移動の意図が明確

### 8.2 query() の削除 (2026-02-17)

**判断**: `query(addr: address) -> memory-info` を削除。

**理由**:
- kernel/task: アプリケーション側が `allocate` 時にサイズを記録すれば十分
- shared: `shared_block.get_size()` で取得可能。冗長

### 8.3 check_ownership() の削除 (2026-02-17)

**判断**: `check-ownership(addr, task-id) -> bool` を削除。

**理由**:
- `shared_block.get_owner()` で所有権確認可能
- vMMIO許可チェックはソート済み `shared_block` リストでの二分検索で実現
- 生ポインタを直接やり取りすることはない（すべて `shared_block` リソース経由）
