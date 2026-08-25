# Fireball ドキュメント体系定義書 (Document Structure & Metadata)

この文書は `docs/**` における階層構造（Tier）、メタキーワード、トレーサビリティの正本である。下位文書で表記が揺れた場合はこの文書を優先する。

本ドキュメントは、Fireball プロジェクトにおける設計書の配置ルール、**設計複雑度（Complexity）に基づくシステム分解階層（Decomposition Tiers）**の定義、およびドキュメント間の一貫性検証のための依存性ルールを定義する。 `{META_AI_Native_Dev}`

---

## 1. 設計複雑度に基づく Tier（分解階層）の定義

Fireball では、システム全体の認知負荷、形式検証（モデル検査）の状態空間爆発、および仕様変更の影響を局所化・制御するため、**「設計複雑度に応じた階層的デコンポジション（分解）」**を採用する。

Tier は単なる「OSやハードウェアの実行レイヤ」ではなく、**「複雑すぎるコンポーネントを扱いやすい粒度のサブコンポーネントへブレークダウンした深さ（Decomposition Depth）」**を表す。

```
[ Tier 0: システム要求仕様 (Requirements) ] ─ (最上位要求: Why)
           │
           │  システム主要責務への分解
           ▼
[ Tier 1: 主要システムコンポーネント (Primary Components) ] ─ (What)
  · COOS (os_coos, os_scheduler)
  · Interface (ipc_router, system_service, interface_wit)
  · System Core (system_config, system_logging, system_syscall, system_containers)
           │
           │  複雑な状態空間・機能のサブシステム分解
           ▼
[ Tier 2: 分解されたサブコンポーネント (Decomposed Subcomponents) ] ─ (How - Subsystem)
  · vSoC Subsystem (runtime_vsoc, runtime_loader, runtime_interpreter, runtime_vmmio, wasm_instruction)
  · Debug Subsystem (debug_manager)
  · Configuration Details (system_config_details)
           │
           │  深層コンポーネント・プラットフォーム具象化への分解
           ▼
[ Tier 3: リーフ / プラットフォームコンポーネント (Leaf & Platform Components) ] ─ (How - Leaf / Physical)
  · JIT Subsystem (jit_compiler, jit_engine_copy_patch, jit_assembler_constexpr, jit_runtime_entry, jit_runtime_hotspot) — vSoC の実行エンジンから分解された JIT 一式
  · Debug Internals (debug_gdb_rsp)
  · Platform (platform_hal, platform_memory)

[ Meta: 横断的メタ設計・開発計画 (Cross-cutting / Meta) ] ─ (全Tier横断)
  · Architecture (architecture_overview, concept_harness, document_structure, master_physical_design, resource_budget)
  · Plans (roadmap_phase, backlog_list, backlog_archive)

[ Specs: 横串物理仕様・規格マトリクス (Cross-cutting Physical Specs & Catalogs) ] ─ (全Tier横断・具象規格)
  · Specs (wasm_instruction_set, wasi_preview1_abi, gdb_rsp_protocol, jit_stencil_catalog)
```

### 1.1 各 Tier の定義と配置ディレクトリ

| レイヤー | ディレクトリ | 定義される設計書 | 複雑度・責務の範囲 |
| :--- | :--- | :--- | :--- |
| **Tier 0** | `docs/requires/` | システム要求仕様書 (`requirement_list.md`) | **最上位要求 (Why)**<br>システム全体が満たすべき受入基準・機能要求。 |
| **Tier 1** | `docs/components/tier1_core/`<br>`docs/components/tier1_interface/` | スケジューラ、チャネル通信、システムサービス、IPCルータ、共有静的コンテナ語彙等のコア仕様書 | **粗粒度主要コンポーネント (What)**<br>要求（Tier 0）を直接受け取る。単一仕様書で状態遷移・ポリシーを自己完結して記述可能なシステム要素。 |
| **Tier 2** | `docs/components/tier2_runtime/` | WASMインタープリタ、WASMローダー、vMMIO等のサブコンポーネント仕様書 | **分解されたサブコンポーネント (How - Subsystem)**<br>Tier 1 で扱うには状態空間やアルゴリズムが複雑化するため、独立した責務としてブレークダウンされた要素。 |
| **Tier 3** | `docs/components/tier3_platform/`<br>`docs/components/tier3_jit/` | HAL実装、物理メモリ管理、JITコンパイラ一式（Copy-and-Patchコード生成器、Constexprアセンブラ等）| **詳細リーフ / 物理コンポーネント (How - Leaf)**<br>Tier 2 からさらに責務が切り出された具象コンポーネント、またはハードウェア抽象化層。JIT は vSoC (Tier 2) の実行エンジンから分解された一式として、内部を jit_compiler.md がオーケストレーションする。 |
| **Specs** | `docs/specs/` | WASM命令セット、WASI API、GDB RSP、JITステンシルカタログ等の規格マトリクス | **横串物理規格・具象カタログ (How - Physical Specs)**<br>コンポーネントを横断して統一される具象バイナリ列、ABI、パケット形式、命令セットマトリクス。 |
| **Meta** | `docs/architecture/`<br>`docs/plans/` | 全体アーキテクチャ、設計方針、開発計画 | **全Tier横断メタ設計**<br>Hypervisor の機能コンポーネント自体には属さない共通ポリシー・計画。 |

---

## 2. 階層間デコンポジションと依存性ルール

自動検証ツール `spec-integrator` は、本ルールに基づいてコンポーネント間の階層一貫性（Hierarchy Gate）およびトレーサビリティ（Traceability Gate）を検証する。

### 2.1 デコンポジション基準（いつ下位 Tier へ分解するか）
1. **単一責務・複雑度制御の原則**: コンポーネントが複数の独立した状態機械・アルゴリズムを持つ場合、単一仕様書に肥大化させず、サブコンポーネントとして分解して Tier を 1 つ下げる。
   - 判定基準は「単一仕様書に自己完結して書けるか」であり、「親から分解された」という記述の有無ではない。vSoC の Interpreter/vMMIO/Loader は vSoC から分解されたと書きつつも各々 1 ファイルに自己完結するため Tier 2 のまま（vSoC の Tier 2 サブコンポーネント群の一員）。JIT だけは複雑度が突出しており、`jit_compiler.md` を親に 4 本のリーフ仕様書へさらに分割する必要があったため、JIT 一式は Tier 3 に位置する。
2. **検証可能性（Verification Tractability）の維持**: 形式検証（pyModelChecking等）において状態空間が爆発しない単位に状態遷移モデルを区切る。
3. **親コンポーネントのカプセル化**: 分解元（上位Tier）は、分解先（下位Tier）の内部実装パラメータに依存せず、抽象インターフェースのみで統合する。

### 2.2 依存方向のルール
1. **下り方向の依存（詳細化・具体化）**:
   - 上位 Tier (N) の定義や要求は、下位 Tier (N+1) において具体化（詳細化）される。
   - 下位 Tier は上位 Tier のインターフェースや定数定義を明示的に参照（Refine）して実装する。
2. **上り方向の依存禁止（カプセル化・逆流禁止）**:
   - 上位 Tier (N) が、より下位の Tier (N+1, N+2) の内部具象構造や下位パラメータに直接依存してはならない。
   - 上位コンポーネントが下位の機能を束ねる場合（例: vSoC ハーネス）、必ず定義されたインターフェース（Stateless Interface / Harness）を介して統合すること。

---

## 3. ドキュメントの静的チェックルール

各設計書は、システムの一貫性を保つため、以下の静的チェックおよびフォーマットに適合しなければならない。

- **フォーマット適合**: `docs/components/FORMAT.md` に準拠し、C++コードブロックの直接記述の禁止（Pythonで疑似コードを記述）、`####` 見出しにおけるC++識別子のみの表記の禁止（自然言語を添える）等を守ること。
- **要求キーワード（Keyword）の紐付け**: 設計書内の各セクションは、末尾に要求キーワード（中括弧で囲まれたもの）を付与し、`requirement_list.md` に定義された要求仕様とのトレーサビリティを維持すること。

---

## 4. 特殊キーワードの分類と検証仕様

### 4.1 分類基準と検証時の挙動

| 分類 | 命名規則 | 定義対象 | 検証時（spec-integrator）の挙動 |
| :--- | :--- | :--- | :--- |
| **メタキーワード** | `{META_[Name]}` | システム横断的な非機能要求、アーキテクチャ設計方針、共通パターン。 | - 階層検証 (`Hierarchy Gate`) の逆流判定から除外される。<br>- LLM as a Judge の意味的監査基準として読み込まれる。 |
| **グローバルキーワード** | `{GLOBAL_[Name]}` | システム全体（多数の仕様書）に適用される広域ポリシー、プラットフォーム要件。 | - 各仕様書の単体要求適合性 (`Traceability Gate`) で検証される。 |
| **ローカルキーワード** | `{[Name]}` (プレフィックスなし) | 個別の機能、コンポーネント（スケジューラ、チャネル通信など）に閉じた具体的な要求。 | - Traceability Gate および DocGraph 上のすべての追跡対象となる。 |

---

### 4.2 メタキーワード（共通非機能要件・設計方針）の定義

| キーワード | 説明 |
| :--- | :--- |
| `{META_3TierSeparation}` | 設計複雑度に応じた3階層のデコンポジション（分解）とカプセル化された依存関係管理。 |
| `{META_ConfigurableSystem}` | ヘッダマクロ定義および `constexpr` 定数により、システムパラメータをコンパイル時に静的確定する。 |
| `{META_FaultIsolation}` | メモリパーティションにより、コンポーネント間の障害伝播を防止する。 |
| `{META_RecoveryStrategy}` | エラーコードの代わりに推奨されるリカバリー動作（Retry/Panic等）を返し、自己修復を促進する。 |
| `{META_RestrictedPhysicalAccess}` | 物理リソースへのアクセスを許可テーブルで厳格に制限する。 |
| `{META_StaticDI}` | コンパイル時の設定・静的バインディングにより依存性を注入する。 |
| `{META_AI_Native_Dev}` | 定型的な実装はLLMを活用し、設計と検証の品質を重視する。 |
| `{META_Risk_Tiering}` | リスクベースの設計階層化。重要度や不確実性に応じて検証レベルを調整する。 |
| `{META_SpecificationFirst}` | 実装に先立ち、形式仕様や契約を定義する開発スタンス。 |
| `{META_ZeroOverhead}` | ゼロコスト抽象化。高性能組み込み向けC++デザイン。 |
| `{META_ZeroCostAbstraction}` | 抽象化のコストを実行時に支払わない。C++非仮想インターフェース等による最適化。 |
| `{META_Static_Resolution}` | 実行時に決定可能な事項はコンパイル時に決定し、オーバーヘッドを最小化する。 |
| `{META_CompileTimeValidation}` | 静的な型チェックやconstexprにより、コンパイル時に不正を検知する。 |
| `{META_NoStdVector}` | 動的な `std::vector` の使用を禁止し、固定長またはカスタムコンテナを使用する。 |
| `{META_BumpAllocator}` | メモリの断片化を防ぎ、コンパイル時または実行時に高速なメモリ割り当てを行うアロケータ。 |
| `{META_FlatMapIndexed}` | ソート済み配列や `fireball::flat_map_view`、二段テーブル等を用いて、順序維持と高速検索を省メモリで実現する。 |
| `{META_BinarySearch}` | ソート済み配列に対する $O(\log N)$ の高速検索。 |
| `{META_AccessDictionary}` | データの索引化と、それを用いたランタイムアクセスの最適化。 |

---

### 4.3 グローバルキーワード（広域仕様・横断ポリシー）の定義

| キーワード | 説明 |
| :--- | :--- |
| `{GLOBAL_Policy_Memory}` | メモリ管理や割り当てに関する共通ポリシー。 |
| `{GLOBAL_StrictMemoryLimit}` | メモリの上限が厳格に制限された動作。 |
| `{GLOBAL_IndependentHeap}` | 各コンポーネントが互いに独立したヒープメモリ領域を確保する設計。 |
| `{GLOBAL_IdleDetection}` | アイドル状態の検出とログフラッシュ・バックグラウンド処理制御。 |
| `{GLOBAL_PeriodicTask}` | 周期的に実行されるタスクスケジュール。 |
| `{GLOBAL_ComponentHarness}` | テストや検証、サブコンポーネント統合のためのハーネスパターン。 |
| `{GLOBAL_InterruptWakeup}` | 割り込み契機による待機解除・復帰処理。 |
| `{GLOBAL_UseCpp20Coroutine}` | C++20 コルーチンの使用方針。 |
| `{GLOBAL_UseCpp23Library}` | C++23 標準ライブラリ機能の使用方針。 |
| `{GLOBAL_StaticScalability}` | 静的にパラメータ化されたスケーラビリティ。 |
