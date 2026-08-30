# 物理メモリ (COOSメモリマネージャ) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_platform/platform_memory.md`
参考実装: なし
現行実装: なし（pysimは各コンポーネントがPythonオブジェクトを自由に確保しており、統合物理メモリプール/パーティション管理の概念を持たない）

統合物理メモリプールからの独立ヒープパーティション切り出し、`shared-block`のRAII所有権移譲、Cortex-M33 PMSAv8 MPUリージョン配分とJIT W^X切替プロトコルを検証する。

## 2. テストケース一覧

### パーティション管理 (§1, §4-6)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-01 | `allocate`はカテゴリ別パーティションから割り当て | kernel/taskカテゴリ | `allocate(size, category)` | 指定カテゴリの独立ヒープから割り当てられ、アドレスを返す | §4 allocate |
| MEM-02 | 割り当て失敗時のリカバリー戦略 | パーティション枯渇 | `allocate`失敗 | `recovery-strategy`を返す（エラーコードではない） | §4 allocate 戻り値 |
| MEM-03 | 総割当量の上限 | - | 複数回allocate | `total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE`を常に満たす | §5「制約と不変条件」 |
| MEM-04 | 所有者task-idの自動設定 | - | `allocate`/`allocate-shared` | 呼び出し元task-idが自動設定される（`block.owner != 0`） | §5, §6 |
| MEM-05 | `deallocate`は所有者のみ実行可能 | 他タスクが確保したブロック | 別task-idから`deallocate` | 拒否される | §6「所有者タスクのみ実行可能」 |
| MEM-06 | ゲストRAMの64KBアライメント | `pool-base`設定 | アドレスを確認 | WASMページ境界(64KB)に配置され、vMMIO/インタープリタの単一比較命令高速判定の前提を満たす | §5「WasmPageAlignment」 |

### `shared-block`ライフサイクル (§7-8)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-10 | `allocate-shared`→`release`→`claim`の所有権移動 | タスクAが`allocate-shared`済み | §7手順1-7を実行 | `release`後はA側で無効化され、`claim`後はB側が所有権を得る（二重所有なし） | §7ライフサイクル手順 |
| MEM-11 | `shared-block`のRAII自動解放 | Bがdropする | drop実行 | メモリが自動解放される（明示的`deallocate`不要） | §7手順9, §8.1 |
| MEM-12 | `shm-id`はkv_pairの`handle`型で転送 | IPC送信 | メッセージ構築 | `dtype=handle`のkv_pairとして格納される | §7「大きなデータを転送する場合」 |
| MEM-13 | `query()`/`check_ownership()`が削除されている | - | APIサーフェスを確認 | これらのAPIは存在しない（`shared_block.get_size()`/`get_owner()`で代替） | §8.2, §8.3 |

### MPU / W^X (§9)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-20 | 8リージョンの静的配分 | - | MPUリージョン設定を確認 | Region0(Flash RO+X)〜Region7(Stack Guard No Access)の表どおりに配分される | §9.1 |
| MEM-21 | JIT Code Cache(Region4)のW^X切替 | パッチ生成開始 | `begin_jit_patch()` | `RO+X`→`RW+XN`に切り替わり、`__DSB();__ISB();`が発行される | §9.2 手順1 |
| MEM-22 | パッチ完了時の復元 | パッチ完了 | `commit_jit_patch()` | `RW+XN`→`RO+X`に復元され、命令キャッシュ・プリフェッチがフラッシュされる | §9.2 手順3 |
| MEM-23 | RWX状態の恒常的排除 | 任意の時点 | MPU状態を確認 | 実行可能かつ書き込み可能な状態(RWX)が存在しない | §9.2冒頭 |
| MEM-24 | トランザクションバッチ化 | 複数命令パッチを含む1コンパイル単位 | コンパイル実行 | `begin_jit_patch`/`commit_jit_patch`が1回ずつのみ発行される（命令ごとに切り替えない） | §9.2「トランザクションバッチ化」 |
| MEM-25 | PMSAv8の32バイトアライメント制約 | リージョン設定 | Base/Limitアドレスを確認 | 下位5bitが0（32バイト境界） | §9.3 |

## 3. 現状のギャップ（pysim実装との差分）

**未実装**: `experiments/pysim`には統合物理メモリプール・パーティション管理・`shared-block`RAII・MPU W^X切替のいずれも存在しない。

- `exec_memory.py`はWin32 `VirtualAlloc`で実行可能メモリを確保するのみで、書き込み専用⇔実行専用の動的な属性切替（MEM-21〜23相当のW^X保護）を行っていない。x64_jit.pyのコンパイル方式（README「What building this actually found」bug#8参照）は、MPU W^X保護があれば必要なかった類の問題を、コード側の工夫（`rdi`レジスタによる復元ポイント）で回避している側面がある。
- `ShmBufferPool`（HAL）と統合メモリプールの関係（`allocate-shared`/`claim`によるRAII所有権移譲）は未実装。pysimの所有権チェックは`ShmBufferPool._resolve`によるtask_id照合のみで、`release`/`claim`のような明示的な所有権移動プロトコルを持たない。
- MEM-01〜25すべて未検証。

## 4. 未検証・スコープ外

- Cortex-M33実機でのMPUレジスタ操作そのもの（Pythonでは原理的に模倣不可能な領域）。
