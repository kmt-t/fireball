# COOS メモリマネージャ設計書

## 1. コンセプト
メモリマネージャ（`memory-manager`）は、物理メモリプールを複数の論理パーティション（Kernel, Task, Shared等）に分割し、隔離と効率的なメモリ利用を提供する Tier 2 コンポーネントである。 `{3TierSeparation}` `{Policy_Memory}`

## 2. アーキテクチャ分類 (Tier 2: Service Domain)
本コンポーネントは **Tier 2 (サービスドメイン)** に属する。動的メモリ確保を最小限に抑えつつ、固定サイズパーティション内でのアロケーションを管理する。

## 3. 静的モデル

### 3.1 データ構造
- **`MemoryManager` (Class)**: パーティション管理とアロケーションロジックをカプセル化。
- **`partition_info` (View)**: パーティションの境界と使用状況の可視化。

### 3.2 依存関係 (Zero-cost DI)
- `initialize` メソッドにより、管理対象の物理メモリプールの基点アドレスとサイズを受け取る。

## 4. インターフェイス設計 (Stateless Interface)

#### `initialize`
`initialize(pool-base: address, pool-size: byte-count) -> operation-result`

#### `allocate`
`allocate(size: byte-count, kind: partition-kind) -> result<shm-id, recovery-strategy>`

#### `free`
`free(id: shm-id) -> void`

#### `to-address`
`to-address(id: shm-id) -> result<address, bool>`

## 5. 制約と不変条件
- `∀m ∈ Allocations : ¬dynamic(m) ∧ is_heap_less(m)`
- `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE`
