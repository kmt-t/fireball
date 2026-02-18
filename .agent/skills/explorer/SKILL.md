---
name: Codebase Explorer
description: インタラクティブにコードベースを探索し、構造把握、シンボル要約、文脈解析 (Context Analysis) を行う統合ツール。
WHEN: 構造把握、関数追跡、シンボル一覧取得、キーワード文脈理解（文脈集約）が必要な時
SCOPE: プロジェクト全域
RELATED: friction_audit, docker_workaround
PROTOCOL: 検索時は「自己流の単語」を避け、「設計ドキュメント内に明示されている用語」を使用すること。
---

# Codebase Explorer

## 1. 概要 (Overview)
インタラクティブにコードベースを探索し、構造把握、シンボル要約、文脈解析 (Context Analysis) を行う統合ツール。
本スキルは、エージェントのワーキングメモリを保護し、大規模なコードベースを構造的に把握することを目的とする。

## 2. 標準的な実行手順 (Standard Invocations)

Windows環境ではパス解釈やクオーティングの差異によりエラーが発生しやすいため、以下の標準エントリポイントを必ず遵守せよ。

### A. コンテナ内解析 (推奨)
Clang AST解析やコンテナ環境ツールを使用する場合。
```bash
# 基本形:
bash .agent/skills/explorer/docker-explorer.sh <command> [args...]

# 例: AST解析 (JSON)
bash .agent/skills/explorer/docker-explorer.sh ast path/to/file.hxx --json -Iinc
```

### B. ホスト解析 (WSL2 Bash)
高速なテキスト検索やドキュメント要約に使用。
```bash
# PowerShell から bash と打って入った後、または wsl bash -c で実行:
bash .agent/skills/explorer/scripts/explorer.sh summary docs/architecture/
```

### C. コンテナ内からの直接実行
devcontainer 内のターミナルまたは `docker exec` で入った後は、ラッパーを介さず直接実行できます。

```bash
# コンテナ内蔵の python を使用:
python3 .agent/skills/explorer/scripts/explorer.py summary src/
```

## 3. 解析サブコマンド
- `summary <path>`: ファイルまたはディレクトリのシンボルツリーと骨格を抽出。
- `ast <path>`: Clang AST をダンプ。`--json` フラグ併用可能。
- `callers <symbol>`: 指定したシンボルの呼び出し元を再帰的に探索。

## 4. 設計情報の抽出
設計書 (`docs/*.md`) を解析する際、AIは自然言語から設計不変条件やインターフェース契約を自律的に抽出すること。
詳細は `scripts/README.md` を参照せよ。

### シンボル俯瞰 (Summary)

```bash
bash .agent/skills/explorer/scripts/explorer.sh summary src/main.cxx
```

## 6. 高度な利用方法: バッチ処理 (Batch Processing)

本ツールは標準的な Unix パイプを介した一括処理が可能です。`docker-cmd.sh` でファイルを抽出し、`xargs` を介して `docker-explorer.sh` に渡すことで、大規模なコードベースを高速に要約できます。

```bash
# 例: src ディレクトリ内のすべての .cxx ファイルを 5 つまでバッチ要約
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find src -name "*.cxx" | head -n 5 | xargs -I {} bash .agent/skills/explorer/scripts/docker-explorer.sh summary {}
```

---

## 7. トラブルシューティング & フリクション (Troubleshooting)

- **Windows シェル環境の不備**: PowerShell や CMD ではクオーティング不備や `find` コマンドの挙動差異（Windows版 `find` が呼ばれる等）により、スクリプトが誤作動することがあります。
  - **解決策**: 常に **WSL2 Bash** を使用してスクリプトを起動してください（PowerShell から `bash` と入力して入るのが最も容易です）。
  - **解決策**: ファイル探索にはシステム標準の `find` ではなく、`grep_search` や本スキルの `summary` を優先的に使用してください。

---
