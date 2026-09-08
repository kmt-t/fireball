# メモリマネージャ 実装（system_allocator / shm_allocator） コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     formal: ../tier3_jit/formal/jit_cache_model.py
     test: tests/runtime_memory_test_spec.md
     concept: concepts/runtime_memory_concept.py
-->

本コンポーネントは、Tier 1 の抽象契約 [`system_memory.md`](docs/components/tier1_core/system_memory.md)（`co_mem` インターフェース、パーティション貸与ポリシー、独立ヒープ不変条件）の物理実装である。契約と実装の記述に食い違いがあれば `system_memory.md` を正とする（`document_structure.md` §2.1-4 契約/実装分割パターン）。

## 1. コンセプト
<!-- traceability: {System_Allocator} {Shm_Allocator} {ConsolidatedHeap} -->
[`system_memory.md`](docs/components/tier1_core/system_memory.md) が定義するパーティション貸与契約を実現するため、固定長パーティション貸与に加え、システム基盤向けシステムコンテナの動的確保・個別解放を担う**システム用アロケータ (`system_allocator` / `{System_Allocator}`)**、およびタスク間ゼロコピー IPC 転送用の共有メモリ領域（MPU Region 6）から可変長バッファを切り出す**SHM用アロケータ (`shm_allocator` / `{Shm_Allocator}`)** を dlmalloc（`create_mspace_with_base`）により運用する。 `{System_Allocator}` `{Shm_Allocator}` `{ConsolidatedHeap}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 2 (分解されたサブコンポーネント: Decomposed Subcomponent)** に属し、Tier 1 の `system_memory.md` 契約を dlmalloc ベースのアロケータ群として物理実装する。 `{META_3TierSeparation}`

## 3. 静的モデル

### 3.1 データ構造
- **`system_allocator`**: dlmalloc（`create_mspace_with_base`）を基盤とする固定長システムヒープアリーナ管理アロケータ。システムコンテナストレージの動的割り当てと個別解放を担当。 `{System_Allocator}`
- **`shm_allocator`**: dlmalloc（`create_mspace_with_base`）を基盤とする共有メモリ（MPU Region 6）アリーナ管理アロケータ。可変長共有ブロック（`shared_block`）の切り出しと RAII 解放時の合体を担当。 `{Shm_Allocator}`

### 3.2 内部ブロック図
```mermaid
graph TD
    Contract["system_memory.md (Tier1 契約: co_mem)"] -.->|realizes| Impl[runtime_memory: MemoryManagerImpl]
    Impl --> SysAlloc[system_allocator: dlmalloc mspace]
    Impl --> ShmAlloc[shm_allocator: dlmalloc mspace]
    SysAlloc --> KernelPool[Kernel Pool / Heap: MPU Region 2]
    ShmAlloc --> SharedRegion[Shared Memory Buffers: MPU Region 6]
```

## 4. インターフェース実装
`system_memory.md` §4 で定義された公開API（`init-manager`, `acquire-partition`/`release-partition`, `acquire-slot`/`release-slot`, `allocate-shared`, `claim`, `deallocate`）を、以下のアロケータ群を用いて実現する。

- `init-manager`: `system_allocator`/`shm_allocator` それぞれに対応する `create_mspace_with_base` を実行する。
- `acquire-partition`/`release-partition`/`acquire-slot`/`release-slot`/`deallocate`: `system_allocator` の mspace 上で有界レイテンシの動的確保・解放を行う。
- `allocate-shared`/`claim`: `shm_allocator` の mspace 上で可変長 `shared_block` を切り出し、RAII 解放時に `mspace_free` へ返却・自動合体する。

## 5. 制約達成の方策
<!-- traceability: {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {WasmPageAlignment} {META_BumpAllocator} {META_FaultIsolation} {OneRuntimeOneGuest} {Runtime_BumpAllocator} {System_Allocator} {Shm_Allocator} -->

### 5.1 性能制約と不変条件
- **目標**: `system_memory.md` が要求する決定論的 $O(1)$ または有界 $O(\log n)$ のメモリ割り当て・解放および高速な境界判定を実現する。
- **方策**:
  - `{META_BumpAllocator}`: 固定長パーティションおよび型付きプールスロットによる断片化なき高速貸与。
  - `{Runtime_BumpAllocator}`: 1ランタイム1ゲスト（`{OneRuntimeOneGuest}`）の実行モデルにおいて、各ランタイムに対して固定長 RAM パーティション（データアリーナ: `RW + XN`）を一括貸与する。ランタイム内部のシステムコンテナストレージ確保はすべてこのアリーナから順次切り出され、アンロード時は個別オブジェクトの破棄なしに $O(1)$ でアリーナ全体が回収・リセットされる。なお、JIT ネイティブコードキャッシュ（3-Bank）は MPU の `W^X`（ライト・実行権限排他）制御が適用された専用の実行可能セクションから専用の JIT コードアロケータによって確保され、データ用バンプアロケータ（RAM/XN）とはハードウェア保護ドメインが厳格に分離される。
  - `{System_Allocator}`: システム層（カーネル、vMMIO、IPC ルータ等）のシステムコンテナストレージ向けに、固定長システムヒープアリーナを dlmalloc（`mspace`）により運用する。システム稼働中の動的な登録・破棄に柔軟対応し、$O(\log n)$ の有界レイテンシで個別解放と断片化の自動合体を提供する。
  - `{Shm_Allocator}`: 共有メモリ領域（MPU Region 6）を固定長アリーナとして dlmalloc（`mspace`）により運用し、可変長 `allocate_shared(size)` 要求に即座に応じる。RAII 解放時の自動合体により、長時間のゼロコピー IPC 通信下でも断片化を最小化する。
  - `{WasmPageAlignment}`: ゲスト RAM（Region 3）を WASM ページサイズである **64KB アライメント**（`0x10000` 境界）に配置し、単一の比較命令による $O(1)$ 高速境界検査（`FastAddressCheck`）と PMSAv8 リージョン境界を完全一致させる。

### 5.2 メモリ制約と方策
<!-- traceability: {GLOBAL_StrictMemoryLimit} {GLOBAL_IndependentHeap} {OneRuntimeOneGuest} {System_Allocator} {Shm_Allocator} -->
- **目標**: 総メモリ消費を有界化し、タスク間のヒープ干渉を完全に防止。
- **方策**:
  - `{GLOBAL_StrictMemoryLimit}`: システム全体の総割当量をコンパイル時定数 `FB_CONF_MEMORY_POOL_SIZE` 以内に厳格制限。
  - `{GLOBAL_IndependentHeap}` `{OneRuntimeOneGuest}`: 各タスク・各ランタイムに独立した静的パーティション（アリーナ）を割り当て、ヒープ干渉を物理的に排除する。共有メモリは 4KB ページ単位で完全に分離する。
  - `{System_Allocator}` `{Shm_Allocator}`: システムヒープおよび共有メモリヒープの容量を `FB_CONF_SYSTEM_HEAP_SIZE` および `FB_CONF_SHM_POOL_SIZE` でコンパイル時静的確定し、無制限な動的拡張（OS からの再確保）を禁止。

### 5.3 安全性制約と方策
<!-- traceability: {META_FaultIsolation} {PageGranularPermissionIsolation} -->
- **目標**: ハードウェア MPU および vMMIO による不正アクセス・二重解放の完全排除。
- **方策**:
  - `{PageGranularPermissionIsolation}`: 4KB 物理ページ単位で所有権と権限を分離し、異種タスクの同一ページ相乗りを禁止。
  - `{META_FaultIsolation}`: 非所有タスクからの操作をトラップで即座に拒絶。

## 6. 物理ページマッピングと共有メモリライフサイクルの物理実装
<!-- traceability: {GLOBAL_Policy_Memory} {META_FaultIsolation} {OwnershipTransfer} {PageGranularPermissionIsolation} {VmmioShmDelegation} -->

`system_memory.md` §6 が定義する契約（所有権追跡・イベント通知インターフェース・ライフサイクルフェーズ）を、以下のとおり物理実装する。

### 6.1 所有権追跡の物理実装
各メモリブロックは `memory-info.owner` で割り当て元 task-id を追跡する。`acquire-partition`/`acquire-slot`/`deallocate` や `RAII`/`drop` による解放は、用途別に事前確保された独立パーティション（固定長アリーナ）から `shm_allocator`/`system_allocator` を用いて有界に切り出し、使用後にアリーナへ返却・合体する。

### 6.2 共有メモリマッピングと仮想化リスナーへのコールバック委譲（物理実装）
<!-- traceability: {VmmioShmDelegation} {OwnerMismatchTrap} -->
物理メモリマネージャは、クリーンアーキテクチャ（依存性逆転の原則: DIP）に従い、特定の上位仮想化ハードウェア（vMMIO 等）の内部シンボルや特定の仮想アドレス体系（`0xE000_0000`）に直接依存しない。物理メモリマネージャは `system_memory.md` §6.2 が定義するイベント通知インターフェース（リスナー機構）を提供し、仮想化層（vMMIO コントローラ等）がこれを購読・登録する。 `{VmmioShmDelegation}`

物理メモリマネージャは物理ページ（4KB）のライフサイクル変化時にこの通知を発火し、仮想化層側が自身の仮想アドレス空間（VPN）に対応するページテーブル（PTE）更新や TLB エントリフラッシュを自律的に実施する。 `{OwnerMismatchTrap}`

### 6.3 ページ単位権限分離仕様（Page-Granular Permission Isolation）
<!-- traceability: {PageGranularPermissionIsolation} {META_FaultIsolation} -->
Cortex-M33 MPU および vMMIO のハードウェア保護機構において、マッピングおよびアクセス権限（読み書き許可ビット）は **4KB 物理ページ（`FB_PAGE_SIZE = 4096`）単位**でのみ設定可能である。
したがって、システム全体のメモリ保護を完全にするため、**「権限（所有タスク ID およびアクセス権限）ごとに物理ページを完全に分離する」** ことを不変条件として強制する。 `{PageGranularPermissionIsolation}`

1. **他タスクとのページ混在の禁止**:
   - 異なるタスクに属する共有メモリスロットを同一 4KB 物理ページ内に共存（相乗り）させることは厳格に禁止される。
   - `allocate_shared(caller_task_id, size)` は、`shm_allocator`（dlmalloc `create_mspace_with_base`）を介して可変長バッファを切り出す際、既に `caller_task_id` が所有し十分な空き容量のある 4KB 物理ページ内の領域から割り当てる。存在しない場合は必ず新規の 4KB 物理ページを `caller_task_id` 専用として払い出し、そのページ境界内でメモリを切り出す。これにより、dlmalloc による可変長アロケーションの柔軟性と、4KB ページ単位の unmap ハードウェア保護（`{PageGranularPermissionIsolation}`）を両立させる。 `{Shm_Allocator}`
2. **ページ単位の所有権移譲**:
   - IPC 転送時、所有権の移譲（Revoke $\to$ Grant）はページ全体を単位として連動する。
   - ページ内の全スロットは常に同一の所有者（または移譲中アンマップ状態）であり、一部のスロットのみが別タスクへ移譲されてページ内で所有者が分裂する状態は生じない。

### 6.4 共有メモリライフサイクルと権限遷移プロトコル（物理実装）
<!-- traceability: {OwnershipTransfer} {META_FaultIsolation} -->
`system_memory.md` §6.3 のライフサイクルフェーズ遷移表に対応する、vMMIO PTE / TLB の具体的な物理挙動を以下に示す。

| ステップ | フェーズ | 送信元(Task A) | 受信先(Task B) | vMMIO PTE & TLB 挙動 |
| :---: | :--- | :--- | :--- | :--- |
| 1 | 確保 | 所有 (`TaskA`) | - | `TaskA` 用にマッピング登録、4KB 専用物理ページ確保 |
| 2 | 書込 | 書込可能 | - | 正常アクセス |
| 3 | 送信開始 | **無効化** (ハンドル返却) | - | **vMMIO アンマップ ＆ TLB 即時フラッシュ** (Revoke) |
| 4 | メッセージ化 | - | - | スコープ: `RESOURCE` |
| 5 | ランデブー | サスペンド待機 | - | 送受信マッチング待ち (Rendezvous) |
| 6 | 認可・受信 | 待機解除 | 受信完了 | 受信タスクへのハンドオフ確約 |
| 7 | 所有権取得 | - | **所有** (`TaskB`) | `TaskB` 用に **マッピング登録** (Grant) |
| 8 | 読出 | - | 読出可能 | 正常アクセス |
| 9 | 自動解放 | - | **解放** | PTE アンマップ、TLB フラッシュ、ページプール返却 |

- **非所有タスク操作の完全遮断 (`MEM-GOTCHA-02`)**: 共有メモリブロックの操作時、ブロックの所有タスク ID を厳格に照合し、非所有タスクからの操作は即座にトラップ（`ShmTrap` / `ERR_PERMISSION_DENIED`）で遮断する。
- **送信中ブロックの保護状態 (`MEM-GOTCHA-03`)**: 送信開始（`release()`）から受信完了（`claim()`）までの間、vMMIO から PTE をアンマップし、TLB を即時フラッシュすることで、送信元タスクからの旧アドレスアクセスを未登録ページフォルト（`TRAP_UNREGISTERED_PAGE`）として確実に遮断し、TOCTOU 競合や不正アクセスを構造的に排除する。
- **障害時回復**: Rendezvous中に通信が中断された場合、`rollback_transfer(original_sender_id, shm_id)` により送信元タスクへ PTE を再マッピングし、リソースのダングリングを防止する。

#### ページ単位権限分離と共有メモリ移譲プロトコル（責務シーケンス図）
<!-- traceability: {PageGranularPermissionIsolation} {OwnershipTransfer} {VmmioShmDelegation} -->
Task A、MemoryManager、vMMIO Controller、Task B 間での専用 4KB 物理ページ切り出しと所有権遷移（A $\to$ アンマップ $\to$ B）の責務分離を示す。

```mermaid
sequenceDiagram
    autonumber
    actor TaskA as Task A (Sender)
    participant Mem as MemoryManager
    participant vMMIO as vMMIO Controller (PTE & TLB)
    actor TaskB as Task B (Receiver)

    TaskA->>Mem: allocate_shared(size)
    Note over Mem: MEM-GOTCHA-01: Page-Granular Isolation
    alt Existing page owned by Task A has sufficient free capacity
        Mem->>Mem: Carve out slot from existing dedicated Physical Page for Task A
    else No suitable existing page
        Mem->>Mem: Allocate fresh dedicated 4KB Physical Page for Task A
        Mem->>vMMIO: Register PTE: VPN -> PPN (mapped for Task A)
    end
    Mem-->>TaskA: Return shared_block (local handle)

    TaskA->>TaskA: Write data into shared buffer
    TaskA->>Mem: release() (Revoke phase)
    Note over Mem,vMMIO: MEM-GOTCHA-03: Unmap Page & Invalidate TLB
    Mem->>vMMIO: Unmap Page: unmap_shm_page(vpn) & Flush TLB
    vMMIO-->>Mem: TLB flushed & PTE removed
    Mem-->>TaskA: Return shm_id (access revoked)

    Note over TaskA,TaskB: IPC Router CSP Rendezvous (Zero-copy handoff shm_id)

    TaskB->>Mem: claim(shm_id) (Grant phase)
    Mem->>vMMIO: Map PTE for Task B: map_shm_page(vpn, ppn)
    vMMIO-->>Mem: Mapping active
    Mem-->>TaskB: Return new shared_block handle
    TaskB->>TaskB: Read data safely (mapped in Task B)

    Note over TaskB,vMMIO: Automatic Release (shared-block RAII drop)
    TaskB->>Mem: shared-block RAII drop
    Mem->>vMMIO: Unmap Page: unmap_shm_page(vpn) & Flush TLB
    vMMIO-->>Mem: TLB flushed & PTE removed
    Mem->>Mem: Return Physical Page to Free Pool
```

## 7. ハードウェアメモリ保護 (MPU) & W^X 設計
<!-- traceability: {META_FaultIsolation} {WasmPageAlignment} {LowLatencyJIT} {System_Allocator} {Shm_Allocator} -->

### 7.1 Cortex-M33 PMSAv8 MPU リージョン配分
Cortex-M33 (ARMv8-M Mainline) の PMSAv8 (Protected Memory System Architecture) に準拠し、ハードウェア MPU の 8 リージョン（最小標準構成）を以下のように静的に配分・構成する。 `{META_FaultIsolation}`

| Region # | 対象領域 | 物理メモリ種別 | デフォルト属性 | 特権アクセス | ユーザーアクセス | 役割と保護目的 |
| :---: | :--- | :--- | :---: | :---: | :---: | :--- |
| **0** | Flash / Kernel Code | Flash (ROM) | `RO + X` | RO, Exec | なし | カーネルテキスト・不変定数の改ざん防止 |
| **1** | Kernel Data & BSS | SRAM (Internal) | `RW + XN` | RW, NoExec | なし | カーネル静的変数・スタック領域 |
| **2** | Kernel Pool / Heap | SRAM (Internal) | `RW + XN` | RW, NoExec | なし | `system_allocator`（dlmalloc）によるシステムコンテナおよびタスク管理構造体の動的確保 `{System_Allocator}` |
| **3** | Guest WASM RAM | SRAM (Internal) | `RW + XN` | RW, NoExec | RW, NoExec | ゲスト WASM リニアメモリ（64KB 境界配置） |
| **4** | **JIT Code Cache** | SRAM (Internal) | **`RO + X`** | **RO, Exec** (パッチ時 `RW+XN`) | なし | JIT 生成ネイティブコード（W^X 保護対象。詳細管理は [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md) / [`jit_runtime.md`](docs/components/tier3_jit/jit_runtime.md) を正本とする） |
| **5** | Peripheral MMIO | Device Memory | `RW + XN` | RW, NoExec | なし | ペリフェラルレジスタ（Device 属性） |
| **6** | Shared Memory Buffers | SRAM (Internal) | `RW + XN` | RW, NoExec | RW, NoExec | `shm_allocator`（dlmalloc）による可変長 shared_block バッファ管理（4KBページ分離） `{Shm_Allocator}` |
| **7** | Stack Guard Band | - | `No Access` | 不可 | 不可 | スタックオーバーフロー検出用ガードバンド |

### 7.2 JIT W^X (Write XOR Execute) 切替プロトコル
JIT コードキャッシュ（Region 4）は、実行可能（Execute）と書き込み可能（Write）が同時に有効化される状態（`RWX`）をハードウェアレベルで恒常的に排除する。本領域は、データ用バンプアロケータ（`{Runtime_BumpAllocator}`）が管理する通常のデータ RAM パーティション（Region 3: `RW + XN`）とはハードウェア保護ドメインが厳格に分離された専用セクションであり、専用の JIT コードアロケータによって管理される。 `{LowLatencyJIT}` `{Runtime_BumpAllocator}`

#### 属性切替シーケンス
JIT コンパイル開始から完了までの属性切替ステップを示す。ハードウェアレジスタ操作と目的を構造化して定義する。

| ステップ | 操作名 | レジスタ設定内容 | 属性 / バリア | 目的と安全性不変条件 |
| :---: | :--- | :--- | :--- | :--- |
| 1 | パッチ生成開始 (`begin_jit_patch`) | `RNR = 4`<br>`RLAR.EN = 0`<br>`RBAR.AP = RW`, `RBAR.XN = 1`<br>`RLAR.EN = 1` | `RW + XN`<br>`__DSB(); __ISB();` | リージョン4を書き込み可能・実行不可へ移行し、パイプラインを同期。実行を禁止して改ざん時暴走を防止。 |
| 2 | Copy-and-Patch 生成 | テンプレートコピー & 即値パッチ | `RW + XN` | 生成中は安全にメモリ書き込みのみを行う。 |
| 3 | パッチ生成完了 (`commit_jit_patch`) | `RNR = 4`<br>`RLAR.EN = 0`<br>`RBAR.AP = RO`, `RBAR.XN = 0`<br>`RLAR.EN = 1` | `RO + X`<br>`__DSB(); __ISB();` | リージョン4を読み取り専用・実行可能へ復元し、命令キャッシュ・プリフェッチをフラッシュしてネイティブ実行を有効化。 |

#### トランザクションバッチ化によるレイテンシ両立
Copy-and-Patch の各命令パッチごとに個別 MPU 切替を行うとバリアオーバーヘッドが増大するため、JIT コンパイル単位（WASM 関数または基本ブロック単位）で `begin_jit_patch()` と `commit_jit_patch()` を 1 回ずつ発行する**トランザクションバッチ化**を適用する。これにより、属性切替コストをコンパイルあたり 1 回のバリアに抑え、`{LowLatencyJIT}` のリアルタイム制約を達成する。

##### JIT W^X バッチ切り替えトランザクション手順（手順アクティビティ図）
<!-- traceability: {MEM-GOTCHA-04} {GLOBAL_Policy_Memory} {META_RestrictedPhysicalAccess} -->
JIT コンパイル時の MPU 属性切り替え（RW+XN $\to$ RO+X）と CPU キャッシュコヒーレンシバリア発行の決定論的手順を示す。

```mermaid
flowchart TD
    Start(["JIT Compiler: Begin Trace Generation"]) --> MPU_RW["MPU: Switch Active Bank to RW+XN (Writeable, Execute-Never)"]
    MPU_RW --> BarrierRW["Issue DSB (Data Synchronization) & ISB (Instruction Synchronization)"]
    BarrierRW --> CopyPatch["Copy Stencil Machine Code & Apply Immediate/Register Patches"]
    CopyPatch --> Complete{"Trace generation complete?"}

    Complete -- "Yes" --> CleanD["ARM CMSIS: SCB_CleanDCache_by_Addr(trace_addr, size)"]
    CleanD --> InvalI["ARM CMSIS: SCB_InvalidateICache_by_Addr(trace_addr, size)"]
    InvalI --> Barrier["Issue DSB (Data Synchronization) & ISB (Instruction Synchronization)"]
    Barrier --> MPU_RO["MPU: Switch Active Bank to RO+X (Read-Only, Executable)"]
    MPU_RO --> CommitTrace(["Trace Committed: Safe Native Execution Enabled"])
```

- **バッチ化トランザクション (`MEM-GOTCHA-04`)**:
  **設計理由と不変条件**: 1 命令の書き込みごとに MPU 属性の切り替え（実行不可・書き込み可 $\to$ 書き込み不可・実行可）を行うと、その都度 ARM D-Cache クリーン、I-Cache インバリデート、および DSB/ISB メモリバリア命令を発行する必要があり、パイプラインフラッシュの累積により JIT コンパイル性能が致命的に悪化する。そのため、W^X 切り替えは必ず「1 トレースまたは 1 バッチ」単位でトランザクション化し、トレース全体の生成完了後に一括してキャッシュクリーンとバリアを発行して実行可能属性へ遷移させる。

### 7.3 アライメントおよび境界制約 (PMSAv8)
- **PMSAv8 アライメント**: PMSAv7 と異なり、$2^n$ 乗サイズ境界制約は存在しない。Base アドレス（`RBAR`）および Limit アドレス（`RLAR`）は **32 バイトアライメント**（下位 5 ビットが `0`）を満たせば任意サイズで設定可能。
- **WASM ページ境界**: ゲスト RAM (Region 3) は WASM ページサイズである **64KB アライメント**（`0x10000` 境界）に配置し、vMMIO 高速アドレス判定 (`FastAddressCheck`) と PMSAv8 リージョン境界を完全一致させる。 `{WasmPageAlignment}`

## 8. 形式検証・テスト仕様との対応

### 8.1 検証対象の不変条件
- **ページ単位権限分離**: 4KB 物理ページ内に異種タスクのスロットが共存しないこと（`MEM-14`, `MEM-GOTCHA-01`）。
- **非所有者アクセストラップ**: 所有権未取得（未マッピング）スロットへのアクセスが `TRAP_UNREGISTERED_PAGE` で拒絶されること（`MEM-16`, `MEM-GOTCHA-02`）。
- **W^X 不変条件**: JIT キャッシュ領域で `RWX` が同時に許可される状態が存在しないこと（[`jit_cache_model.py`](docs/components/tier3_jit/formal/jit_cache_model.py), `MEM-23`）。

### 8.2 テスト仕様書との連携
本コンポーネントのテストケース（MEM-01〜MEM-25, MEM-GOTCHA-01〜04）は、[`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) を正本として定義する。

## 9. 設計判断 (ADR)
<!-- traceability: {ADR_PageGranularPermissionIsolation} -->
このコンポーネントのADRは `{ADR_PageGranularPermissionIsolation}` のキーワードで参照される。公開API設計に関する `{ADR_SharedBlockRaii}` / `{ADR_MemoryManagerMinimalSurface}` は契約側 [`system_memory.md`](docs/components/tier1_core/system_memory.md) を正本とする。

- **決定事項**: `{ADR_PageGranularPermissionIsolation}` (2026-09-02)
  - **背景**: vMMIO FC=14 の PTE および MPU は 4KB ページ単位でしかマッピング・権限（RW許可）を設定できない。同一ページ内に異なるタスクのスロットが混在すると、タスク間のメモリ隔離が破綻し、他タスクのデータが読み書きされる危険があった。
  - **選択肢と評価**:
    - 案1: 単一ページ内に複数タスクのスロットを混在させ、メモリアクセス時にソフトウェアでスロット境界とタスクIDを毎回検査する。チェックのオーバーヘッドが大きく、vMMIO のハードウェア PTE / TLB 高速ディスパッチの恩恵を損なう。
    - 案2: 権限（所有タスクID）ごとに独立した 4KB 物理ページを割り当て、同一ページ内には同一所有者のスロットのみを配置する。メモリ消費はページ単位に量子化されるが、ページ単位のマッピング有無による教科書的な仮想記憶保護が完全に成立し、ゼロコストでタスク間隔離が担保される。
  - **結論**: 案2を採用する。
  - **理由**: Fireball の最重要方針である `{META_FaultIsolation}`（障害隔離）および `{META_ZeroCostAbstraction}` を実現するため。PTE に余計な `owner_id` フィールドを持たせず、マッピング有無による未登録ページ遮断としてアクセスパスを最速に保つ。

## 10. 参考実装リスト
なし
