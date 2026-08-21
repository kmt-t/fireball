# Fireball プロジェクトガイド

## 正本

- `docs/requires/requirement_list.md`: 要求仕様の正本
- `docs/architecture/document_structure.md`: 文書階層、メタキーワード、traceability の正本
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

## 検証

- 具体的な検証コマンドは `tools/README.md` を正本とする。
  - Windows: `powershell tools/run_all_tests.ps1 -clean`
  - Linux/WSL: `./tools/run_all_tests.sh --clean`
  - 複雑度・リスク評価: `powershell tools/run_all_tests.ps1 -assess -backend sakura`
  - LLM 意味監査: `powershell tools/run_all_tests.ps1 -llm -backend sakura`

## エージェント入口

- エージェント共通のルール正本は `AGENTS.md` および `.agents/rules/**`。
- 品質検証スキルは `.agents/skills/document-validation/` を参照。

<!-- headroom:rtk-instructions -->
# RTK (Rust Token Killer) - Token-Optimized Commands

When running shell commands, **always prefix with `rtk`**. This reduces context
usage by 60-90% with zero behavior change. If rtk has no filter for a command,
it passes through unchanged — so it is always safe to use.

## Key Commands
```bash
# Git (59-80% savings)
rtk git status          rtk git diff            rtk git log

# Files & Search (60-75% savings)
rtk ls <path>           rtk read <file>         rtk grep <pattern>
rtk find <pattern>      rtk diff <file>

# Test (90-99% savings) — shows failures only
rtk pytest tests/       rtk cargo test          rtk test <cmd>

# Build & Lint (80-90% savings) — shows errors only
rtk tsc                 rtk lint                rtk cargo build
rtk prettier --check    rtk mypy                rtk ruff check

# Analysis (70-90% savings)
rtk err <cmd>           rtk log <file>          rtk json <file>
rtk summary <cmd>       rtk deps                rtk env

# GitHub (26-87% savings)
rtk gh pr view <n>      rtk gh run list         rtk gh issue list

# Infrastructure (85% savings)
rtk docker ps           rtk kubectl get         rtk docker logs <c>

# Package managers (70-90% savings)
rtk pip list            rtk pnpm install        rtk npm run <script>
```

## Rules
- In command chains, prefix each segment: `rtk git add . && rtk git commit -m "msg"`
- For debugging, use raw command without rtk prefix
- `rtk proxy <cmd>` runs command without filtering but tracks usage
<!-- /headroom:rtk-instructions -->
