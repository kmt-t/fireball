# Docker Workaround Scripts

Docker CLI経由でコンテナ内のツールを実行するヘルパースクリプト集。

---

## スクリプト一覧

### docker-explorer.sh

コードベースの探索・解析ツール。

```bash
# Markdownファイルの要約
bash docker-explorer.sh summary docs/README.md

# ソースコードのパイプ解析
bash docker-find.sh src -name "*.cxx" | bash docker-explorer.sh pipe summary
```

### docker-codegen.sh

WIT IDLからのコード生成。

```bash
# 全ワークフロー実行
bash docker-codegen.sh
```

### docker-friction.sh

ドキュメント整合性チェック（Friction Audit）。

```bash
bash docker-friction.sh
```

### docker-tlc.sh

TLA+ モデル検査。

```bash
bash docker-tlc.sh tla/spec.tla
```

### docker-cmd.sh

任意のコマンドをコンテナ内で実行する汎用ラッパー。

```bash
# find コマンド (パイプ用)
bash docker-cmd.sh find docs -name "*.md"

# ビルドコマンド
bash docker-cmd.sh meson test -C build
```

---

## 前提条件

1. **Dockerコンテナが動作可能であること**
   スクリプトは自動的に `fireball-dev` コンテナを起動しようと試みます。

2. **WSL2 Bash使用（Windows）**
   - **PowerShell上で直接実行するのは非推奨です**。パス変換の問題を回避するため、必ず PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルに入ってから実行してください。

3. **プロジェクトルートからのパス依存**
   スクリプトは `.agent/skills/docker_workaround/scripts/` にありますが、リポジトリ内のどこから呼び出しても動作するように設計されています（内部でルートを解決）。ただし、引数のパスはカレントディレクトリ相対で指定してください。

