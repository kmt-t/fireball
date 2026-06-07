# Fireball 共有ガイド

## 正本

- `docs/requires/requirement_list.md`: 要求仕様の正本
- `docs/architecture/document_structure.md`: 文書階層、メタキーワード、traceability の正本
- `docs/plans/backlog_list.md`: 現在の作業単位
- `docs/plans/roadmap_phase.md`: 全体フェーズ
- `docs/components/**`: コンポーネント設計
- `.claude/rules/**`: 開発ルール
- `tools/README.md` / `verify/README.md`: 検証入口

## 作業前に読むもの

- 変更前に関連する既存ルールを読む。
- 迷ったら `docs/plans/backlog_list.md` と `docs/plans/roadmap_phase.md` を確認する。
- 仕様変更時は `docs/components/`、`docs/requires/`、`verify/` を必要に応じて更新する。
- 仕様、計画、検証に触れる変更では、`docs/architecture/document_structure.md` に従って `{Keyword}` の紐付けを保つ。
- ルールの再掲は `GLOBAL` / `LOCAL` のスコープ差がある場合のみ意図的とみなす。

## 主要ルール

- C++ は 2 スペース、100 桁、snake_case を基本にする。
- 公開 API は `fireball` 名前空間に置く。
- ヘッダは `.hxx`、C++ は `.cxx`、C は `.c` を使う。
- 組み込みコードは静的/スタック主体とし、`malloc` / `new` / `void*` / 例外 / RTTI を避ける。
- ドキュメント本文は日本語、コード名・API 名・キーワード・URI は英語。
- 図は Mermaid、表は Markdown を優先する。

## 検証

- 具体的な検証コマンドは `tools/README.md` と `verify/README.md` を正本とする。

## エージェント入口

- Claude Code 用の入口は `CLAUDE.md`。
- Codex 用の入口は `AGENTS.md`。
- Antigravity 用の入口は `.agents/rules/fireball.md`。
