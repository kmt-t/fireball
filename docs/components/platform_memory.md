# COOS メモリマネージャ設計書

## 1. コンセプト
メモリマネージャ（`memory-manager`）は、物理メモリプールを複数の論理パーティション（Kernel, Task, Shared等）に分割し、隔離と効率的なメモリ利用を提供する Tier 2 コンポーネントである。 `{3TierSeparation}` `{Policy_Memory}` `{ConsolidatedHeap}`

## 2. アーキテクチャ分類
本コンポーネントは **Tier 2 (サービスドメイン)** に属する。動的メモリ確保を最小限に抑えつつ、固定サイズパーティション内でのアロケーションを管理する。 `{WasmPageAlignment}`

## 3. 静的モデル

### 3.1 データ構造
- **`MemoryManager`**: パーティション管理とアロケーションロジックをカプセル化。
- **`partition_info`**: パーティションの境界と使用状況の可視化。

### 3.2 依存関係 (Zero-cost DI)
- `initialize` メソッドにより、管理対象の物理メモリプールの基点アドレスとサイズを受け取る。

## 4. インターフェイス設計

#### `initialize`
`initialize(pool-base: address, pool-size: byte-count) -> operation-result`

#### `allocate` (kernel/task専用)
`allocate(size: byte-count, kind: partition-kind) -> result<address, recovery-strategy>`

ローカルアクセスは返された `address` を `binary_view` (`std::span`) で直接使用する。
`shm_id` は一切関与しない。

#### `allocate-shared` (IPC転送データ専用)
`allocate-shared(size: byte-count) -> result<shared-memory, recovery-strategy>`

RAII所有権付きの `shared-memory` リソースを返す。

#### `claim` (IPC受信側)
`claim(id: shm-id) -> result<shared-memory, recovery-strategy>`

#### `deallocate` (kernel/task専用)
`deallocate(addr: address) -> void`

共有メモリは `shared-memory` のデストラクタで自動解放される。

## 5. 制約と不変条件
- `∀m ∈ Allocations : ¬dynamic(m) ∧ is_heap_less(m)`
- `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE` `{StrictMemoryLimit}`
- `∀block ∈ allocated : block.owner != 0` (task-idと必ず紐付く)

## 6. 所有権追跡
各メモリブロックは `memory-info.owner` で割り当て元task-idを追跡する。 `{Policy_Memory}`

- `allocate` / `allocate-shared` 時に呼び出し元タスクIDが自動設定
- kernel/task: `deallocate` は所有者タスクのみが実行可能
- shared: `shared-memory` リソースのRAII / drop で自動解放

## 7. 共有メモリ (shared-memory) のライフサイクル
`shared-memory` リソースが所有権の単位。IPC転送時に `release` → `claim` で所有権が移動する。 `{FaultIsolation}` `{OwnershipTransfer}`

大きなデータを転送する場合、`shm-id` をkv-pairの `value` フィールドに `data-type = handle` で格納し、通常のIPCメッセージとして送信する。

1. タスクAが `allocate-shared(size)` → `shared-memory` リソースを取得
2. `shm.get-address()` でローカルアドレスを取得、データを書き込み
3. `shm.release()` → `shm-id` を取得。リソースはA側で無効化
4. `shm-id` を kv-pair (`dtype=handle, key=任意, value=shm-id`) に格納
5. `ipc.send(chan, message{kv-pairs})` で送信
6. タスクBが `ipc.recv(chan)` → kv-pair から `shm-id` を取り出す
7. `claim(shm-id)` → 新 `shared-memory` リソースを取得（所有権移動）
8. `shm.get-address()` でデータを読み取り
9. B側の `shared-memory` が drop されるとメモリ自動解放

@see `memory.wit` shared-memory, `types.wit` kv-pair data-type

## 8. 設計判断の記録

### 8.1 shared-memory のリソース化 (2026-02-17)

**判断**: `shm-id` を単なるIDではなく、RAII所有権を持つ `shared-memory` リソースとして設計。

**理由**:
- **所有権の明確化**: `release()` で送信側が無効化、`claim()` で受信側が取得。ダングリングポインタを防止
- **自動解放**: デストラクタで自動的にメモリ解放。手動 `deallocate` 不要
- **セマンティクスの明示**: `to-shm/to-address` より `release/claim` の方が所有権移動の意図が明確

### 8.2 query() の削除 (2026-02-17)

**判断**: `query(addr: address) -> memory-info` を削除。

**理由**:
- kernel/task: アプリケーション側が `allocate` 時にサイズを記録すれば十分
- shared: `shared_memory.get_size()` で取得可能。冗長

### 8.3 check_ownership() の削除 (2026-02-17)

**判断**: `check-ownership(addr, task-id) -> bool` を削除。

**理由**:
- `shared_memory.get_owner()` で所有権確認可能
- vMMIO許可チェックはソート済み `shared_memory` リストでの二分検索で実現
- 生ポインタを直接やり取りすることはない（すべて `shared_memory` リソース経由）
