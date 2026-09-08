# メモリマネージャ 抽象契約 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_memory.md`](docs/components/tier1_core/system_memory.md)
参考実装: [`runtime_memory_concept.py`](docs/components/tier2_runtime/concepts/runtime_memory_concept.py)（実装は Tier 2 側にあるが、本書は契約（`co_mem`）レベルの振る舞いのみを検証する）

パーティション貸与ポリシー、型付きスロット貸与、共有ブロックのRAII所有権契約、および公開APIの最小面（`{ADR_MemoryManagerMinimalSurface}`）を検証する。ハードウェア（MPU/PMSAv8）や dlmalloc アリーナの物理実装は [`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) の責務とする。

## 2. テストケース一覧

### パーティション管理 (system_memory.md (Partition Manager))

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-01 | `acquire-partition`はタスク固有の固定長パーティションを貸与する（汎用ヒープAPIではない） | 任意のタスクID | `acquire-partition(owner)` | `size`引数を取らない。固定長`partition-view`を返す。`allocate(size, category)`のような任意サイズ確保APIは存在しない | acquire-partition, os_coos.md 「汎用ヒープAPIではない」 |
| MEM-01b | `acquire-slot<T>`は型付きスロットを貸与する | - | `acquire-slot<T>()` | `pool-ref<T>`（型付きハンドル）を返す | acquire-slot |
| MEM-02 | 割り当て失敗時のリカバリー戦略 | パーティション/スロット枯渇 | `acquire-partition`/`acquire-slot`失敗 | `memory-error`（`recovery-strategy`へ変換される）を返す（生のエラーコードのみを返して終わりにしない） | - |
| MEM-03 | 総割当量の上限 | - | 複数回`acquire-partition`/`acquire-slot`/`allocate-shared` | `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE`を常に満たす | 「制約と不変条件」 |
| MEM-04 | 所有者task-idの自動設定 | - | `acquire-partition`/`acquire-slot`/`allocate-shared` | 呼び出し元task-idが自動設定される（`block.owner != 0`） | - |
| MEM-05 | `release-partition`/`deallocate`は所有者のみ実行可能 | 他タスクが確保したブロック | 別task-idから`release-partition`/`deallocate` | 拒否される | 「所有者タスクのみ実行可能」 |
| MEM-06 | ゲストRAMの64KBアライメント | `pool-base`設定 | アドレスを確認 | WASMページ境界(64KB)に配置され、vMMIO/インタープリタの単一比較命令高速判定の前提を満たす | 「WasmPageAlignment」 |
| MEM-07 | `allocate-shared`はvMMIO FC=14ページを実際にマッピング登録する | - | `allocate-shared(size)` | 対応するvMMIO PTEが呼び出し元タスクの仮想アドレス空間にマッピング登録される（`runtime_vmmio.md` `map_shm_page`相当） | allocate-shared 事後条件 |
| MEM-08 | `claim`は有効なshm-idを要求する | 無効・解放済みshm-id | `claim(shm-id)` | 拒否される（`ERR_INVALID_SHM_ID`） | claim 事前条件 |
| MEM-09 | HALの`acquire_buffer`との一本化 | HALがバッファを確保 | `acquire_buffer(size)`（HAL）を呼ぶ | 内部的に本コンポーネントの`allocate-shared`を呼び出しており、HALとメモリマネージャが独立にSHMページを確保しない | allocate-shared 補足 |

### `shared-block`ライフサイクル（契約レベル）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-10 | `allocate-shared`→`release`→`claim`の所有権移動 | タスクAが`allocate-shared`済み | ライフサイクル手順を実行 | `release`後はA側で無効化され、`claim`後はB側が所有権を得る（二重所有なし） | ライフサイクル手順 |
| MEM-10b | `release()`/`claim()`とvMMIO PTEマッピングの対応 | 同上 | `release()`直後・`claim()`直後それぞれでvMMIO PTEを確認 | `release()`直後はアンマップ（`is_mapped == False`）、`claim()`直後は受信タスク空間に再マッピング（`is_mapped == True`）される | runtime_vmmio.md |
| MEM-10c | `rollback_transfer()`によるマッピングの復元 | `release()`済みでアンマップ状態 | `rollback_transfer(original_sender_id, shm_id)`を実行 | 対応するvMMIO PTEが送信元タスク空間へ再マッピングされる（アンマップ状態のまま放置されない） | ipc_router.md |
| MEM-11 | `shared-block`のRAII自動解放 | Bがdropする | drop実行 | メモリが自動解放される（明示的`deallocate`不要） | 「ADR_SharedBlockRaii」 |
| MEM-12 | `shm-id`のkv_pairエンコーディング | IPC送信 | メッセージ構築 | 型スコープ上位3bit=`0b000`（機能的）、下位5bit=`0b00001`（u32）のkv_pairとして格納される。`ipc_router.md`の型語彙表にない独自の`dtype=handle`は使わない | ipc_router.md |
| MEM-13 | `query()`/`check_ownership()`が削除されている | - | APIサーフェスを確認 | これらのAPIは存在しない（`shared_block.get_size()`/`get_owner()`で代替） | ADR_MemoryManagerMinimalSurface |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（契約レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- ページ単位権限分離（MEM-14〜16）、MPU/W^X（MEM-20〜25）、GOTCHA（物理実装の勘所）は [`runtime_memory_test_spec.md`](docs/components/tier2_runtime/tests/runtime_memory_test_spec.md) を参照。
