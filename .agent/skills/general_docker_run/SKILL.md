---
name: Docker Workaround
description: >
  Docker Composeを使用して安定した開発環境を構築し、ツールを実行する手順。
  WHEN: devcontainerが動かない, コンテナ内ツール必要, WSL2 Bash使用
  SCOPE: Docker Composeによるワークフロー。
  RELATED: project_code_generate（WIT自動生成）, fireball_architecture（ビルドシステム）
---

# Docker Workaround スキル

## 概要

`docker_workaround` スキルは、環境の不確実性を排除し、全ツール群を決定論的なコンテナ内で実行するための「レバレッジ・ポイント」です。
VSCodeのdevcontainer機能が不安定な場合や、外部からコンテナ内ツールを利用したい場合に、`docker-compose` を使用して手動で開発環境を立ち上げ、操作します。

### 本スキルを使用するメリット (Leverage)

- **「自分の環境では動く」の排除**: ツールチェーンの違いによる微細な挙動の差という認知ノイズを消し、純粋なロジックに集中できます。
- **手数の集約**: `tlc`, `wasm-tools`, `clang` 等の重量級ツールを個別に管理せず、1 つのランナー経由で統一的に操作します。
- **ツールの聖域化**: ホスト環境を汚さず、隔離されたクリーンな環境で検証を実行します。
- **環境トラブルの 0 化**: Windows/VHDX 環境特有の Docker トラブルに対する最短の解決パスを提供し、開発の「詰まり」を解消します。

## 1. 環境の起動

**Note for Windows Users**:
Windows環境では、PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルに入り、そこから以下のコマンドを実行してください。PowerShell上で直接実行するとパス変換の問題により動作しない場合があります。

プロジェクトルートで実行:

```bash
# コンテナをバックグラウンドで起動
# -f オプションで .devcontainer 内のファイルを指定します
docker compose -f .devcontainer/docker-compose.yml up -d

# 状態確認
docker compose -f .devcontainer/docker-compose.yml ps
```

サービス名: `fireball-dev`

## 2. ツールの実行 (Recommended)

`docker compose exec` を使用して、起動中のコンテナ内でコマンドを実行します。
ユーザーは `developer` として実行されます。

> [!TIP]
> **Execution Context**:
> `.agent/skills/*/scripts/docker-*.sh` は、**ホスト側 (WSL2 Bash)** からの実行を想定したラッパーです。
> すでにコンテナ内 (devcontainer ターミナル等) にいる場合は、これらのラッパーを使用せず、直接対象のコマンドやスクリプトを実行してください。

### Explorer (Code Analysis)
コードベースの探索や要約を行います。

```bash
# ヘルパースクリプト (推奨)
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh summary references/webassembly/README.md

# または汎用コマンドランナー
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh ls -la
```

### Generic Command Runner (Recommended)
任意のコマンドをコンテナ内で実行するための汎用ラッパースクリプトです。

```bash
# 基本的な使い方
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh <command> <args>

# 例: find コマンド (ソースコードのスキャン等)
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh find src -name "*.cxx"

# 例: パイプを使用した explorer との連携 (バッチ解析)
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh find src -name "*.hxx" | xargs -I {} bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh summary {}

# 例: make / meson (ビルドコマンド)
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh meson test -C build
```
```

### Code Generator (WIT to C++)
WIT IDLからC++ヘッダを生成します。

```bash
# ヘルパースクリプト (推奨)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh

# 特定のワークフローを実行
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh check-quality.sh
```

### TLA+ 検証 (TLC)
形式仕様のモデル検査を行います。

```bash
# ヘルパースクリプト (推奨)
bash .agent/skills/general_docker_run/scripts/docker-check-tla.sh tla/coos.tla
```

### Friction Audit (Documentation Check)
ドキュメントの整合性をチェックします。

```bash
# ヘルパースクリプト (推奨)
bash .agent/skills/general_docker_run/scripts/docker-audit-friction.sh
```

### Meson ビルド
プロジェクトのビルドとテストを行います。

```bash
# セットアップ
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev //bin/bash -c "cd /workspaces/fireball && meson setup build"

# ビルド
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev //bin/bash -c "cd /workspaces/fireball && ninja -C build"

# テスト
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev //bin/bash -c "cd /workspaces/fireball && meson test -C build"
```

## 3. コンテナへのシェルアクセス

対話的な作業が必要な場合:

```bash
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev //bin/bash
```

## 4. 環境の停止

作業終了後:

```bash
docker compose -f .devcontainer/docker-compose.yml down
```

## 5. トラブルシューティング

### ポート競合等で起動しない場合

```bash
docker compose -f .devcontainer/docker-compose.yml down
docker system prune  # 注意: 未使用のリソースが削除されます
docker compose -f .devcontainer/docker-compose.yml up --build -d
```

### コンテナ内でマウントが空 (Windows)

**症状**: `docker compose up -d` 後、コンテナ内の `/workspaces/fireball` が空。

**原因**: N:ドライブ等のVHDX仮想ドライブの場合、**Dockerより先にVHDXをマウント**する必要がある。Docker起動時にドライブが認識されていないとマウントに失敗する。

**解決策**:
1. VHDXドライブをマウント
2. **その後** Docker Desktopを起動
3. `docker compose up -d` 実行

### `.devcontainer` ディレクトリでの実行に関する注意

`docker-compose.yml` 内の相対パス (`..:/workspaces/fireball`) は、YMLファイルの場所を基準に解決されます。
そのため、プロジェクトルートから `-f .devcontainer/docker-compose.yml` を指定して実行するのが最も確実です。
