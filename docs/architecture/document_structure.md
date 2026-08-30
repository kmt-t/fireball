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
  · vSoC Subsystem (runtime_vsoc, runtime_loader, runtime_interpreter, runtime_vmmio)
  · Debug Subsystem (debug_manager)
           │
           │  深層コンポーネント・プラットフォーム具象化への分解
           ▼
[ Tier 3: リーフ / プラットフォームコンポーネント (Leaf & Platform Components) ] ─ (How - Leaf / Physical)
  · JIT Subsystem (jit_compiler, jit_runtime) — vSoC の実行エンジンから分解された JIT コアおよびランタイム
  · Platform (platform_hal, platform_memory)

[ Meta: 横断的メタ設計・開発計画 (Cross-cutting / Meta) ] ─ (全Tier横断)
  · Architecture (architecture_overview, document_structure, integration_test_scenarios, keyword_dictionary)
  · Plans (roadmap_phase, backlog_list, backlog_archive)

[ Specs: 横串物理仕様・規格マトリクス (Cross-cutting Physical Specs & Catalogs) ] ─ (全Tier横断・具象規格)
  · Specs (wasm_instruction_set, wasi_preview1_abi, gdb_rsp_protocol, jit_stencil_catalog)
```

### 1.1 各 Tier の定義と配置ディレクトリ

| レイヤー | ディレクトリ | 定義される設計書 | 複雑度・責務の範囲 |
| :--- | :--- | :--- | :--- |
| **Tier 0** | `docs/requires/` | システム要求仕様書 (`requirement_list.md`) | **最上位要求 (Why)**<br>システム全体が満たすべき受入基準・機能要求。 |
| **Tier 1** | `docs/components/tier1_core/`<br>`docs/components/tier1_interface/` | スケジューラ、チャネル通信、システムサービス、IPCルータ、共有静的コンテナ語彙等のコア仕様書 | **粗粒度主要コンポーネント (What)**<br>要求（Tier 0）を直接受け取る。単一仕様書で状態遷移・ポリシーを自己完結して記述可能なシステム要素。 |
| **Tier 2** | `docs/components/tier2_runtime/` | WASMインタープリタ、WASMローダー、vMMIO、デバッグマネージャ等のサブコンポーネント仕様書 | **分解されたサブコンポーネント (How - Subsystem)**<br>Tier 1 で扱うには状態空間やアルゴリズムが複雑化するため、独立した責務としてブレークダウンされた要素。 |
| **Tier 3** | `docs/components/tier3_platform/`<br>`docs/components/tier3_jit/` | HAL実装、物理メモリ管理、JITコンパイラ一式（コード生成コア `jit_compiler.md`、ランタイム管理 `jit_runtime.md`）| **詳細リーフ / 物理コンポーネント (How - Leaf)**<br>Tier 2 からさらに責務が切り出された具象コンポーネント、またはハードウェア抽象化層。 |
| **Specs** | `docs/specs/` | WASM命令セット、WASI API、GDB RSP、JITステンシルカタログ等の規格マトリクス | **横串物理規格・具象カタログ (How - Physical Specs)**<br>コンポーネントを横断して統一される具象バイナリ列、ABI、パケット形式、命令セットマトリクス。 |
| **Meta** | `docs/architecture/`<br>`docs/plans/` | 全体アーキテクチャ、設計方針、開発計画 | **全Tier横断メタ設計**<br>Hypervisor の機能コンポーネント自体には属さない共通ポリシー・計画。 |

---

## 2. 階層間デコンポジションと依存性ルール

自動検証ツール `spec-integrator` は、本ルールに基づいてコンポーネント間の階層一貫性（Hierarchy Gate）およびトレーサビリティ（Traceability Gate）を検証する。

### 2.1 デコンポジション基準（いつ下位 Tier へ分解するか）
1. **単一責務・複雑度制御の原則**: コンポーネントが複数の独立した状態機械・アルゴリズムを持つ場合、単一仕様書に肥大化させず、サブコンポーネントとして分解して Tier を 1 つ下げる。
   - 判定基準は「単一仕様書に自己完結して書けるか」であり、「親から分解された」という記述の有無ではない。vSoC の Interpreter/vMMIO/Loader は vSoC から分解されたと書きつつも各々 1 ファイルに自己完結するため Tier 2 のまま（vSoC の Tier 2 サブコンポーネント群の一員）。JIT は実行時コード生成の責務分離として、コード生成コア（`jit_compiler.md`）とランタイム制御（`jit_runtime.md`）の 2 ファイルで Tier 3 に位置する。
2. **検証可能性（Verification Tractability）の維持**: 形式検証（pyModelChecking等）において状態空間が爆発しない単位に状態遷移モデルを区切る。
3. **親コンポーネントのカプセル化**: 分解元（上位Tier）は、分解先（下位Tier）の内部実装パラメータに依存せず、抽象インターフェースのみで統合する。

### 2.2 依存方向のルール
1. **下り方向の依存（詳細化・具体化）**:
   - 上位 Tier (N) の定義や要求は、下位 Tier (N+1) において具体化（詳細化）される。
   - 下位 Tier は上位 Tier のインターフェースや定数定義を明示的に参照（Refine）して実装する。
2. **上り方向の依存禁止（カプセル化・逆流禁止）**:
   - 上位 Tier (N) が、より下位の Tier (N+1, N+2) の内部具象構造や下位パラメータに直接依存してはならない。
   - 上位コンポーネントが下位の機能を束ねる場合（例: vSoC ハーネス）、必ず定義されたインターフェース（Stateless Interface / Harness）を介して統合すること。

### 2.3 矛盾が見つかった場合の解決規則（Clean Architecture の依存ルールに基づく）

下位 Tier の文書が、それが参照・具体化しているはずの上位 Tier の文書と矛盾している場合、**常に上位 Tier 側が正**である。Clean Architecture の依存ルールにおいて、方針（policy）は内側の層が定義し、詳細（detail）は外側の層がそれに従って実装するのであって、逆向きに詳細が方針を決めることはない。本プロジェクトの Tier 構造ではこれが「上位 Tier ほど粗粒度の方針を、下位 Tier ほど具体化された詳細を記述する」という配置（§1）に対応するため、下位が上位と食い違うのは常に**下位側の記述誤り・追随漏れ**であり、上位側を下位に合わせて書き換えることはしない。

- 矛盾を見つけた場合は、上位 Tier の記述（インターフェイス名・シグネチャ・状態モデル・語彙）に下位 Tier 側を合わせて修正する。
- 同一 Tier 内の文書同士が食い違う場合（例: Tier 3 の `platform_hal.md` と `platform_memory.md`）は上下関係がないため、両者が同じ下位機構（例: 同じ vMMIO アドレス空間）を指しているかを確認し、矛盾なく整合させる。判断がつかない場合は解決を断定せず、両文書に矛盾として明記した上でどちらかの正本化を提案する。
- 「上位 Tier の記述が古い／実装しづらい」という理由で下位 Tier 側の記述を正として上位を書き換えることはしない。上位を変更すべきだとコンポーネント作業者が判断した場合は、それ自体を独立した提案として明示し、黙って下位からの逆流で上書きしない（§2.2「上り方向の依存禁止」）。

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

---

## 5. 検証タグとエビデンス（Evidence）の対応体系

各設計書は、タイトル行で検証種別（`{VERIFY_*}`）を明示し、直下に `<!-- evidence: ... -->` コメントブロックを配置して機械検証可能なエビデンスファイルを宣言する。

### 5.1 検証タグとエビデンスの対応表

| 検証タグ | 検証義務の分類 | 必要なエビデンス宣言 (`<!-- evidence: ... -->`) | 検証を実行・判定する Verifier |
| :--- | :--- | :--- | :--- |
| `VERIFY_FORMAL` | 形式検証義務 | `formal: formal/*_model.py`<br>（Kripke 構造・CTL/LTL 性質・`BACKS` 宣言） | **Formal Gate** + **Obligation Gate** |
| `VERIFY_WIT` | インターフェース契約義務 | `wit: wit/*.wit`<br>（型安全・物理バイトオフセット・リカバリー戦略） | **WIT Gate** |
| `VERIFY_BENCHMARK` | 定量性能・予算実測義務 | `benchmark: benchmarks/*_bench.py`<br>（計算量 $O(1)/O(\log N)$、レイテンシ実測） | **Phase 3 (Benchmarks)** + **Evidence Gate** |
| `VERIFY_LLM` | 意味的整合性・ADR監査義務 | LLM as a Judge 判定ログ（さくらインターネット / Qwen 3.6） | **Phase 2 (Judge)** + **Obligation Gate** |
| *(暗黙・全件)* | 実行可能参照実装 | `concept: concepts/*_concept.py` | **Phase 3 (Concepts)** + Unicorn エミュレータ |
| *(暗黙・全件)* | テスト仕様（振る舞い網羅） | `test: tests/` サブフォルダ内のコンポーネント名を冠したテスト仕様書（`concept`/`formal`/`benchmark` と同じ配置規則: 正本と同じディレクトリ直下の共有 `tests/` サブフォルダに置く） | **Phase 3 (Concepts)** と同格の実行可能参照実装の一部として扱う。`concept`/`formal`/`benchmark` と同様、正本の直下 `<!-- evidence: ... -->` ブロックに宣言すること。テストケースは対象の正本（および対応する `concepts/*_concept.py`）を実際に読んだ上で導出し、実装から逆算しないこと。 |

### 5.2 形式検証モデル（`formal/*.py`）の責任分担正本表

| 形式検証モデルファイル | 検証・証明する対象性質 | `BACKS` 正本ドキュメント一覧 |
| :--- | :--- | :--- |
| [`../components/tier1_core/formal/coos_channel_model.py`](../components/tier1_core/formal/coos_channel_model.py) | - CSP チャネル純粋ランデブー<br>- デッドロック不在・二重所有不在<br>- 連続ハンドオフ有界復帰 | - `components/tier1_core/os_coos.md`<br>- `components/tier1_core/os_scheduler.md`<br>- `components/tier1_core/system_config.md` |
| [`../components/tier1_interface/formal/csp_handoff_model.py`](../components/tier1_interface/formal/csp_handoff_model.py) | - 所有権移譲と Drop ハンドラによる二重所有・リーク防止 | - `components/tier1_interface/ipc_router.md` |
| [`../components/tier2_runtime/formal/vsoc_cache_coherency_model.py`](../components/tier2_runtime/formal/vsoc_cache_coherency_model.py) | - vSoC JIT キャッシュ整合性・Debugger 介入安全性・ローテーション有界性 | - `components/tier2_runtime/runtime_vsoc.md`<br>- `components/tier2_runtime/debug_manager.md`<br>- `components/tier3_jit/jit_compiler.md`<br>- `components/tier3_platform/platform_memory.md` |
| [`../components/tier2_runtime/formal/vsoc_state_model.py`](../components/tier2_runtime/formal/vsoc_state_model.py) | - vSoC 実行状態<br>- Safepoint ポーリング応答性<br>- 割り込み/デバッグフォールバック | - `components/tier2_runtime/runtime_vsoc.md`<br>- `components/tier2_runtime/runtime_vmmio.md`<br>- `components/tier2_runtime/runtime_interpreter.md`<br>- `components/tier2_runtime/debug_manager.md`<br>- `components/tier3_platform/platform_hal.md`<br>- `components/tier1_core/system_config.md` |
| [`../components/tier3_jit/formal/jit_cache_model.py`](../components/tier3_jit/formal/jit_cache_model.py) | - 3面キャッシュ代謝<br>- MPU W^X 保護<br>- 遅延チェイニング局所アンリンク安全性<br>- 2-bit Hotspot FSM | - `components/tier3_jit/jit_compiler.md`<br>- `components/tier3_jit/jit_runtime.md`<br>- `components/tier3_platform/platform_memory.md` |
