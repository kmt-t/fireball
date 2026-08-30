# COOS メモリマネージャ コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: ../tier3_jit/formal/jit_cache_model.py
     test: tests/platform_memory_test_spec.md
     concept: concepts/platform_memory_concept.py
-->

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

<!-- traceability: {GLOBAL_Policy_Memory} -->
本コンポーネントの公開APIは、[`os_coos.md` §5.2「サブコンポーネント・インターフェイス」](../tier1_core/os_coos.md#52-サブコンポーネントインターフェイス-c23)が定義する `co_mem` インターフェイス契約の物理的な実現である。`os_coos.md` が Tier 1（上位）としてこの契約の正本であり、本節はその具体的な割り当て戦略・データ構造・W^X 連携を詳細化（Refine）するにとどまる。契約自体（メソッド名・引数・戻り値の意味）に食い違いがあれば `os_coos.md` を正とし、本節を修正する。

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
| 機能概要 | タスク固有の静的メモリパーティションを貸与する。**汎用ヒープAPIではない**: `os_coos.md`が明示するとおり、`size_t`指定の任意サイズ確保も`void*`の返却も提供せず、コンパイル時に確定した固定長パーティションのみを貸し出す。 |
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

#### `allocate-shared` (IPC転送データ専用)
<!-- traceability: {OwnershipTransfer} -->
IPC転送のための共有メモリブロック確保は、上記の `acquire-partition`/`acquire-slot` とは別のライフサイクルを持つ。所有権の移動が [`ipc_router.md`](../tier1_interface/ipc_router.md) の Revoke → Enqueue → Grant と、[`runtime_vmmio.md` §4.6](../tier2_runtime/runtime_vmmio.md#46-共有メモリマッピング-fc14)（Tier 2、SHM=FC=14 の PTE `owner_id`/`FLIGHT_SENTINEL`）双方に跨るため、`shared-block` はこの2つの上位仕様が管理する状態を物理メモリ側で保持するRAIIラッパーであり、独自の所有権管理を並行して持つものではない。`release()`/`claim()` の呼び出しは、`ipc_router.md`のRevoke/Grantフェーズおよび対応する vMMIO PTE の `owner_id` 更新と対応する（詳細は §7）。 `{OwnershipTransfer}`

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC転送用の共有メモリブロックを割り当て、RAII所有権を持つリソースを返す。 |
| シグネチャ | `allocate-shared(size: byte-count) -> result<shared-block, recovery-strategy>` |
| 引数 | `size`: 割り当てサイズ |
| 戻り値 | 成功時は `shared-block` リソース |
| 事後条件 | 対応する vMMIO FC=14 ページが `owner_id` = 呼び出し元タスクIDで登録される（`runtime_vmmio.md` `map_shm_page` 相当） |
| 補足 | [`platform_hal.md` §5.1「バッファの確保」](platform_hal.md#51-公開api)が公開する`acquire_buffer(size)`は、本APIの上にHALのデバイス通信用途を薄くラップしたものである（同じTier 3内の兄弟コンポーネント。両者が別々にSHMページを確保することはない）。 |

#### 所有権要求（claim）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC経由で受け取った共有メモリIDから、所有権を持つリソースを取得する。 |
| シグネチャ | `claim(id: shm-id) -> result<shared-block, recovery-strategy>` |
| 引数 | `id`: 共有メモリID（`interface_wit.md` §5.3 の `shm-slice.handle` と同一の `(page_idx << 8) \| slot_idx` 形式） |
| 戻り値 | 成功時は `shared-block` リソース |
| 事前条件 | `ipc_router.md` のGrantフェーズが完了済み（対応するvMMIO PTEの`owner_id`が呼び出し元タスクIDに更新済み）であること |

#### 解放（deallocate）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | `acquire-partition`/`acquire-slot` で確保したローカルメモリを解放する。 |
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
各メモリブロックは `memory-info.owner` で割り当て元task-idを追跡する。本コンポーネントが提供する `acquire-partition`/`acquire-slot`/`deallocate` や `RAII`/`drop` による解放は、実行時の動的ヒープ確保・解放を意味するものではなく、コンパイル時に固定確保された静的メモリプールから領域を論理的に切り出して貸し出し、使用後にプールへ返却（Placement new およびデストラクタ明示的呼び出しによるバッファ再利用）する「静的パーティショニング」を指す。 `{GLOBAL_Policy_Memory}`

- `acquire-partition` / `acquire-slot` / `allocate-shared` 時に呼び出し元タスクIDが自動設定
- kernel/task: `deallocate`（`release-partition`/`release-slot`相当）は所有者タスクのみが実行可能
- shared: `shared-block` リソースのRAII / drop による自動返却（プールへの返却）

## 7. 共有メモリ (shared-block) のライフサイクル
<!-- traceability: {META_FaultIsolation} {OwnershipTransfer} -->
`shared-block` リソースが物理メモリ側での所有権の単位である。ただし所有権の実体（誰が読み書きしてよいか）を最終的に判定するのは、[`runtime_vmmio.md` §1「3層セキュリティゲート」](../tier2_runtime/runtime_vmmio.md#1-コンセプト)のTier 3ゲート（vMMIO FC=14のPTE `owner_id`/`FLIGHT_SENTINEL`）である。`shared-block`の`release()`/`claim()`は、[`ipc_router.md` §4.1「ゼロコピー所有権移譲」](../tier1_interface/ipc_router.md#41-アルゴリズム)のRevoke→Enqueue→Grantと1対1で対応する物理層の操作であり、独立した二重の所有権管理を行うものではない: `release()`はRevokeフェーズで対応するvMMIO PTEの`owner_id`を`FB_TASK_ID_FLIGHT`（移譲中）にし、Grant成立時に`claim()`が呼ばれて`owner_id`を受信タスクへ更新する。 `{META_FaultIsolation}` `{OwnershipTransfer}`

大きなデータを転送する場合、`shm-id` をkv_pairの `value` フィールドに格納し、通常のIPCメッセージとして送信する。kv_pairの型スコープは [`ipc_router.md` §3.3「型スコープのビット構成」](../tier1_interface/ipc_router.md#key-value%E3%83%9A%E3%82%A2kv_pair)が定義する語彙の範囲内で表現する: `shm-id`はハードウェア記述子ではなく物理メモリ側のハンドルであるため、上位3bitは `0b010`（リソース）ではなく `0b000`（機能的、`{IPC_HandleBased}`が定義するハンドル値として解釈）を用い、下位5bitは既定の `0b00001`（`uint32_t`/32bit即値）とする。`ipc_router.md`の型語彙表に`shm-id`専用の型値は存在しないため、新規の型値追加が必要であれば`ipc_router.md`（Tier 1）側の拡張として提案すること。本書側で独自の`dtype=handle`を勝手に定義しない。

1. タスクAが `allocate-shared(size)` → `shared-block` リソースを取得
2. `shm.get-address()` でローカルアドレスを取得、データを書き込み
3. `shm.release()` → `shm-id` を取得。リソースはA側で無効化。対応するvMMIO PTEの`owner_id`が`FB_TASK_ID_FLIGHT`になる（Revoke相当）
4. `shm-id` を kv_pair (`scope=functional, type=u32, key=任意, value=shm-id`) に格納
5. `ipc.send(chan, message(kv_pairs))` で送信。`ipc_router.md`のEnqueueフェーズに対応する（キュー満杯時は`ERR_QUEUE_FULL`でロールバックし、A側の`owner_id`が復元される）
6. タスクBが `ipc.recv(chan)` → kv_pair から `shm-id` を取り出す
7. `claim(shm-id)` → 新 `shared-block` リソースを取得（Grant相当。対応するvMMIO PTEの`owner_id`がB側タスクIDへ更新される）
8. `shm.get-address()` でデータを読み取り
9. B側の `shared-block` が drop されるとメモリ自動解放

@see `../tier1_interface/wit/fireball.wit`

## 8. 設計判断 (ADR)
<!-- traceability: {ADR_SharedBlockRaii} {ADR_MemoryManagerMinimalSurface} -->
このコンポーネントのADRは [architecture_overview.md §8](../../architecture/architecture_overview.md#8-アーキテクチャスタイルと設計判断-adr) の一覧から `{ADR_*}` キーワードで参照される。詳細な背景・選択肢の比較検討は以下に記録する。

- **決定事項**: `{ADR_SharedBlockRaii}` (2026-02-17)
  - **背景**: IPC転送用の共有メモリを、単なる`shm-id`（整数）として扱うか、所有権を持つリソース型として扱うかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: `shm-id`を単なる整数IDとし、明示的な`release_shm(id)`/`acquire_shm(id)`関数で操作する。実装は単純だが、解放忘れやダングリング参照を型システムで防げない。
    - 案2: `shm-id`をRAII所有権を持つ`shared-block`リソースとして設計し、`release()`/`claim()`で所有権移動を明示し、デストラクタで自動解放する。
  - **結論**: 案2を採用する。
  - **理由**: `release()`で送信側が無効化、`claim()`で受信側が取得する設計により、ダングリングポインタを構造的に防止できる。デストラクタでの自動解放により手動`deallocate`忘れも排除できる。`to-shm`/`to-address`のような対称的な変換名より`release`/`claim`の方が所有権移動という意図を明確に表す。この所有権移動は独立した機構ではなく、`ipc_router.md`のRevoke/Grantおよび`runtime_vmmio.md`のPTE `owner_id`更新と対応させる（§7）。

- **決定事項**: `{ADR_MemoryManagerMinimalSurface}` (2026-02-17)
  - **背景**: メモリマネージャのAPIに、確保済みブロックの情報を問い合わせる`query(addr) -> memory-info`と、所有権を確認する`check-ownership(addr, task-id) -> bool`を含めるかどうかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: 両APIを提供し、呼び出し側が任意のアドレスについて情報・所有権を問い合わせられるようにする。汎用的だが、`shared-block`が既に保持している情報を別経路でも問い合わせ可能にする冗長な公開面を作り、`{META_FaultIsolation}`が要求する「所有権はshared-block経由でのみ確認できる」という単一の経路を弱める。
    - 案2: 両APIを削除する。サイズはkernel/task用途では呼び出し側（`allocate`時に記録済み）が、shared用途では`shared_block.get_size()`/`get_owner()`が代替する。
  - **結論**: 案2を採用する。
  - **理由**: `query()`は`allocate`時に呼び出し側がサイズを記録すれば冗長であり、`check-ownership()`は`shared_block.get_owner()`で代替可能かつ、vMMIO側の許可チェック（ソート済みPTEに対する二分探索、`runtime_vmmio.md`正本）と二重の判定経路を作らずに済む。生ポインタを直接やり取りする経路が存在しない設計（すべて`shared_block`リソース経由）とも整合する。

## 9. ハードウェアメモリ保護 (MPU) & W^X 設計

<!-- traceability: {META_FaultIsolation} {WasmPageAlignment} {LowLatencyJIT} {VERIFY_FORMAL} -->

### 9.1 Cortex-M33 PMSAv8 MPU リージョン配分

Cortex-M33 (ARMv8-M Mainline) の PMSAv8 (Protected Memory System Architecture) に準拠し、ハードウェア MPU の 8 リージョン（最小標準構成）を以下のように静的に配分・構成する。 `{META_FaultIsolation}`

| Region # | 対象領域 | 物理メモリ種別 | デフォルト属性 | 特権アクセス | ユーザーアクセス | 役割と保護目的 |
| :---: | :--- | :--- | :---: | :---: | :---: | :--- |
| **0** | Flash / Kernel Code | Flash (ROM) | `RO + X` | RO, Exec | なし | カーネルテキスト・不変定数の改ざん防止 |
| **1** | Kernel Data & BSS | SRAM (Internal) | `RW + XN` | RW, NoExec | なし | カーネル静的変数・スタック領域 |
| **2** | Kernel Pool / Heap | SRAM (Internal) | `RW + XN` | RW, NoExec | なし | タスク管理・IPC 内部制御構造体 |
| **3** | Guest WASM RAM | SRAM (Internal) | `RW + XN` | RW, NoExec | RW, NoExec | ゲスト WASM リニアメモリ（64KB 境界配置） |
| **4** | **JIT Code Cache** | SRAM (Internal) | **`RO + X`** | **RO, Exec** (パッチ時 `RW+XN`) | なし | JIT 生成ネイティブコード（W^X 保護対象） |
| **5** | Peripheral MMIO | Device Memory | `RW + XN` | RW, NoExec | なし | ペリフェラルレジスタ（Device 属性） |
| **6** | Shared Memory Buffers | SRAM (Internal) | `RW + XN` | RW, NoExec | RW, NoExec | IPC ゼロコピー共有バッファ領域 |
| **7** | Stack Guard Band | - | `No Access` | 不可 | 不可 | スタックオーバーフロー検出用ガードバンド |

### 9.2 JIT W^X (Write XOR Execute) 切替プロトコル

JIT コードキャッシュ（Region 4）は、実行可能（Execute）と書き込み可能（Write）が同時に有効化される状態（`RWX`）をハードウェアレベルで恒常的に排除する。 `{LowLatencyJIT}`

#### 属性切替シーケンス
1. **パッチ生成開始 (`begin_jit_patch`)**:
   - `MPU->RNR = 4;` (JIT Cache リージョン選択)
   - `MPU->RLAR &= ~MPU_RLAR_EN_Msk;` (リージョン一時無効化)
   - `MPU->RBAR = (cache_base & MPU_RBAR_BASE_Msk) | MPU_RBAR_AP_RW | MPU_RBAR_XN;` (`RW + XN` 属性設定)
   - `MPU->RLAR |= MPU_RLAR_EN_Msk;` (リージョン有効化)
   - `__DSB(); __ISB();` (メモリ・命令パイプライン同期バリア発行)
2. **Copy-and-Patch 生成**:
   - テンプレートコードのコピーおよび即値リロケーションパッチ書き込み（`RW+XN` のため安全に書き込み可能、実行は禁止）。
3. **パッチ生成完了 (`commit_jit_patch`)**:
   - `MPU->RNR = 4;`
   - `MPU->RLAR &= ~MPU_RLAR_EN_Msk;`
   - `MPU->RBAR = (cache_base & MPU_RBAR_BASE_Msk) | MPU_RBAR_AP_RO;` (`RO + X` 属性復元、`XN=0`)
   - `MPU->RLAR |= MPU_RLAR_EN_Msk;`
   - `__DSB(); __ISB();` (命令キャッシュ・プリフェッチフラッシュ)

#### トランザクションバッチ化によるレイテンシ両立
Copy-and-Patch の各命令パッチごとに個別 MPU 切替を行うとバリアオーバーヘッドが増大するため、JIT コンパイル単位（WASM 関数または基本ブロック単位）で `begin_jit_patch()` と `commit_jit_patch()` を 1 回ずつ発行する**トランザクションバッチ化**を適用する。これにより、属性切替コストをコンパイルあたり 1 回のバリアに抑え、`{LowLatencyJIT}` のリアルタイム制約を達成する。

### 9.3 アライメントおよび境界制約 (PMSAv8)

- **PMSAv8 アライメント**: PMSAv7 と異なり、$2^n$ 乗サイズ境界制約は存在しない。Base アドレス（`RBAR`）および Limit アドレス（`RLAR`）は **32 バイトアライメント**（下位 5 ビットが `0`）を満たせば任意サイズで設定可能。
- **WASM ページ境界**: ゲスト RAM (Region 3) は WASM ページサイズである **64KB アライメント**（`0x10000` 境界）に配置し、vMMIO 高速アドレス判定 (`FastAddressCheck`) と PMSAv8 リージョン境界を完全一致させる。 `{WasmPageAlignment}`

