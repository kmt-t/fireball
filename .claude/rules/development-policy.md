# Fireball 開発ガイド (Development Guide)

Fireballプロジェクトにおける開発方針、プロセス、および各種ルールをここに集約する。

## 1. 開発方針 (Development Policy)

極限環境（RAM 32KB - 64KB）で動作する高性能WASM JITランタイムを実現するため、以下の原則を遵守する。

- **Specification-First (仕様第一)**: 実装に先立ち、`docs/components/` 以下に詳細な仕様書を作成する。
- **WIT as Single Source of Truth (WIT真実在)**: コンポーネント間のインターフェースは WIT (WebAssembly Interface Types) を唯一の正解とし、ここから設計を開始する。
- **Zero-Cost Abstraction (ゼロコスト抽象化)**: C++23 (flat_map等), C++23 Concepts, constexpr を活用し、実行時のオーバーヘッドを排除する。
- **Strict Memory Policy (厳格なメモリ管理)**: 動的メモリ確保（ヒープ）を原則禁止し、静的またはスタック割り当てを優先する。 `{Policy_Memory}`
- **Code Size Constraint (15KLOC制約)**: 全体のコード規模を 15,000行 (SLOC) 以内に収める。
- **Bonsai Design (盆栽デザイン)**: 全体のバランスを見ながら、設計の密度を段階的に（Phase/Stepごとに）上げていく。

## 2. 開発プロセス (Development Process)

開発は以下の 4 ステップを 1 サイクルとして進める。

### Step 0: Bonsai Design (盆栽デザイン)
- `docs/components/*.md` に仕様書を記述する。
- Mermaid を使用して SysML 形式（BDD/SD/SMD/PAR）で設計を可視化する。
- `docs/components/CHECKLIST.md` に基づき、エージェントがセルフレビューを行う。

### Step 1-2: Formal Verification (TLA+/TLC)
- インターフェースを WIT で定義する。
- **TLA+** を用いてモデルを記述し、**TLC** で不変条件 (ATC: @pre, @post, @inv) や動的振る舞いの論理的な一貫性を検証する。

### Step 3: Implementation Generation (実装生成)
- WIT から C++ コード（Harness, Interface）を自動生成する。
- コンポーネントのロジックを実装する。LLMを積極的に活用し、定型コードの生成を自動化する。 `{AI_Native_Dev}`

### Step 4: Testing & Integration (テスト・統合)
- ホスト環境およびターゲット環境（Cortex-M等）でのテストを実行する。
- `docs/backlog/backlog_list.md` のストーリーに基づき、価値の提供を確認する。

## 3. エージェント向け運用ルール

- **セルフレビュー**: ドキュメントの修正後は必ず `docs/components/CHECKLIST.md` を確認すること。
- **TODO管理**: 未決定事項は `TODO(Phase X): [課題] - [アクション]` の形式で明示する。
- **トレーサビリティ**: 要求仕様（`docs/requires/requirement_list.md`）のキーワード `{Keyword}` をドキュメント内に記述し、紐付けを維持する。
- **質問の推奨**: 仕様の不確実性は憶測で埋めず、積極的にユーザーへ質問すること。
- **TLA+/TLCの活用**: 複雑な状態遷移や所有権の移譲については、TLA+によるモデル化とTLCによる検証を提案または実施すること。
