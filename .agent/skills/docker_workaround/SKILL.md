---
name: Docker Workaround
description: >
  Docker Composeを使用して安定した開発環境を構築し、ツールを実行する手順。
  WHEN: devcontainerが動かない, コンテナ内ツール必要, Git Bash使用
  SCOPE: Docker Composeによるワークフロー。
  RELATED: code_generator（WIT自動生成）, fireball_architecture（ビルドシステム）
---

# Docker Workaround スキル

## 概要

VSCodeのdevcontainer機能が不安定な場合や、外部からコンテナ内ツールを利用したい場合に、
`docker-compose` を使用して手動で開発環境を立ち上げ、操作する手順。
`.devcontainer/docker-compose.yml` を使用します。

## 1. 環境の起動

プロジェクトルート（`n:\sources\fireball` または `/workspaces/fireball`）で実行:

```bash
# コンテナをバックグラウンドで起動
docker compose -f .devcontainer/docker-compose.yml up -d

# 状態確認
docker compose -f .devcontainer/docker-compose.yml ps
```

サービス名: `fireball-dev`

## 2. ツールの実行 (Recommended)

`docker compose exec` を使用して、起動中のコンテナ内でコマンドを実行します。
ユーザーは `developer` として実行されます。

### TLA+ 検証 (TLC)

```bash
# ヘルパースクリプトを使用 (推奨)
bash .agent/skills/docker_workaround/scripts/docker-run-tlc.sh tla/coos.tla

# または直接実行
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash -c "cd /workspaces/fireball && tlc tla/coos.tla"
```

### WIT 自動生成

```bash
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash -c \
  "cd /workspaces/fireball && python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen"
```

### Meson ビルド

```bash
# セットアップ
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash -c "cd /workspaces/fireball && meson setup build"

# ビルド
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash -c "cd /workspaces/fireball && ninja -C build"

# テスト
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash -c "cd /workspaces/fireball && meson test -C build"
```

## 3. コンテナへのシェルアクセス

対話的な作業が必要な場合:

```bash
docker compose -f .devcontainer/docker-compose.yml exec -u developer fireball-dev bash
```

## 4. 環境の停止

作業終了後:

```bash
docker compose -f .devcontainer/docker-compose.yml down
```

## トラブルシューティング

### ポート競合等で起動しない場合

```bash
docker compose -f .devcontainer/docker-compose.yml down
docker system prune  # 注意: 未使用のリソースが削除されます
docker compose -f .devcontainer/docker-compose.yml up --build -d
```
## 5. トラブルシューティング

### コンテナ内でマウントが空

**症状**: `docker compose up -d` 後、コンテナ内の `/workspaces/fireball` が空。

**原因**: N:ドライブ等のVHDX仮想ドライブの場合、**Dockerより先にVHDXをマウント**する必要がある。Docker起動時にドライブが認識されていないとマウントに失敗する。

**解決策**:
1. VHDXドライブをマウント
2. **その後** Docker Desktopを起動
3. `docker compose up -d` 実行

既にDocker起動済みの場合：
```bash
# Docker Desktop再起動
# または
docker compose down
docker compose up -d
```

### `.devcontainer` ディレクトリでの実行

`docker-compose.yml` 内の `volumes: - ..:/workspaces/fireball` は相対パス。**必ず `.devcontainer` ディレクトリで実行**すること：

```bash
cd .devcontainer
docker compose up -d
```

プロジェクトルートで実行すると、`..` が `n:\sources` になり誤動作する。
