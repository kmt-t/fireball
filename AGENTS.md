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

- C++ は 2 スペース、100 桁、snake_case を基本にする。
- 公開 API は `fireball` 名前空間に置く。
- ヘッダは `.hxx`、C++ は `.cxx`、C は `.c` を使う。
- 組み込みコードは静的/スタック主体とし、`malloc` / `new` / `void*` / 例外 / RTTI を避ける。
- ドキュメント本文は日本語、コード名・API 名・キーワード・URI は英語。
- 図は Mermaid、表は Markdown を優先する。
- 形式検証は Python `pyModelChecking`（Kripke 構造・CTL/LTL）で記述・実行する。

## 検証・フォーマット

- 具体的な検証コマンドは `tools/README.md` および `.agents/skills/document-validation/` を正本とする。
- **回帰テストは関係あるファイルのみに絞る**：変更したファイルおよび直接関連する単体テスト・概念コードのみを実行する。
- **コード自動フォーマット（コミット前実行）**：
  - Windows: `powershell tools/format_all.ps1`
  - Linux/WSL: `./tools/format_all.sh`
- **表記揺れチェック（特出しスクリプト）**：
  - Windows: `powershell tools/check_terminology.ps1` （高速・静的版: `-quick`）
  - Linux/WSL: `./tools/check_terminology.sh` （高速・静的版: `--quick`）
- **普段（コミット前など）は簡易テスト（コスト0）のみ実行する**：
  - Windows: `powershell tools/run_all_tests.ps1` または単体 Python 実行
  - Linux/WSL: `./tools/run_all_tests.sh`
- **クラウド LLM 監査（API 課金）はユーザーから明示的な指示があった場合のみ実行する**：
  - マイルストーン監査（リスク評価 + 意味監査）: `powershell tools/run_all_tests.ps1 -level 2`
  - フル全量監査: `powershell tools/run_all_tests.ps1 -level 3`

## エージェント入口

- エージェント共通のルール正本は `AGENTS.md` および `.agents/rules/**`。
- 品質検証スキルは `.agents/skills/document-validation/` を参照。
