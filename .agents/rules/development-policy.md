---
name: development-policy
description: Fireball プロジェクトの開発プロセス（盆栽デザイン）、ライフサイクル、運用原則
globs: ["**/*"]
scope: GLOBAL
---

# Fireball 開発ガイド

Fireball の開発方針と作業順序を定める。
文書階層、メタキーワード、traceability の正本は `docs/architecture/document_structure.md`、要求仕様の正本は `docs/requires/requirement_list.md` とする。

## 1. 開発方針 (Development Policy)

評価対象の最小構成は RAM 32KB / ROM 96KB である。容量要件は `docs/requires/requirement_list.md`、物理配置と適合性の評価状況は `docs/architecture/resource_budget_estimation.md` を正本とする。

- **Specification-First**: 実装に先立ち、対象領域の仕様を `docs/components/**` や `docs/requires/**` に記述する。
- **Bonsai Design (盆栽デザイン)**: 最初から過密な実装を行わず、仕様・検証・シミュレーション・本実装と段階的に密度を引き上げる。
- **Zero-Cost Abstraction (ゼロコスト抽象化)**: 言語機能やコンパイラ最適化を活用し、実行時のオーバーヘッドを排除する。
- **Strict Memory Policy `{GLOBAL_Policy_Memory}`**: `malloc` / `free` / `realloc` / `calloc` および通常の `new` / `delete` を禁止する。placement/in-place `new` と、プロジェクトで提供する独自ヒープ API・独自コンテナは許可する。標準の動的 STL コンテナは、システム提供アロケータを使用していても禁止する。
- **Code Size Constraint (20 KSLOC制約)**: コメントとテストコードを除く製品ソースコードを 20,000 行 (SLOC) 以内に収める。
- **Rule Independence**: ルールに一時的な計測値やツールの内部実装を複製しない。変わり得る値は正本を参照する。

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
  - コンポーネントのテスト仕様書（`docs/qa/tier*/*_test_spec.md`）を作成し、正常系・異常系・境界値・直交表組み合わせを網羅する。
- **形式検証 (`docs/components/<tier>/formal/*_model.py`)**:
  - Python `pyModelChecking` の Kripke 構造と CTL / LTL 式で、対象の不変条件を検証する。
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
- ドキュメント変更は連動修正を検査し、問題を解消した後にドキュメントトポロジーと一貫性ベースラインを同期する。
- **回帰テストは関係あるファイルのみに局所化して実行**する（コスト 0 のローカル検証）。
- クラウド LLM 監査は、ユーザーから明示的な指示があった場合のみ実行する（課金制御）。
- 検証の分類とコマンド入口は `tools/README.md`、個別ゲートの検査項目とオプションは `tools/spec-integrator/README.md` を正本とする。検証スキルは対象選定と結果確認の運用を示し、コマンド一覧を複製しない。

---

## 3. 作業時の確認

- 実装・仕様変更の着手時は `docs/plans/backlog_list.md` と `docs/plans/roadmap_phase.md` で作業の位置を確認する。
- `Step 3` の着手前に、対象範囲で `Step 0-2` の仕様・テスト設計・形式検証・Gotchas 還元が整っているか確認する。欠落があれば補うか、未決事項を明示する。
- 仕様・ドキュメント作成時は `.agents/rules/documentation-standards.md` を遵守し、上位要求とのトレーサビリティ `{Keyword}` を維持すること。
- 不確実な仕様は憶測で埋めず、必要ならユーザーに質問すること。
- `TODO(未決): [課題] [アクション]` を TODO 管理の基本形式とする。フェーズ番号（Phase 1 等）は `docs/plans/**` にのみ記述し、他の文書やコードには書かない。
- 仕様と実装・形式モデル・テストにまたがる変更は、対応する層を同じ変更単位で整合させる。層間矛盾の観点は `.agents/rules/verification-antipatterns.md` を参照する。
- **日常の検証はコスト 0 のローカル検証のみを実行すること。** クラウド LLM 監査（API 課金）はユーザーから明示的な指示があった場合のみ実行する。
