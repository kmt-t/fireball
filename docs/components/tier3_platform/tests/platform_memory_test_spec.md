# 物理メモリ (COOSメモリマネージャ) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_platform/platform_memory.md`
参考実装: `docs/components/tier3_platform/concepts/platform_memory_concept.py`

統合物理メモリプールからの独立ヒープパーティション切り出し、`shared-block`のRAII所有権移譲、Cortex-M33 PMSAv8 MPUリージョン配分とJIT W^X切替プロトコルを検証する。

## 2. テストケース一覧

### パーティション管理 (§1, §4-6)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-01 | `acquire-partition`はタスク固有の固定長パーティションを貸与する（汎用ヒープAPIではない） | 任意のタスクID | `acquire-partition(owner)` | `size`引数を取らない。固定長`partition-view`を返す。`allocate(size, category)`のような任意サイズ確保APIは存在しない | §4 acquire-partition, os_coos.md §5.2「汎用ヒープAPIではない」 |
| MEM-01b | `acquire-slot<T>`は型付きスロットを貸与する | - | `acquire-slot<T>()` | `pool-ref<T>`（型付きハンドル）を返す | §4 acquire-slot |
| MEM-02 | 割り当て失敗時のリカバリー戦略 | パーティション/スロット枯渇 | `acquire-partition`/`acquire-slot`失敗 | `memory-error`（`recovery-strategy`へ変換される）を返す（生のエラーコードのみを返して終わりにしない） | §4 |
| MEM-03 | 総割当量の上限 | - | 複数回`acquire-partition`/`acquire-slot`/`allocate-shared` | `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE`を常に満たす | §5「制約と不変条件」 |
| MEM-04 | 所有者task-idの自動設定 | - | `acquire-partition`/`acquire-slot`/`allocate-shared` | 呼び出し元task-idが自動設定される（`block.owner != 0`） | §5, §6 |
| MEM-05 | `release-partition`/`deallocate`は所有者のみ実行可能 | 他タスクが確保したブロック | 別task-idから`release-partition`/`deallocate` | 拒否される | §6「所有者タスクのみ実行可能」 |
| MEM-06 | ゲストRAMの64KBアライメント | `pool-base`設定 | アドレスを確認 | WASMページ境界(64KB)に配置され、vMMIO/インタープリタの単一比較命令高速判定の前提を満たす | §5「WasmPageAlignment」 |
| MEM-07 | `allocate-shared`はvMMIO FC=14ページを実際に登録する | - | `allocate-shared(size)` | 対応するvMMIO PTEが`owner_id`=呼び出し元タスクIDで登録される（`runtime_vmmio.md` `map_shm_page`相当） | §4 allocate-shared 事後条件（本セッションでの訂正後） |
| MEM-08 | `claim`はGrant完了前には成功しない | Grantフェーズ未完了 | `claim(shm-id)` | 拒否される（`ipc_router.md`のGrantフェーズ完了＝対応vMMIO PTEの`owner_id`更新が事前条件） | §4 claim 事前条件（本セッションでの訂正後） |
| MEM-09 | `platform_hal.md`の`acquire_buffer`との一本化 | HALがバッファを確保 | `acquire_buffer(size)`（HAL）を呼ぶ | 内部的に本コンポーネントの`allocate-shared`を呼び出しており、HALとメモリマネージャが独立にSHMページを確保しない | §4 allocate-shared 補足 |

### `shared-block`ライフサイクル (§7-8)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-10 | `allocate-shared`→`release`→`claim`の所有権移動 | タスクAが`allocate-shared`済み | §7手順1-7を実行 | `release`後はA側で無効化され、`claim`後はB側が所有権を得る（二重所有なし） | §7ライフサイクル手順 |
| MEM-10b | `release()`/`claim()`とvMMIO PTE `owner_id`の対応 | 同上 | `release()`直後・`claim()`直後それぞれでvMMIO PTEを確認 | `release()`直後は`owner_id == FB_TASK_ID_FLIGHT`、`claim()`直後は`owner_id`が受信タスクIDに一致する（`shared-block`が独立した所有権状態を並行して持たない） | §7（本セッションでの訂正後）, runtime_vmmio.md §4.6 |
| MEM-10c | `rollback_transfer()`によるowner_idの復元 | `release()`済みで`owner_id == FB_TASK_ID_FLIGHT` | `rollback_transfer(original_sender_id, shm_id)`を実行 | 対応するvMMIO PTEの`owner_id`が送信元タスクIDへ復元される（`FB_TASK_ID_FLIGHT`のまま放置されない） | ipc_router.md §4.1 |
| MEM-11 | `shared-block`のRAII自動解放 | Bがdropする | drop実行 | メモリが自動解放される（明示的`deallocate`不要） | §7手順9, §8「ADR_SharedBlockRaii」 |
| MEM-12 | `shm-id`のkv_pairエンコーディング | IPC送信 | メッセージ構築 | 型スコープ上位3bit=`0b000`（機能的）、下位5bit=`0b00001`（u32）のkv_pairとして格納される。`ipc_router.md`の型語彙表にない独自の`dtype=handle`は使わない | §7「大きなデータを転送する場合」（本セッションでの訂正後）, ipc_router.md §3.3 |
| MEM-13 | `query()`/`check_ownership()`が削除されている | - | APIサーフェスを確認 | これらのAPIは存在しない（`shared_block.get_size()`/`get_owner()`で代替） | §8 ADR_MemoryManagerMinimalSurface |

### MPU / W^X (§9)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-20 | 8リージョンの静的配分 | - | MPUリージョン設定を確認 | Region0(Flash RO+X)〜Region7(Stack Guard No Access)の表どおりに配分される | §9.1 |
| MEM-21 | JIT Code Cache(Region4)のW^X切替 | パッチ生成開始 | `begin_jit_patch()` | `RO+X`→`RW+XN`に切り替わり、`__DSB();__ISB();`が発行される | §9.2 手順1 |
| MEM-22 | パッチ完了時の復元 | パッチ完了 | `commit_jit_patch()` | `RW+XN`→`RO+X`に復元され、命令キャッシュ・プリフェッチがフラッシュされる | §9.2 手順3 |
| MEM-23 | RWX状態の恒常的排除 | 任意の時点 | MPU状態を確認 | 実行可能かつ書き込み可能な状態(RWX)が存在しない | §9.2冒頭 |
| MEM-24 | トランザクションバッチ化 | 複数命令パッチを含む1コンパイル単位 | コンパイル実行 | `begin_jit_patch`/`commit_jit_patch`が1回ずつのみ発行される（命令ごとに切り替えない） | §9.2「トランザクションバッチ化」 |
| MEM-25 | PMSAv8の32バイトアライメント制約 | リージョン設定 | Base/Limitアドレスを確認 | 下位5bitが0（32バイト境界） | §9.3 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- Cortex-M33 実機での MPU レジスタ操作そのもの。
