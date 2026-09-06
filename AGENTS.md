# Fireball プロジェクトガイド

## 正本

- `docs/requires/requirement_list.md`: 要求仕様の正本
- `docs/architecture/document_structure.md`: 文書階層、メタキーワード、traceability の正本
- `docs/architecture/keyword_dictionary.md`: リンク用メタキーワード・アンカー台帳の正本
- `docs/plans/backlog_list.md`: 現在の作業単位
- `docs/plans/roadmap_phase.md`: 全体フェーズ
- `docs/components/**`: コンポーネント設計書（Tier 1〜3）
- `docs/components/<tier>/formal/**`: 形式検証モデル（pyModelChecking）
- `.agents/rules/**`: 開発・設計ルール
- `tools/README.md`: 検証入口（spec-integrator パイプライン）

## 作業前に読むもの

- 変更前に関連する既存ルール（`.agents/rules/**`）を読む。
- 迷ったら `docs/plans/backlog_list.md` と `docs/plans/roadmap_phase.md` を確認する。
- 仕様変更時は `docs/components/`、`docs/requires/` を必要に応じて更新し、形式検証モデル（`docs/components/<tier>/formal/`）を整合させる。
- 仕様、計画、検証に触れる変更では、`docs/architecture/document_structure.md` に従って `{Keyword}` の紐付けを保つ。
- ルールの再掲は `GLOBAL` / `LOCAL` のスコープ差がある場合のみ意図的とみなす。

## 主要ルール

- コンパイラは Clang 17+ 必須（`[[clang::musttail]]` による直接末尾呼び出し最適化前提、GCC/MSVC非サポート）。
- C++ は 2 スペース、100 桁、snake_case を基本にする。
- 公開 API は `fireball` 名前空間に置く。
- ヘッダは `.hxx`、C++ は `.cxx`、C は `.c` を使う。
- 組み込みコードは静的/スタック主体とし、`malloc` / `new` / `void*` / 例外 / RTTI を避ける。
- Python（シミュレータ・概念コード・形式検証・テスト）は `typing.Any` を完全禁止し、具体型・代数的データ型を用いる。
- ドキュメント本文は日本語（自然言語）、コード名・API 名・キーワード・URI は英語。
- 複雑な動的アルゴリズムの図は、責務重視＝シーケンス図（`sequenceDiagram`）、手順重視＝アクティビティ図（`flowchart TD`）とする。
- 形式検証は Python `pyModelChecking`（Kripke 構造・CTL/LTL）で記述・実行し、`guards=False` 変異検査を必須とする。

## 検証・フォーマット

- 具体的な検証コマンドは `tools/README.md` および `.agents/skills/document-validation/` を正本とする。
- **回帰テストは関係あるファイルのみに絞る**：変更したファイルおよび直接関連する単体テスト・概念コードのみを実行する。
- **自動フォーマット（コミット前実行）**：
  - ドキュメント: `powershell tools/format-doc.ps1` (Linux/WSL: `./tools/format-doc.sh`)
  - ソースコード: `powershell tools/format-src.ps1 -group <cpp|python|concepts|formal|pysim|all>` (Linux/WSL: `./tools/format-src.sh -g <group>`)
- **静的チェック・品質ゲート・サボり検証（コスト0）**：
  - ドキュメント (8大ゲート): `powershell tools/check-doc.ps1` (Linux/WSL: `./tools/check-doc.sh`)
  - ソースコード (規約・サボり・テスト): `powershell tools/check-src.ps1 -group <cpp|python|concepts|formal|pysim|all>` (Linux/WSL: `./tools/check-src.sh -g <group>`)
- **ドキュメントDB構築・キーワード抽出**：
  - Windows: `powershell tools/build.ps1`
  - Linux/WSL: `./tools/build.sh`
- **クラウド LLM 監査（API 課金、ユーザー明示指示時のみ）**：
  - 単語揺れ検査: `powershell tools/llm-word.ps1`（高速静的版: `-quick`）
  - キーワードリスク評価: `powershell tools/risk.ps1`
  - 単体ドキュメントレビュー: `powershell tools/llm-single-review.ps1 -file <path>`
  - 高リスク島レビュー: `powershell tools/llm-keyword-review.ps1`

## エージェント入口

- エージェント共通のルール正本は `AGENTS.md` および `.agents/rules/**`。
- 品質検証スキルは `.agents/skills/document-validation/` を参照。
- ドキュメントレビュースキル（仕様→形式検証→コード→テストの垂直一貫性・サブエージェント監査）は `.agents/skills/document-review/` を参照。
