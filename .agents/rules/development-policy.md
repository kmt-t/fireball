# Fireball 開発ガイド (Development Guide)

Fireball プロジェクトにおける開発方針、プロセス、および各種ルールをここに集約する。
文書階層、メタキーワード、traceability の正本は `docs/architecture/document_structure.md`、要求仕様の正本は `docs/requires/requirement_list.md` とする。
scope: GLOBAL

## 1. 開発方針 (Development Policy)

極限環境（RAM 32KB - 64KB）で動作する高性能 WASM JIT ランタイムを実現するため、以下の原則を遵守する。

- **Specification-First**: 実装に先立ち、対象領域の仕様を `docs/components/**` や `docs/requires/**` に記述する。
- **WIT as Single Source of Truth**: コンポーネント間のインターフェースは WIT を唯一の正解とし、設計と実装の起点にする。
- **Zero-Cost Abstraction (ゼロコスト抽象化)**: C++23、`constexpr`、C++23 Concepts を活用し、実行時のオーバーヘッドを排除する。
- **Strict Memory Policy `{Policy_Memory}`**: 動的メモリ確保（ヒープ）を原則禁止し、静的またはスタック割り当てを優先する。
- **Code Size Constraint (15KLOC制約)**: 全体のコード規模を 15,000 行 (SLOC) 以内に収める。
- **Bonsai Design (盆栽デザイン)**: 設計の密度は Phase/Step ごとに段階的に上げる。
- **Rule Independence**: ルール本文は個別ドキュメント名や本文例に依存させず、役割と分類を参照して記述する。

## 2. 開発プロセス (Development Process)

開発は以下の 4 ステップを 1 サイクルとして進める。

### Step 0: Bonsai Design (盆栽デザイン)
- 対象領域の仕様書群に仕様を記述する。
- Mermaid を使用して SysML 形式（BDD/SD/SMD/PAR）で設計を可視化する。

### Step 1: Formal Verification (pyModelChecking)
- インターフェースを WIT で定義する。
- Python **pyModelChecking** を用いてモデルを記述し、不変条件（Hoare Triple: `@pre`, `@post`, `@inv`）や動的振る舞いの論理的一貫性（CTL/LTL）を検証する。

### Step 2: Implementation Generation (実装生成)
- WIT から C++ コード（Harness, Interface）を自動生成する。
- コンポーネントのロジックを実装する。LLM を積極的に活用し、定型コードの生成を自動化する。 `{AI_Native_Dev}`

### Step 3: Testing & Integration (テスト・統合)
- ホスト環境およびターゲット環境（Cortex-M 等）でのテストを実行する。
- `tools/README.md` にある整合性・トレーサビリティ監査の入口を使って、機械チェックを実行する。
- `docs/plans/backlog_list.md` のストーリーに基づき、価値の提供を確認する。

## 3. エージェント向け運用ルール

- いかなる操作（実装、形式検証、ドキュメント修正）を開始する前にも、必ず `docs/plans/backlog_list.md` を読み、現在選択中のタスクがどのフェーズ・バックログアイテムに属するかを確認すること。
- `Step 2`（実装生成）を開始する前に、必ず前段の `Step 0-1`（設計・形式検証）がすべてのチェックリスト要件を満たしているかユーザーに明示的に確認すること。エージェント判断での自己完結的な実装開始を禁止する。
- 仕様・計画・検証に触れる変更では、`docs/architecture/document_structure.md` の定義に従って `{Keyword}` の traceability を維持すること。
- 変更した仕様は `docs/components/`、`docs/requires/`、`verify/` の対応箇所に反映すること。
- 不確実な仕様は憶測で埋めず、必要ならユーザーに質問すること。
- `TODO(未決): [課題] [アクション]` を TODO 管理の基本形式とする。フェーズ番号（Phase 1 等）は `docs/plans/**` にのみ記述し、他の文書やコードには書かない。
- 複雑な状態遷移や所有権の移譲については、pyModelChecking によるモデル化と検証を提案または実施すること。
- **仕様（ドキュメント）と実装（コンセプトコード・WIT・ステンシルカタログ等）にまたがる変更は、必ず同一コミットで両方を更新すること。** 片方だけを直すと、`judge`（LLM意味監査）は `docs/**/*.md` しか読まないため層間の矛盾を検出できず、「ドキュメント上は解決済みだが実装は未着手」という状態がそのまま緑のパイプラインとして通過する。実例と検出パターンは `.agents/rules/verification-antipatterns.md` パターンG（層間矛盾）を参照。
