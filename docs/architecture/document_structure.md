# Fireball ドキュメント体系定義書 (Document Structure & Metadata)

この文書は `docs/**` における階層、メタキーワード、traceability の正本である。下位文書で表記が揺れた場合はこの文書を優先する。

本ドキュメントは、Fireballプロジェクトにおける設計書（仕様書）の配置ルール、システム階層（Tier）との対応関係、およびドキュメント間の一貫性検証のための「依存性ルール（メタ定義）」を定義する。 `{META_AI_Native_Dev}`

---

## 1. ドキュメント階層とフォルダ構成の定義

システムは機能の抽象度に応じて4つのTier（0〜3）に分割され、各設計書は対応するディレクトリに厳格に配置されなければならない。

| レイヤー | ディレクトリ | 定義される設計書 | 抽象度 |
| :--- | :--- | :--- | :--- |
| **Tier 0** | `docs/requires/` | システム要求仕様書 (`requirement_list.md`) | 最高（要件定義） |
| **Tier 1** | `docs/components/tier1_core/`<br>`docs/components/tier1_interface/` | スケジューラ、チャネル通信、システムサービス、IPCルータ等のコア仕様書 | 高（システムインターフェース・コアポリシー） |
| **Tier 2** | `docs/components/tier2_runtime/`<br>`docs/components/tier2_jit/` | WASMインタープリタ、WASMローダー、JITエンジン等の実行エンジン仕様書 | 中（エンジン内部設計・メモリ管理） |
| **Tier 3** | `docs/components/tier3_platform/` | HAL実装、プラットフォーム依存メモリ、ハードウェアドライバ等の物理・プラットフォーム抽象化仕様書 | 低（ハードウェア/プラットフォーム依存） |
| **Meta** | `docs/architecture/`<br>`docs/plans/` | 全体アーキテクチャ、設計方針、開発計画（Hypervisorそのものの仕様には含まれないメタ設計） | 適用外（メタ仕様） |

---

## 2. 階層間依存性（トレーサビリティ）ルール

自動テストツール `spec-integrator` は、本ドキュメントに定義されたTierマッピングに基づいて、各ドキュメントの階層一貫性を検証する。

### 2.1 依存方向のルール
1. **下り方向の依存（詳細化）**:
   - 上位 Tier (N) の定義や要求は、下位 Tier (N+1) において具体化（詳細化）される。
   - 下位 Tier は上位 Tier のインターフェースや定数定義を明示的に参照して実装しなければならない。
2. **上り方向の依存禁止（カプセル化）**:
   - 上位 Tier (N) が、より下位の Tier (N+1, N+2) の具象ハードウェアや具体的な実装詳細に直接依存してはならない（抽象化漏れの禁止）。
   - 上位 Tier が下位の機能を利用する場合、必ず Tier 1 または Tier 2 で定義された抽象インターフェースを介し、静的DI（コンパイル時解決）の原則に従うこと。

### 2.2 階層検証における親子関係（ペア）の対応
階層検証では、以下のレイヤー間での一貫性とカプセル化違反を監査する。

* **Tier 1 検証**:
  - 親ドキュメント: `docs/requires/requirement_list.md` (Tier 0)
  - 子ドキュメント: `docs/components/tier1_core/*`, `docs/components/tier1_interface/*`
* **Tier 2 検証**:
  - 親ドキュメント: `docs/components/tier1_core/*`, `docs/components/tier1_interface/*`
  - 子ドキュメント: `docs/components/tier2_runtime/*`, `docs/components/tier2_jit/*`
* **Tier 3 検証**:
  - 親ドキュメント: `docs/components/tier2_runtime/*`, `docs/components/tier2_jit/*`
  - 子ドキュメント: `docs/components/tier3_platform/*`

---

## 3. ドキュメントの静的チェックルール

各設計書は、システムの一貫性を保つため、以下の静的チェックおよびフォーマットに適合しなければならない。

- **フォーマット適合**: `docs/components/FORMAT.md` に準拠し、C++コードブロックの直接記述の禁止（Pythonで疑似コードを記述）、`####` 見出しにおけるC++識別子のみの表記の禁止（自然言語を添える）等を守ること。
- **要求キーワード（Keyword）の紐付け**: 設計書内の各セクションは、末尾に要求キーワード（中括弧で囲まれたもの）を付与し、`requirement_list.md` に定義された要求仕様とのトレーサビリティを維持すること。

---

## 4. 特殊キーワードの分類と検証仕様

Fireball プロジェクトでは、一貫性・トレーサビリティ検証をノイズなく高速に実行するため、キーワードを以下の3つに分類して管理する。

### 4.1 分類基準と検証時の挙動

| 分類 | 命名規則 | 定義対象 | 検証時（tools）の挙動 |
| :--- | :--- | :--- | :--- |
| **メタキーワード** | `{META_[Name]}` | 検証ツール（doc_test_llm）の制御、およびLLMに適用する横断的な開発・コーディング規約。 | - 階層検証 (`--hierarchy`) の親子関係マッチングから除外される。<br>- 仕様書ペア整合性 (`S-ARCH-CHECKLIST`) の生成・検証から除外される。<br>- LLMのシステムポリシーとして読み込まれる。 |
| **グローバルキーワード** | `{GLOBAL_[Name]}` | システム全体（5つ以上の多数の仕様書）に適用される広域ポリシー、またはプラットフォーム要件。 | - 各仕様書の単体要求適合性 (`S-TRACE-ALIGN`) では**検証される**。<br>- 仕様書ペア整合性 (`S-ARCH-CHECKLIST`) では、重複・ノイズ削減のため**除外される**。 |
| **ローカルキーワード** | `{[Name]}` (プレフィックスなし) | 個別の機能、コンポーネント（スケジューラ、チャネル通信など）に閉じた具体的な要求。 | - すべての単体検証 (`S-TRACE-ALIGN`) およびペア整合性検証 (`S-ARCH-CHECKLIST`) の対象となる。 |

---

### 4.2 メタキーワード（共通非機能要件・設計方針）の定義

メタキーワードは、特定のコンポーネントや個別の機能に閉じない、システム横断的な非機能要求、アーキテクチャ設計方針、および共通の実装パターンを表す。

| キーワード | 説明 |
| :--- | :--- |
| `{META_3TierSeparation}` | アーキテクチャ、サブシステム、実装の3層に厳密に分離し、依存関係を管理する。 |
| `{META_ConfigurableSystem}` | ヘッダファイルのマクロ定義によりシステムパラメータを固定する。 |
| `{META_FaultIsolation}` | メモリパーティションにより、コンポーネント間の障害伝播を防止する。 |
| `{META_RecoveryStrategy}` | エラーコードの代わりに推奨されるリカバリー動作（Retry/Panic等）を返す。 |
| `{META_RestrictedPhysicalAccess}` | 物理リソースへのアクセスを許可テーブルで厳格に制限する。 |
| `{META_StaticDI}` | コンパイル時の設定により依存性を注入する。 |
| `{META_AI_Native_Dev}` | 定型的な実装はLLMを活用し、設計と検証の品質を重視する。 |
| `{META_Risk_Tiering}` | リスクベースの設計階層化。重要度や不確実性に応じて検証レベルを調整する。 |
| `{META_SpecificationFirst}` | 実装に先立ち、形式仕様や契約を定義する開発スタンス。 |
| `{META_ZeroOverhead}` | ゼロコスト抽象化。高性能組み込み向けC++デザイン。 |
| `{META_ZeroCostAbstraction}` | 抽象化のコストを実行時に支払わない。C++非仮想インターフェース等による最適化。 |
| `{META_Static_Resolution}` | 実行時に決定可能な事項はコンパイル時に決定し、オーバーヘッドを最小化する。 |
| `{META_CompileTimeValidation}` | 静的な型チェックやconstexprにより、コンパイル時に不正を検知する。 |
| `{META_NoStdVector}` | 動的な `std::vector` の使用を禁止し、固定長またはカスタムコンテナを使用する。 |
| `{META_BumpAllocator}` | メモリの断片化を防ぎ、コンパイル時または実行時に高速なメモリ割り当てを行うアロケータ。 |
| `{META_FlatMapIndexed}` | C++23 std::flat_map を用いて、データの順序維持と高速検索を最小限のメモリで実現する。 |
| `{META_BinarySearch}` | ソート済み配列に対する $O(\log N)$ の高速検索。flat_map の利用を推奨。 |
| `{META_AccessDictionary}` | データの索引化と、それを用いたランタイムアクセスの最適化。 |

---

### 4.3 グローバルキーワード（広域仕様・横断ポリシー）の定義

グローバルキーワードは、多数のコンポーネント仕様書（仕様書間の境界）で共有される広域的なポリシーやプラットフォーム要件を表す。

| キーワード | 説明 |
| :--- | :--- |
| `{GLOBAL_Policy_Memory}` | メモリ管理や割り当てに関する共通ポリシー。 |
| `{GLOBAL_StrictMemoryLimit}` | メモリの上限が厳格に制限された動作。 |
| `{GLOBAL_IndependentHeap}` | 各コンポーネントが互いに独立したヒープメモリ領域を確保する設計。 |
| `{GLOBAL_IdleDetection}` | アイドル状態の検出とパワーマネジメント制御。 |
| `{GLOBAL_PeriodicTask}` | 周期的に実行されるタスクスケジュール。 |
| `{GLOBAL_ComponentHarness}` | テストや検証を容易にするためのテストハーネス。 |
| `{GLOBAL_InterruptWakeup}` | 割り込み契機による待機解除・復帰処理。 |
| `{GLOBAL_UseCpp20Coroutine}` | C++20 コルーチンの使用方針。 |
| `{GLOBAL_UseCpp23Library}` | C++23 標準ライブラリ機能の使用方針。 |
| `{GLOBAL_StaticScalability}` | 静的にパラメータ化されたスケーラビリティ。 |

---

### 4.4 ルールスコープ（文書規約の適用範囲）

ルール本文の重複は、スコープが異なる場合に限り意図的とみなす。  
基本は `GLOBAL` と `LOCAL` に分け、必要に応じて `OVERRIDE` と `REFERENCE` を補助的に使う。

| スコープ | 定義 | 典型的な置き場所 | 重複の扱い |
| :--- | :--- | :--- | :--- |
| `GLOBAL` | リポジトリ横断の不変条件や共通方針。 | `CLAUDE.md`、`.claude/rules/development-policy.md`、コード系の共通規約。 | ローカル文書では要約または参照に留める。 |
| `LOCAL` | 特定のディレクトリや文書群に閉じる規約。 | `.claude/rules/documentation*.md`、`.claude/rules/format_*.md`、`docs/components/**`。 | 対象範囲内では直接記述してよい。 |
| `OVERRIDE` | `GLOBAL` を明示的に上書きする例外。 | 例外を定義する個別文書。 | 何を上書きするかを明記する。 |
| `REFERENCE` | 実体は別文書にあり、入口だけを示す。 | `README`、入口ガイド。 | 本文を繰り返さずリンクだけ置く。 |
