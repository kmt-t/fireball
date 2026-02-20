---
name: Docker Workaround
description: >
  Dockerを使用して安定した開発環境を構築し、ツールを実行する手順。
  WHEN: devcontainerが動かない, コンテナ内ツール必要, WSL2 Bash使用
  SCOPE: Dockerによるワークフロー。
  RELATED: project_code_generate, general_codebase_explore, project_friction_audit
---

# Docker 実行ワークアラウンド スキル設計書

環境の不確実性を排除し、全ツール群を決定論的なコンテナ内で実行するための統合基盤スキルです。

## 1. 概要

「自分の環境では動く」という認知ノイズを消し、純粋なロジックに集中するための「レバレッジ・ポイント」を提供します。

- **決定論的実行**: ツールチェーンの微細なバージョン差異を Docker によって吸収し、常に一貫した結果を保証します。
- **手数の集約**: `wasm-tools`, `clang`, `tlc` 等の重量級ツールを個別にホストへインストールせず、統一的に操作します。
- **環境の聖域化**: ホスト環境を汚さず、隔離されたクリーンな環境で検証を実行します。

## 2. 環境・前提条件

- **Docker Desktop**: Docker エンジンが動作していること。
- **WSL2 Bash Windows**: PowerShell 上での直接実行はパス変換の問題があるため、必ず `bash` 上で実行してください。

## 3. 使用方法

各ラッパースクリプトを実行すると、必要に応じて自動的にイメージがビルドされ、コンテナが起動します。手動での `docker run` は通常不要です。

### 3.2 統合ランナーの使用 推奨

各スキルの動作をコンテナ経由で行うためのラッパースクリプトが提供されています。

```bash
# コード生成 all: generate -> check -> build
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh all

# コードベース探索 symbols
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh symbols src/main.cxx

# フリクション監査
bash .agent/skills/general_docker_run/scripts/docker-audit-friction.sh
```

### 3.3 汎用コマンドランナー

```bash
# 任意のコマンドをコンテナ内で実行
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh meson test -C build
```

## 4. 構成要素の詳細

### scripts/ ラッパー群
- **[docker-run-command.sh](.agent/skills/general_docker_run/scripts/docker-run-command.sh)**: 任意のコマンドをコンテナ内で実行。
- **[docker-generate-code.sh](.agent/skills/general_docker_run/scripts/docker-generate-code.sh)**: WIT コード生成ワークフロー。
- **[docker-explore-codebase.sh](.agent/skills/general_docker_run/scripts/docker-explore-codebase.sh)**: AST 解析・グラフ生成ワークフロー。

## 5. 品質・検証ルール

- **再現性の保証**: コンテナイメージのハッシュに基づく固定環境により、100% の再現性を担保します。
- **パス正規化**: ホスト側とコンテナ側のディレクトリマッピングを自動で整合させ、エージェントがパスの差異を意識しなくて済むように設計されています。

## 6. トラブルシューティング

**コンテナ内の `/workspaces/fireball` が空 Windows**:
N:ドライブ等の仮想ドライブを使用している場合、Docker Desktop の起動より先にドライブがマウントされている必要があります。ドライブ再接続後に Docker Desktop を再起動してください。

**Permission Denied**:
コンテナ内ツールは `developer` ユーザー UID 1000 で動作します。ホスト側のファイルの所有権が不適切な場合、`chown` で修正してください。
