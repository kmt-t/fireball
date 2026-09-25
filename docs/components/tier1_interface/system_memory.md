# メモリマネージャ 抽象契約 コンポーネント設計書 {VERIFY_FORMAL} {VERIFY_LLM}
<!-- evidence:
     contract-only: true
     formal: formal/system_memory_model.py
     test: docs/qa/tier1_interface/system_memory_test_spec.md
     wit: wit/system_memory_contract.wit
-->

## 1. コンセプト
<!-- traceability: {ConsolidatedHeap} {GLOBAL_IndependentHeap} {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {META_3TierSeparation} {OneRuntimeOneGuest} -->
メモリマネージャ（`memory-manager`）は、システム全体の統合物理メモリプール（`ConsolidatedHeap`）を基礎とする。5つの独立した静的アロケーションプールを貸与する抽象契約（`co_mem`）を定義する。各プールは用途、ライフサイクル、アロケータ方式が異なる。プール間は物理的にも領域的にも独立している。特定プールのメモリ不足が他のプールへ波及することを防ぐ。

本コンポーネントが定義する5つのプールは以下の通りである。それぞれの詳細は「4. インターフェース設計」を参照。

1. **ホスト用ヒープ**（`host-heap`）: システムコンテナ（IPCレジストリ、カーネルプール等）用の動的確保・個別解放ヒープ。
2. **タスクヒープ**（`task-heap`）: COOS がタスクを起動する際に貸与する、タスク固有の固定長パーティション。
3. **共有メモリ用ヒープ**（`shared-memory-heap`）: IPC 転送専用の RAII 所有権付き可変長バッファプール。
4. **ランタイム用バンプアロケータ**（`runtime-bump-allocator`）: ランタイム単位（`OneRuntimeOneGuest`）に専有される、モジュールロード用の一括確保・一括解放アリーナ。
5. **JITキャッシュアロケータ**（`jit-cache-allocator`）: JIT コード生成専用に予約された固定長リージョンの貸与。

本コンポーネントは「何を提供するか（契約）」のみを定義する。5プールそれぞれの具体的なアロケータ実装（`system_allocator`、`shm_allocator`、`bump_allocator` 等）は、Tier 2 の [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を正本とする（`{META_ContractImplSplit}`「契約/実装分割パターン」）。**本契約は上位仮想化層（vMMIO 等）の内部シンボル・アドレス体系・PTE/TLB 機構を一切参照しない**——それらは Tier 2 の物理実装が専有する関心事であり、本コンポーネントは 5 プールの貸与・返却・所有権移譲という抽象操作のみを規定する。

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 1 Interface (独立インターフェース契約: Independent Interface Contract)** に属し、システム全体の5プール貸与ポリシーおよび独立ヒープ不変条件を定義する抽象契約を担当する。実装（`system_allocator`/`shm_allocator`/`bump_allocator`等）は Tier 2 の [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) が担う。

## 3. 静的モデル

### 3.1 データ構造
- **`MemoryManager`**: 5プールそれぞれの管理ロジックの契約をカプセル化する抽象インターフェース。具体的なアロケータ実装は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を参照。
- **`partition-slice`**: タスクヒープ貸与単位の境界と使用状況の可視化（非所有ビュー）。
- **`runtime-arena`**: ランタイム用バンプアロケータの貸与単位（1ランタイムにつき1アリーナ）。
- **`jit-cache-region`**: JITキャッシュアロケータの貸与単位（固定長、単一リージョン）。

### 3.2 依存関係 (Zero-cost DI)
- `initialize` メソッドにより、管理対象の物理メモリプールの基点アドレスとサイズを受け取る。実装側は `runtime_memory.md` の各アロケータによりこれを具体化する。

## 4. インターフェース設計

<!-- traceability: {GLOBAL_Policy_Memory} -->
本コンポーネントの公開APIは `{META_StaticDI}` が定義する `co_mem` インターフェース契約そのものである。実装側（`runtime_memory.md`）は本契約をそのまま実現し、契約自体（メソッド名・引数・戻り値の意味）に食い違いがあれば本節を正とする。

WIT インターフェース名は kebab-case で定義する。C++ の公開 API バインディングは、`fireball` 名前空間の下で `snake_case` として実装・公開する（例: `fireball::co_mem::host_alloc`）。

各シグネチャは [`system_memory_contract.wit`](docs/components/tier1_interface/wit/system_memory_contract.wit) の `interface memory` を正本として引用する。プールの基点アドレスとサイズの静的構成（5.2節）は、`system_config.md` の `FB_CONF_*` 定数群を正本とする。本契約は実行時初期化 API を持たない。製品ソースコード規模を抑える方針に基づき、起動時の静的構成だけで要件を満たすためである。

### 4.1 ホスト用ヒープ（`host-heap`）
<!-- traceability: {GLOBAL_Policy_Memory} {System_Allocator} -->
システムコンテナ（IPCレジストリ、カーネルプール、サブシステムバッファ等）が必要とする、動的サイズ・個別解放可能な確保領域。**汎用アプリケーションヒープではない**: システム内部の静的コンテナ実装専用であり、ゲストやタスクの動的メモリ確保用途には使用されない。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ホスト用ヒープから任意サイズのブロックを確保する。 |
| シグネチャ | `host-alloc(size: byte-count) -> result<address, memory-error>`<br>(C++マッピング: `fireball::co_mem::host_alloc`) |
| 引数 | `size`: 確保するバイト数 |
| 戻り値 | 成功時は確保されたブロックの先頭アドレス。失敗時は `memory-error` |

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | `host-alloc` で確保したブロックを個別に解放する。 |
| シグネチャ | `host-free(addr: address) -> result<_, memory-error>`<br>(C++マッピング: `fireball::co_mem::host_free`) |
| 引数 | `addr`: 解放するブロックのアドレス |
| 戻り値 | 成功時は空。不正アドレス・二重解放時は `memory-error` |

### 4.2 タスクヒープ（`task-heap`）
<!-- traceability: {GLOBAL_Policy_Memory} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | COOS がタスクを起動する際に、タスク固有の静的メモリパーティションを貸与する。**汎用ヒープAPIではない**: `{CooperativeMultitasking}`が明示するとおり、サイズ引数を取る動的確保も汎用ポインタの返却も提供せず、コンパイル時に確定した固定長パーティションのみを貸し出す。各タスクへの貸与サイズはタスク種別・搭載予定モジュール規模に応じて `system_config.md` の `FB_CONF_TASK_HEAP_SIZES` ROM配列でスロットごとに個別設定される（呼び出し元がサイズを指定するのではなく、あくまでコンパイル時に確定した値を参照するのみ）。 |
| シグネチャ | `acquire-task-heap() -> result<partition-slice, memory-error>`<br>(C++マッピング: `fireball::co_mem::acquire_task_heap`) |
| 引数 | なし。所有者はスケジューラの実行中タスクから決定される |
| 戻り値 | 成功時は `partition-slice`（`system_memory_contract.wit` `types.partition-slice`。基点アドレス・サイズ・所有タスクを持つ非所有ビュー相当のレコード）。失敗時は `memory-error` |
| 事前条件 | COOS のタスク起動シーケンス内から呼び出されること |
| 事後条件 | 返却された `partition-slice` の範囲は他タスクのパーティションと重複しない |

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | タスク終了時に、`acquire-task-heap` で貸与されたパーティションを返却する。 |
| シグネチャ | `release-task-heap() -> result<_, memory-error>`<br>(C++マッピング: `fireball::co_mem::release_task_heap`) |
| 引数 | なし。返却元タスクはスケジューラの実行中タスクから決定される |
| 戻り値 | 成功時は空。所有者以外からの呼び出しは `memory-error`（`invalid-owner`） |
| 不変条件 | 所有者以外からの呼び出しは無効（返却されない） |

#### 型付きプールスロットの貸与・返却（`acquire-slot` / `release-slot`）
<!-- traceability: {CooperativeMultitasking} {GLOBAL_UseCpp20Coroutine} -->
この契約は、タスクヒープと同様に固定長・静的確保のみを行う。貸与単位は型付きスロットである。**この API は `system_memory_contract.wit` には現れない**。WIT はジェネリクスを表現できないため、`pool-ref<T>` はコンポーネント境界を越えない C++ 側の型付きラッパーとして提供する。WIT 側は `acquire-task-heap` の汎用パーティションを型なしで貸与し、型安全性は C++ テンプレートが担う。

**主要な用途の一つ**は、COOS タスクの C++20 コルーチンフレーム確保である。`promise_type::operator new` / `operator delete` は本 API を介して、カーネルプール（`FB_CONF_KERNEL_HEAP_SIZE`）内の静的スロットからフレームを確保する。ホスト用ヒープの `malloc` / `new` を使わずにコルーチンを起動する。詳細は [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md) の `spawn_task` を正本とする。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 静的プール内の型付きスロットを貸与・返却する。`partition-slice`同様、動的サイズ指定は行わない。 |
| シグネチャ | `acquire_slot<T>() -> result<pool_ref<T>, memory_error>`<br>`release_slot<T>(ref: pool_ref<T>) -> void`<br>(C++専用API、WIT対応なし) |
| 戻り値 | 成功時は `pool_ref<T>`（静的プール内スロットへの型付きハンドル） |

### 4.3 共有メモリ用ヒープ（`shared-memory-heap`）
<!-- traceability: {ADR_SharedBlockRaii} {OwnershipTransfer} {Shm_Allocator} -->
共有メモリブロックの確保は、上記のタスクヒープ/型付きスロットとは別のライフサイクルを持つ。実装側（`runtime_memory.md`）の `shm_allocator` を介して、指定されたバイト数（`size`）の可変長バッファを切り出す。WITの`shm-handle`はABI境界で受け渡す通常のレコードであり、それ自体は線形・move-only所有権を表現しない。C++側の`shared_block`はこのレコードを包むmove-only RAII所有ラッパーであり、レコードを複製しても所有権やアクセス権は複製されない。`release()`の成功時はラッパーを移譲済みとして無効化し、`claim()`の成功時は新しい所有タスク側のラッパーを返す。所有ラッパーのdropは`release(handle)`を呼び出してプールへ返す。所有権移譲の物理的な執行手段はTier 2の実装詳細であり、本契約はそれを規定しない。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | IPC転送用の共有メモリブロックを割り当てる。4KBのFC=14仮想予約スロットは物理SHM容量とは独立し、物理バック領域は指定サイズ分だけ消費する。 |
| シグネチャ | `allocate-shared(size-bytes: byte-count) -> result<shm-handle, memory-error>` |
| 引数 | `size-bytes`: 割り当てサイズ |
| 戻り値 | 成功時は `shm-handle`（`system_memory_contract.wit` `types.shm-handle`。ハンドル値・物理バック領域の基点アドレス・サイズ・所有タスクを持つレコード）。FC=14のゲスト仮想アドレスはハンドルの予約ページ番号から別途求める。C++ 実装はこれを RAII 所有権付きの `shared_block` ラッパーで包み、デストラクタでの自動解放を保証する（`ADR_SharedBlockRaii`）。失敗時は `memory-error` |
| 補足 | なお、HAL デバイス通信用のバッファは本共有メモリとは直交し、HAL 自身が管轄する固定長の HALバッファプールから切り出される。 |

#### 所有権要求（claim）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 移譲済み共有メモリハンドルから、実行中タスクの所有権を確立する。 |
| シグネチャ | `claim(handle: address) -> result<shm-handle, memory-error>` |
| 引数 | `handle`: 共有メモリハンドル値（`{Syscall_Mapping}` / `system_memory_contract.wit` の `shm-handle.handle` と同一の `(page_idx << 8) \| slot_idx` 形式。慣用的に `shm-id` とも呼ぶ） |
| 戻り値 | 成功時は `shm-handle`（C++ 実装は `shared_block` ラッパーとして返す） |
| 事前条件 | 対象ハンドルが移譲済みで、実行中タスクへの所有権確立が許可されていること |

#### 解放（release）
| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 共有メモリブロックを解放しプールへ返却する。 |
| シグネチャ | `release(handle: address) -> result<_, memory-error>` |
| 引数 | `handle`: 解放するブロックのハンドル値 |
| 戻り値 | 成功時は空。所有者以外からの呼び出しは `memory-error`（`invalid-owner`） |
| 補足 | 通常は `shared-block` のデストラクタ（RAII drop）を通じて暗黙に呼び出され、明示呼び出しを必要としない。 |

### 4.4 ランタイム用バンプアロケータ（`runtime-bump-allocator`）
<!-- traceability: {Runtime_BumpAllocator} {OneRuntimeOneGuest} -->
1ランタイム1ゲストの直交分離原則に従い、各ランタイムは専用のバンプアロケータアリーナを所有する。アリーナはランタイムごとに独立する。WASM モジュールのロード時に使うシステムコンテナストレージ（`module_view`、シンボルテーブル等）は、このアリーナから一括確保する。ランタイムまたはモジュールのアンロード時は、個別解放を行わず $O(1)$ で決定論的に一括解放する。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ランタイムインスタンスに専用のバンプアロケータアリーナを貸与する。 |
| シグネチャ | `acquire-runtime-arena(runtime: runtime-id) -> result<runtime-arena, memory-error>`<br>(C++マッピング: `fireball::co_mem::acquire_runtime_arena`) |
| 引数 | `runtime`: 貸与先ランタイムID |
| 戻り値 | 成功時は `runtime-arena` ハンドル |

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | アリーナ内で固定長データを一括確保方向へ切り出す。 |
| シグネチャ | `bump-alloc(arena: runtime-arena, size: byte-count) -> result<address, memory-error>`<br>(C++マッピング: `fireball::co_mem::bump_alloc`) |
| 引数 | `arena`: 対象アリーナ<br>`size`: 確保サイズ |
| 戻り値 | 成功時は確保領域の先頭アドレス |

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | アリーナ内の全確保を個別解放なしで一括リセットする（モジュール単位のアンロード等）。 |
| シグネチャ | `reset-runtime-arena(arena: runtime-arena) -> void`<br>(C++マッピング: `fireball::co_mem::reset_runtime_arena`) |
| 計算量 | $O(1)$（バンプポインタをアリーナ先頭へ巻き戻すのみ） |

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | ランタイム終了時に、アリーナ全体をプールへ返却する。 |
| シグネチャ | `release-runtime-arena(arena: runtime-arena) -> void`<br>(C++マッピング: `fireball::co_mem::release_runtime_arena`) |

### 4.5 JITキャッシュアロケータ（`jit-cache-allocator`）
<!-- traceability: {JIT_MultiBuffer_Cache} {GLOBAL_StrictMemoryLimit} -->
JITコード生成専用に予約された連続固定長リージョン（`FB_CONF_JIT_CACHE_SIZE`=8,192B、4KB物理ページ2枚）の貸与を契約する。領域の固定区画は共通コード2KBとActive/Warm/Oldest各2KBとし、`system_config.md`を正本とする。JITサブシステム（`jit_runtime.md`）は3可変バンクの世代交代・昇格を担当し、共通コード領域をエビクションしない。

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | JIT コード生成用の固定長リージョンを貸与する。 |
| シグネチャ | `acquire-jit-cache() -> result<jit-cache-region, memory-error>`<br>(C++マッピング: `fireball::co_mem::acquire_jit_cache`) |
| 戻り値 | 成功時は `jit-cache-region`（連続した固定長 `FB_CONF_JIT_CACHE_SIZE`=8,192バイト、物理ページ2枚の実行可能メモリ領域ハンドル） |
| 事前条件 | システム起動時に1度のみ呼び出される（動的な複数リージョン確保は想定しない） |
| 補足 | W^X（書込み時 RW+XN / 実行時 RO+X）属性切り替えの実行方式は Tier 2 の実装詳細（`runtime_memory.md`）を正本とする。 |

## 5. 制約達成の方策
<!-- traceability: {GLOBAL_Policy_Memory} {GLOBAL_StrictMemoryLimit} {GLOBAL_IndependentHeap} -->

### 5.1 性能制約と不変条件
- **目標**: 決定論的 $O(1)$ または有界 $O(\log n)$ のメモリ割り当て・解放。
- **方策**: 契約では、5プールそれぞれの貸与単位だけを規定する。単位は動的ブロック、固定長パーティションまたは型付きスロット、可変長共有ブロック、バンプアリーナ、固定長 JIT リージョンである。
  計算量保証を実現する具体的な方式（dlmalloc アリーナ、バンプアロケータ等）は、[`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) を正本とする。型付きスロットは「タスクヒープ」プール内の貸与単位の一種である。これは型安全な C++ 専用ラッパーであり、独立した6番目のプールではない。

### 5.2 メモリ制約と方策
<!-- traceability: {GLOBAL_StrictMemoryLimit} {GLOBAL_IndependentHeap} -->
- **目標**: 総メモリ消費を有界化し、5プール間の相互干渉を完全に防止。
- **方策**:
  - : システム全体の総割当量をコンパイル時定数（`FB_CONF_MEMORY_POOL_SIZE`、定義は `system_config.md` 正本）以内に厳格制限する契約とする。
  - : 5プールそれぞれに独立した静的領域を割り当て、プール間干渉を物理的に排除する契約とする。共有メモリ用ヒープの排他制御・境界検査の物理実装は `runtime_memory.md` を正本とする。

### 5.3 安全性制約と方策
- **目標**: 不正アクセス・二重解放の完全排除。
- **方策**: 所有者以外からの操作を無効とする契約を課す。ハードウェア（MPU等）による具体的な執行方式は `runtime_memory.md` を正本とする。

## 6. 所有権追跡と共有メモリライフサイクル
<!-- traceability: {GLOBAL_Policy_Memory} {META_FaultIsolation} {OwnershipTransfer} -->

### 6.1 所有権追跡仕様
各共有メモリブロックは `memory-info.owner` で割り当て元の task-id を追跡する。本コンポーネントは `acquire-task-heap` / `acquire-slot` / `release-task-heap` / `release-slot` を提供する。`RAII` / `drop` による解放は、システム全体の無秩序なヒープ確保を意味しない。用途別に事前確保した独立プールから有界に切り出し、使用後に返却・合体する契約である。具体的なアロケータ（`shm_allocator`、`system_allocator` 等）は `runtime_memory.md` を正本とする。

- **自動設定**: `acquire-task-heap` / `acquire-slot` / `allocate-shared` 時に、スケジューラが認証した実行中タスクIDを所有者として自動設定する。呼び出し側が task-id を申告する引数は持たない。
- **所有者限定操作**: `release-task-heap`/`release-slot` は所有者タスクのみが実行可能。
- **RAII自動返却**: `shared-block` リソースの RAII / drop によるプールへの自動返却（`release()` の暗黙呼び出し）。

### 6.2 共有メモリライフサイクルと所有権遷移（契約レベル）
<!-- traceability: {OwnershipTransfer} {META_FaultIsolation} -->
`shared-block` リソースが共有メモリ用ヒープにおける所有権の単位である。`shared-block` の `release()` は現在の所有者を無効化して移譲可能状態にし、`claim()` は許可された実行中タスクへ所有権を確立する。アクセス可否の物理的な執行手段は Tier 2 の実装詳細（`runtime_memory.md`）を正本とし、本契約はそれを規定しない。

##### ライフサイクルフェーズ遷移表
| ステップ | フェーズ | 実行API / イベント | 送信元(Task A) | 受信先(Task B) |
| :---: | :--- | :--- | :--- | :--- |
| 1 | 確保 | `allocate_shared(size)` | 所有 (`TaskA`) | - |
| 2 | 使用 | `shared-block` の読み書き | 読書き可能 | - |
| 3 | 移譲開始 | `release()` | **無効化** (ハンドル返却) | - |
| 4 | 所有権確立 | `claim(handle)` | - | **所有** (`TaskB`) |
| 5 | 使用 | `shared-block` の読み書き | - | 読書き可能 |
| 6 | 自動解放 | `shared-block` の RAII drop | - | **解放** |

物理層での各フェーズの具体的な執行内容は [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md) の共有メモリライフサイクルと権限遷移プロトコル（物理実装）節を正本とする。

- **非所有タスク操作の完全遮断 (`GOTCHA-MEM-02`)**: 共有メモリブロックの操作時、ブロックの所有タスク ID を厳格に照合し、非所有タスクからの操作は拒否される。具体的な検出・遮断機構は Tier 2 の実装詳細（`runtime_memory.md`）を正本とし、本契約はそれを規定しない。
- **障害時回復**: 所有権移譲が完了しない場合、`rollback_transfer(handle)` を現在実行中の送信タスクから呼び出して元の所有者へ戻し、リソースのダングリングを防止する。

### 6.3 共有ページマッピング・所有権変更通知フック
<!-- traceability: {OwnershipTransfer} {META_FaultIsolation} -->
Tier 1 は、共有メモリ予約の外部マッピング管理者がライフサイクルを監視するための汎用コールバック口 `PageMappingCallbacks` を提供する。`page_idx` は4KB単位の仮想予約スロット番号であり、物理アドレスや物理ページ番号ではない。これは vMMIO、PTE、TLB を契約へ持ち込むものではなく、予約と物理バック領域の対応・所有権状態を通知する依存性逆転用ポートである。

| コールバック | シグネチャ | 発火条件 | 契約上の意味 |
| :--- | :--- | :--- | :--- |
| `on_map_page` | `(page_idx, physical_base_addr, owner_id, mapped_size_bytes)` | 確保、`claim`、ロールバックで有効な所有ビューを作るとき | 外部管理者が4KB仮想予約を物理基点へ対応付け、要求サイズを超えるアクセスを拒否する |
| `on_owner_changed` | `(page_idx, physical_addr, previous_owner_id, new_owner_id)` | 所有者が変わるとき（`release` による `IN_FLIGHT` 遷移を含む） | 旧ビューを先に無効化する。所有者変更だけでは再マップしない |
| `on_unmap_page` | `(page_idx, physical_addr)` | 共有ページをプールへ返却するとき | 外部管理者がページのビューを破棄する |

`on_owner_changed` はメモリマネージャが所有者台帳を更新した後に一度だけ発火し、登録側は旧マッピングをアンマップする。新しい所有ビューは、所有権確立後の `on_map_page`（`claim` または `rollback_transfer`）で明示的に再登録する。コールバックの呼び出し側が task-id を引数で自己申告することはない。

## 7. 設計判断 (ADR)
<!-- traceability: {ADR_MemoryManagerMinimalSurface} {ADR_SharedBlockRaii} -->
この節では共有メモリ契約の公開面と所有権規則を定める。ページテーブルとTLBの物理操作は本契約に含めない。

- **決定事項**: (2026-02-17)
  - **背景**: IPC転送用の共有メモリを、単なる`shm-id`（整数）として扱うか、所有権を持つリソース型として扱うかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: `shm-id`を単なる整数IDとし、明示的な`release_shm(id)`/`acquire_shm(id)`関数で操作する。実装は単純だが、解放忘れやダングリング参照を型システムで防げない。
    - 案2: `shm-id`をRAII所有権を持つ`shared-block`リソースとして設計し、`release()`/`claim()`で所有権移動を明示し、デストラクタで自動解放する。
  - **結論**: 案2を採用する。
  - **理由**: `release()`で現在の所有者を無効化し、`claim()`で次の所有者が取得する設計により、ダングリングポインタを構造的に防止できる。デストラクタでの自動解放により手動`deallocate`忘れも排除できる。`to-shm`/`to-address`のような対称的な変換名より`release`/`claim`の方が所有権移動という意図を明確に表す（`{OwnershipTransfer}`）。具体的な通信プロトコルとの接続は、それぞれの通信契約側で定義する。

- **決定事項**: (2026-02-17)
  - **背景**: メモリマネージャのAPIに、確保済みブロックの情報を問い合わせる`query(addr) -> memory-info`と、所有権を確認する`check-ownership(addr, task-id) -> bool`を含めるかどうかを決定する必要があった。
  - **選択肢と評価**:
    - 案1: 両APIを提供し、呼び出し側が任意のアドレスについて情報・所有権を問い合わせられるようにする。汎用的だが、`shared-block`が既に保持している情報を別経路でも問い合わせ可能にする冗長な公開面を作り、`{META_FaultIsolation}`が要求する「所有権はshared-block経由でのみ確認できる」という単一の経路を弱める。
    - 案2: 両APIを削除する。サイズはkernel/task用途では呼び出し側（`allocate`時に記録済み）が、shared用途では`shared_block.get_size()`/`get_owner()`が代替する。
  - **結論**: 案2を採用する。
  - **理由**: `query()`は`allocate`時に呼び出し側がサイズを記録すれば冗長であり、`check-ownership()`は`shared_block.get_owner()`で代替可能である。生ポインタを直接やり取りする経路が存在しない設計（すべて`shared_block`リソース経由）とも整合する。

- **決定事項**: `{ADR_FivePoolMemoryModel}` (2026-09-09)
  - **背景**: システム全体のメモリ管理を、単一の汎用「パーティション貸与」契約として抽象化するか、用途別に区別された複数プールの集合として契約化するかを決定する必要があった。従来の契約は「タスクヒープ」と「共有メモリ用ヒープ」のみを規定し、ランタイム用バンプアロケータや JIT キャッシュアロケータは本契約の管轄外（それぞれ `runtime_vsoc.md`/`jit_runtime.md` が独自に規定）とされていた。
  - **選択肢と評価**:
    - 案1: 汎用パーティション貸与契約のまま据え置き、ランタイム用バンプアロケータ・JITキャッシュアロケータは各コンポーネントが独自契約として個別に定義し続ける。実装の自由度は高いが、`{GLOBAL_Policy_Memory}`が既に「ホスト・システム・共有メモリ・WASMゲスト・JIT」の5ドメインを要求として明示しているにもかかわらず、その正本契約が存在しない状態が続く。
    - 案2: 本コンポーネントを5プール（ホスト用ヒープ・タスクヒープ・共有メモリ用ヒープ・ランタイム用バンプアロケータ・JITキャッシュアロケータ）の統一契約として再定義し、各プールの貸与・返却インターフェースを本ドキュメントに集約する。
  - **結論**: 案2を採用する。
- **理由**: `{GLOBAL_Policy_Memory}`が要求する5ドメイン分離ポリシーの正本契約を一箇所に集約することで、`system_config.md`の物理メモリ予算（`FB_CONF_*_HEAP_SIZE`等）との対応関係が一望でき、将来のプール追加・境界見直しの影響範囲を本コンポーネント1つに限定できる。各プールの物理実装（dlmalloc mspace / bump allocator / W^Xコードアロケータ）は引き続き `runtime_memory.md` が担い、契約と実装の分離（`{META_ContractImplSplit}`）は維持される。

## 8. 形式検証との対応

[`system_memory_model.py`](docs/components/tier1_interface/formal/system_memory_model.py) は、共有ブロックの二重所有禁止、全プールの総予算超過禁止、および所有権移譲またはロールバックの有限完了をCTLで検証する。`guards=False` では各不変条件を破る遷移を追加し、通常モデルの性質が反証されることを確認する。
