# Docker Workaround Scripts

Docker CLI を経由して、コンテナ内の全ツール群（wasm-tools, clang, tlc 等）を決定論的に実行するためのヘルパースクリプト集。

## 1. 役割と数学的性質 (Role & Axioms)
- **目的**: ホスト環境（Windows/WSL2）に依存せず、常にプロジェクト規定のビルド環境（コンテナ）内でツールを実行することを保証する。
- **不変条件 (Invariants)**:
    - 実行時に自動的に `fireball-dev` コンテナの状態を確認し、停止している場合は起動を試みる。
    - コンテナ内の `/workspaces/fireball` がマウント済みであることを前提とし、環境変数や UID (1000) を透過的に管理する。
- **影響範囲 (Side Effects)**: Docker コンテナの起動・停止、およびコンテナ内でのファイル操作。

## 2. インターフェース (CLI & Interface)

### [docker-run-command.sh](file:///w:/mysrc/fireball/.agent/skills/general_docker_run/scripts/docker-run-command.sh)
`bash docker-run-command.sh <command> [args...]`
- **目的**: 任意のシェルコマンドをコンテナ内で実行。

### [docker-generate-code.sh](file:///w:/mysrc/fireball/.agent/skills/general_docker_run/scripts/docker-generate-code.sh)
`bash docker-generate-code.sh [workflow]`
- **目的**: WIT コード生成ワークフローの実行。

### [docker-explore-codebase.sh](file:///w:/mysrc/fireball/.agent/skills/general_docker_run/scripts/docker-explore-codebase.sh)
`bash docker-explore-codebase.sh <subcommand> [args...]`
- **目的**: AST 解析やコールグラフ生成の実行。

## 3. 使用方法 (Usage) サンプル

### パターンA: コンテナ内でのビルドとテスト
```bash
bash .agent/skills/general_docker_run/scripts/docker-run-command.sh meson test -C build
```

### パターンB: WIT 生成の全自動バッチ処理
```bash
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh
```

### パターンC: パイプによるバッチ解析
```bash
# ホスト側の find とコンテナ内の explorer を連携
find src -name "*.cxx" | xargs -I {} bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh summary {}
```

## 4. エラーリカバリ (Recovery)
- **Container not found**: `.devcontainer/docker-compose.yml` を使用して手動で `docker compose up -d` を実行してください。
- **Volume mount empty**: Windows 環境特有のマウントタイミングの問題です。[SKILL.md](../SKILL.md) のトラブルシューティングを参照してください。
