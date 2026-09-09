# メモリマネージャ 抽象契約 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_memory.md`](docs/components/tier1_core/system_memory.md)
参考実装: [`runtime_memory_concept.py`](docs/components/tier2_runtime/concepts/runtime_memory_concept.py)（実装は Tier 2 側にあるが、本書は契約（`co_mem`）レベルの振る舞いのみを検証する）

パーティション貸与ポリシー、型付きスロット貸与、共有ブロックのRAII所有権契約、および公開APIの最小面（`{ADR_MemoryManagerMinimalSurface}`）を検証する。ハードウェア（MPU/PMSAv8）や dlmalloc アリーナの物理実装は [`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) の責務とする。

## 2. テストケース一覧

### パーティション管理 (system_memory.md (Partition Manager))

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-01 | `acquire-task-heap`はタスク固有の固定長パーティションを貸与する（汎用ヒープAPIではない） | 任意のタスクID | `acquire-task-heap(owner)` | `size`引数を取らない。固定長`partition-slice`を返す。`allocate(size, category)`のような任意サイズ確保APIは存在しない | acquire-task-heap, `{CooperativeMultitasking}` |
| MEM-01b | `acquire-slot<T>`は型付きスロットを貸与する（C++専用API、WIT対応なし） | - | `acquire_slot<T>()` | `pool_ref<T>`（型付きハンドル）を返す | acquire-slot |
| MEM-02 | 割り当て失敗時のエラー | パーティション/スロット枯渇 | `acquire-task-heap`/`acquire-slot`失敗 | `memory-error`（`out-of-memory`）を返す（生のエラーコードのみを返して終わりにしない） | - |
| MEM-03 | 総割当量の上限 | - | 複数回`acquire-task-heap`/`acquire-slot`/`allocate-shared` | `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE`を常に満たす | `{GLOBAL_StrictMemoryLimit}` |
| MEM-04 | 所有者task-idの自動設定 | - | `acquire-task-heap`/`acquire-slot`/`allocate-shared` | 呼び出し元task-idが自動設定される（`block.owner != 0`） | - |
| MEM-05 | `release-task-heap`/`release-slot`は所有者のみ実行可能 | 他タスクが確保したブロック | 別task-idから`release-task-heap`/`release-slot` | 拒否される（`memory-error`: `invalid-owner`） | 「所有者タスクのみ実行可能」 |
| MEM-08 | `claim`は有効なハンドルを要求する | 無効・解放済みハンドル | `claim(handle)` | 拒否される（`memory-error`: `invalid-owner`または`out-of-bounds`） | claim 事前条件 |
| MEM-09 | HALの`acquire_buffer`との一本化 | HALがバッファを確保 | `acquire_buffer(size)`（HAL）を呼ぶ | 内部的に本コンポーネントの`allocate-shared`を呼び出しており、HALとメモリマネージャが独立にSHMページを確保しない | allocate-shared 補足 |

### `shared-block`ライフサイクル（契約レベル）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-10 | `allocate-shared`→`release`→`claim`の所有権移動 | タスクAが`allocate-shared`済み | ライフサイクル手順を実行 | `release`後はA側で無効化され、`claim`後はB側が所有権を得る（二重所有なし） | `{OwnershipTransfer}` |
| MEM-10c | `rollback_transfer()`による所有権の復元 | `release()`済みで送信中断 | `rollback_transfer(original_sender_id, handle)`を実行 | 所有権が送信元タスクへ復元される（ダングリングのまま放置されない）。物理的なページ再マッピング挙動の検証は [`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) MEM-10c(物理)を正本とする | ipc_router.md |
| MEM-11 | `shared-block`のRAII自動解放 | Bがdropする | drop実行 | メモリが自動解放される（明示的`release`呼び出し不要） | 「ADR_SharedBlockRaii」 |
| MEM-12 | `shm-id`のkv_pairエンコーディング | IPC送信 | メッセージ構築 | 型スコープ上位3bit=`0b000`（機能的）、下位5bit=`0b00001`（u32）のkv_pairとして格納される。`ipc_router.md`の型語彙表にない独自の`dtype=handle`は使わない | ipc_router.md |
| MEM-13 | `query()`/`check_ownership()`が削除されている | - | APIサーフェスを確認 | これらのAPIは存在しない（`shared_block.get_size()`/`get_owner()`で代替） | ADR_MemoryManagerMinimalSurface |

### ランタイム用バンプアロケータ・JITキャッシュアロケータ（契約レベル）
<!-- traceability: {Runtime_BumpAllocator} {JIT_MultiBuffer_Cache} -->

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-26 | `acquire-runtime-arena`は1ランタイム1アリーナを貸与する | 任意のランタイムID | 同一`runtime-id`で2回`acquire-runtime-arena` | 1回目は`runtime-arena`ハンドルを返す。2回目は`{OneRuntimeOneGuest}`により拒否されるか同一ハンドルを返す（実装は`runtime_loader.md`を正本とする） | `{Runtime_BumpAllocator}`, `{OneRuntimeOneGuest}` |
| MEM-27 | `bump-alloc`は単調増加・非重複のアドレスを返す | アリーナ取得済み | 複数回`bump-alloc`を発行 | アドレスは単調増加し重複しない。アリーナ枯渇時は`memory-error`（`out-of-memory`）を返す | `{Runtime_BumpAllocator}` |
| MEM-28 | `reset-runtime-arena`はO(1)でバンプポインタを先頭へ巻き戻す | N個確保済み | `reset-runtime-arena`後に再度`bump-alloc` | リセット後最初の`bump-alloc`はアリーナ基点アドレスを返す | `{Runtime_BumpAllocator}` |
| MEM-29 | `release-runtime-arena`後は`bump-alloc`が拒否される | アリーナ返却済み | 返却済みアリーナへ`bump-alloc` | `memory-error`（`invalid-owner`）を返す | `{Runtime_BumpAllocator}` |
| MEM-30 | `acquire-jit-cache`は起動時1回のみ許可される | - | 2回目の`acquire-jit-cache` | 2回目は拒否される（単一リージョン保証、`{GLOBAL_StrictMemoryLimit}`） | `{JIT_MultiBuffer_Cache}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（契約レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。
- MEM-26〜30（ランタイム用バンプアロケータ・JITキャッシュアロケータの契約レベル振る舞い）は本書で契約として定義済みだが、対応する実行可能テスト（concept code / pysim）は [`runtime_loader.md`](docs/components/tier2_runtime/runtime_loader.md) および [`jit_runtime.md`](docs/components/tier3_jit/jit_runtime.md) 側の実装完了後にそれぞれの正本テスト仕様書へ追加される（現状は契約定義のみで実行時検証は未着手であることを明示する。サイレントな欠落ではなく既知の追跡対象とする）。

## 4. 未検証・スコープ外

- ページ単位権限分離（MEM-14〜16）、MPU/W^X（MEM-20〜25）、GOTCHA（物理実装の勘所）は [`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) を参照。
- vMMIO PTE / TLB の具体的な物理挙動（マッピング登録・アンマップ・再マッピング）は本書のスコープ外であり、`runtime_memory_test_spec.md` を正本とする（本契約はvMMIO等の内部シンボル・アドレス体系を一切参照しないため）。
