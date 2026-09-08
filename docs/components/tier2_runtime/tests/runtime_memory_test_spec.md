# メモリマネージャ 物理実装 テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_memory.md`](docs/components/tier2_runtime/runtime_memory.md)（契約は [`system_memory.md`](docs/components/tier1_core/system_memory.md) を正本とする）
参考実装: [`runtime_memory_concept.py`](docs/components/tier2_runtime/concepts/runtime_memory_concept.py)

ページ単位権限分離、`shared-block`と物理 vMMIO PTE/TLB との連動、Cortex-M33 PMSAv8 MPUリージョン配分とJIT W^X切替プロトコルという、`system_allocator`/`shm_allocator` の物理実装レベルの振る舞いを検証する。契約レベルの公開API振る舞い（MEM-01〜13）は [`system_memory_test_spec.md`](docs/components/tier1_core/tests/system_memory_test_spec.md) の責務とする。

## 2. テストケース一覧

### ページ単位権限分離の物理実装

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-14 | ページ単位権限分離 | タスク1とタスク2が`allocate-shared`実行 | 割り当てられた`page_idx`を比較 | 異なるタスクのスロットは同一ページに混在せず、必ず別個の4KB物理ページ（異なる`page_idx`）に割り当てられる | 「ページ単位権限分離仕様」, 「ADR_PageGranularPermissionIsolation」 |
| MEM-15 | vMMIO FC=14マッピングとTLBフラッシュ連動 | 共有メモリ操作 | `allocate_shared`/`release`/`claim`/`drop`実行 | vMMIO FC=14 PTEのマップ／アンマップと連動して対応VPNのTLBスロットが即座にフラッシュされる | 「共有メモリマッピングと仮想化リスナーへのコールバック委譲」 |
| MEM-16 | 他タスク所有SHMページへのvMMIOアクセス遮断 | タスク1がSHM確保 | タスク2のコンテキスト（未マッピング状態）でvMMIO経由アクセス | `TRAP_UNREGISTERED_PAGE`（未登録ページフォルト）により安全に遮断される | runtime_vmmio.md |

### MPU / W^X

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-20 | 8リージョンの静的配分 | - | MPUリージョン設定を確認 | Region0(Flash RO+X)〜Region7(Stack Guard No Access)の表どおりに配分される | {MPU_WX_Enforcement} |
| MEM-21 | JIT Code Cache(Region4)のW^X切替 | パッチ生成開始 | `begin_jit_patch()` | `RO+X`→`RW+XN`に切り替わり、`__DSB();__ISB();`が発行される | {MPU_WX_Enforcement} |
| MEM-22 | パッチ完了時の復元 | パッチ完了 | `commit_jit_patch()` | `RW+XN`→`RO+X`に復元され、命令キャッシュ・プリフェッチがフラッシュされる | {MPU_WX_Enforcement} |
| MEM-23 | RWX状態の恒常的排除 | 任意の時点 | MPU状態を確認 | 実行可能かつ書き込み可能な状態(RWX)が存在しない | {MPU_WX_Enforcement} |
| MEM-24 | トランザクションバッチ化 | 複数命令パッチを含む1コンパイル単位 | コンパイル実行 | `begin_jit_patch`/`commit_jit_patch`が1回ずつのみ発行される（命令ごとに切り替えない） | {MPU_WX_Enforcement} |
| MEM-25 | PMSAv8の32バイトアライメント制約 | リージョン設定 | Base/Limitアドレスを確認 | 下位5bitが0（32バイト境界） | {MPU_WX_Enforcement} |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MEM-GOTCHA-01 | ページ単位権限分離の強制（異種所有者の相乗り禁止） | タスクAとタスクBが共有メモリスロットを要求 | それぞれの物理アドレス（`page_idx`）を検証 | 異なるタスクのスロットが同一 4KB 物理ページ内に共存することはなく、必ず別個の 4KB ページが割り当てられる。**実装の勘所**: 物理 MPU および vMMIO のアクセス制御は 4KB 単位で行われるため、複数タスクのデータを 1 ページに相乗りさせると、ハードウェア保護境界を貫通する重大なセキュリティ侵害となる | `runtime_memory.md` {PageGranularPermissionIsolation} |
| MEM-GOTCHA-02 | RAII 解放時の所有者タスク検証（不正解放の完全遮断） | タスクAが共有ブロックを所有 | タスクBのコンテキストで該当ブロックの解放を試行 | メモリマネージャは解放処理を拒否し、権限違反エラーを記録する。**実装の勘所**: 共有メモリ ID さえ知っていれば誰でも解放できる素朴なアロケータ設計にすると、他タスクのブロックを勝手に解放する悪意ある攻撃やバグを防げない | `runtime_memory.md` {OwnershipTransfer} |
| MEM-GOTCHA-03 | 送信中状態のアンマップとロールバック保護 | 送信タスクが `release()` を実行 | 送信中ブロックに対する送受信双方からのアクセスを試行 | vMMIO から PTE がアンマップされ、TLB が即時破棄されているため、いかなるタスクからのアクセスも `TRAP_UNREGISTERED_PAGE` で遮断される。転送失敗時は `rollback_transfer()` により安全に送信元空間へ再マッピングされる。**実装の勘所**: 送信中リソースへの書き込みを許すと、受信側が破損データを読み取る TOCTOU（Time-of-Check to Time-of-Use）脆弱性が発生する | `runtime_memory.md` {OwnerMismatchTrap} |
| MEM-GOTCHA-04 | W^X 切り替えのトランザクションバッチ化 | 複数命令からなる JIT トレースのパッチ | パッチ生成から完了まで | 命令生成中は一括して `RW+XN` に切り替え、完了時に一括して `RO+X` とキャッシュフラッシュ（DSB/ISB）を行う。命令ごとに切り替えることはしない。**実装の勘所**: 命令単位で MPU レジスタ書き換えとバリアを発行すると、パイプラインフラッシュが頻発してコンパイル性能が桁違いに悪化する | `runtime_memory.md` {MPU_WX_Enforcement} |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（物理実装レベルの不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- Cortex-M33 実機での MPU レジスタ操作そのもの。
- 契約レベルの公開API振る舞い（MEM-01〜13）は [`system_memory_test_spec.md`](docs/components/tier1_core/tests/system_memory_test_spec.md) を参照。
