---
name: development-policy
description: Fireball プロジェクトの開発プロセス（盆栽デザイン）、ライフサイクル、運用原則
globs: ["**/*"]
scope: GLOBAL
---

# Fireball 開発ガイド (Development Guide)

Fireball プロジェクトにおける開発方針、プロセス、および各種ルールをここに集約する。
文書階層、メタキーワード、traceability の正本は `docs/architecture/document_structure.md`、要求仕様の正本は `docs/requires/requirement_list.md` とする。

## 1. 開発方針 (Development Policy)

極限環境（RAM 32KB - 64KB）で動作する高性能 WASM JIT ランタイムを実現するため、以下の原則を遵守する。

- **Specification-First**: 実装に先立ち、対象領域の仕様を `docs/components/**` や `docs/requires/**` に記述する。
- **Bonsai Design (盆栽デザイン)**: 最初から過密な実装を行わず、仕様・検証・シミュレーション・本実装と段階的に密度を引き上げる。
- **Zero-Cost Abstraction (ゼロコスト抽象化)**: 言語機能やコンパイラ最適化を活用し、実行時のオーバーヘッドを排除する。
- **Strict Memory Policy `{Policy_Memory}`**: 動的メモリ確保（ヒープ）を原則禁止し、静的またはスタック割り当てを優先する。
- **Code Size Constraint (15KLOC制約)**: 全体のコード規模を 15,000 行 (SLOC) 以内に収める。
- **Rule Independence**: ルール本文は個別ドキュメント名やツール実装詳細に依存させず、普遍的な原則・役割・分類を参照して記述する。

---

## 2. 開発プロセス (Development Process: 盆栽デザイン)

開発は以下の 5 ステップを 1 サイクルとして進める。

### Step 0: Bonsai Design (仕様策定・アーキテクチャ設計)
- 対象領域の仕様書群（`docs/components/**`）に仕様とアーキテクチャの骨格を記述する。
- 静的設計（データ構造・内部ブロック図）と動的設計（状態遷移・アルゴリズム図）を必ずセットで定義する。
- 詳細な記述様式、自然言語規則、Mermaid 図の使い分け（シーケンス図 vs アクティビティ図）は `.agents/rules/documentation-standards.md` を遵守する。

### Step 1: Early Validation (コンセプトコード・テスト設計・形式検証)
- **コンセプトコード (`concepts/*_concept.py`)**:
  - アルゴリズムの参照実装を Python で記述し、ロジックの成立性を確認する。言語・型規約は `.agents/rules/coding-standards-python.md` を厳格に遵守する。
- **テスト設計 (テスト仕様書)**:
  - コンポーネントのテスト仕様書（`tests/*_test_spec.md`）を作成し、正常系・異常系・境界値・直交表組み合わせを網羅する。
- **形式検証 (`formal/*_model.py`)**:
  - Python `pyModelChecking`（Kripke 構造・CTL 論理式）により、デッドロック不在、二重所有不在、リソース有界性等の不変条件を数学的に証明する。
  - **ガード無効化（`guards=False`）時の変異検査による反証性の担保を必須**とする。

### Step 2: Reference Simulation & Gotchas Feedback (勘所の抽出とテスト還元)
- 参照シミュレータ（`experiments/pysim` 等）や結合プロトタイピングを実行・検証する。
- 実行から得られた**「実装の勘所（Gotchas・不変条件・コーナーケースの落とし穴）」を体系的に抽出し、テスト仕様書（テスト設計）およびテストコードへフィードバック・還元**する。
- 仕様書とテストの双方に Gotchas（固有識別子と設計理由）を明記し、リグレッションテストを整備する。

### Step 3: Production Implementation (プロダクション本実装)
- テスト設計と仕様の裏付けをもとに、プロダクションコード（`inc/`, `src/`）を実装する。
- 詳細な言語規約、コンパイラ要件、組み込みメモリ制約については、`.agents/rules/coding-standards-cpp.md`（C++コーディング標準）を厳格に遵守する。

### Step 4: Automated Verification Pipeline (検証パイプライン・統合)
- コミット前にコード自動フォーマッタを実行し、スタイル準拠を保証する。
- ドキュメント検証パイプラインを実行して、ドキュメントトポロジーと一貫性ベースラインを同期する。
- **回帰テストは関係あるファイルのみに局所化して実行**する（コスト 0 のローカル検証）。
- クラウド LLM 監査は、ユーザーから明示的な指示があった場合のみ実行する（課金制御）。
- 具体的な実行コマンドやスクリプト呼び出し手順は、ルールではなく検証スキル（`.agents/skills/document-validation/`）および `tools/README.md` を正本とする。

---

## 3. エージェント向け運用ルール

- いかなる操作（実装、形式検証、ドキュメント修正）を開始する前にも、必ず `docs/plans/backlog_list.md` を読み、現在選択中のタスクがどのフェーズ・バックログアイテムに属するかを確認すること。
- `Step 3`（プロダクション本実装）を開始する前に、必ず前段の `Step 0-2`（設計・テスト設計・形式検証・Gotchas還元）がチェックリスト要件を満たしているかユーザーに明示的に確認すること。エージェント判断での自己完結的な実装開始を禁止する。
- 仕様・ドキュメント作成時は `.agents/rules/documentation-standards.md` を遵守し、上位要求とのトレーサビリティ `{Keyword}` を維持すること。
- 不確実な仕様は憶測で埋めず、必要ならユーザーに質問すること。
- `TODO(未決): [課題] [アクション]` を TODO 管理の基本形式とする。フェーズ番号（Phase 1 等）は `docs/plans/**` にのみ記述し、他の文書やコードには書かない。
- **仕様（ドキュメント）と実装（コンセプトコード・形式検証モデル・テスト等）にまたがる変更は、必ず同一コミットで両方を更新すること。** 片方だけを直すと層間矛盾（アンチパターン G）を引き起こす。詳細は `.agents/rules/verification-antipatterns.md` を参照。
- **日常の検証はコスト 0 のローカル検証のみを実行すること。** クラウド LLM 監査（API 課金）はユーザーから明示的な指示があった場合のみ実行する。
