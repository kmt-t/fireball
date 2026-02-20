# Docker Workaround Scripts

Docker CLI を経由して、コンテナ内の全ツール群（wasm-tools, clang, tlc 等）を決定論的に実行するためのヘルパースクリプト集。

## 1. 役割と数学的性質
- **目的**: ホスト環境 Windows/WSL2 に依存せず、常にプロジェクト規定のビルド環境 コンテナ 内でツールを実行することを保証する。
- **不変条件 Invariants**:
    - 実行時に自動的に `fireball-dev` イメージと `fireball-dev-container` の状態を確認し、必要に応じてビルド・起動を行う。
    - ホストのプロジェクトルートはコンテナ内の `//workspace` にマウントされ、作業ディレクトリもそこに固定される。
    - コンテナ外（ホスト側）からは `docker-*.sh` スクリプトを通じて透過的にアクセスする。
- **影響範囲 Side Effects**: Docker コンテナの起動・停止、およびコンテナ内でのファイル操作。

## 2. インターフェース

### [docker-run-command.sh](.agent/skills/general_docker_run/scripts/docker-run-command.sh)
`bash docker-run-command.sh <command> [args...]`
- **目的**: 任意のシェルコマンドを `//workspace` 下で実行。

### [docker-generate-code.sh](.agent/skills/general_docker_run/scripts/docker-generate-code.sh)
`bash docker-generate-code.sh <subcommand> [args...]`
- **目的**: `run-codegen.sh` への委譲を通じたコード生成。例: `all`, `generate`, `check`, `build`.

### [docker-explore-codebase.sh](.agent/skills/general_docker_run/scripts/docker-explore-codebase.sh)
`bash docker-explore-codebase.sh <subcommand> [args...]`
- **目的**: `run-explorer.sh` への委譲を通じた AST 解析やシンボル抽出。例: `symbols`, `ast`, `callers`.

### [docker-check-cpp.sh](.agent/skills/general_docker_run/scripts/docker-check-cpp.sh)
`bash docker-check-cpp.sh [path...]`
- **目的**: 組み込み環境における C++ 制約（no-heap, no-exceptions等）の検証。

## 3. 使用方法

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

## 4. エラーリカバリ
- **Container/Image not found**: `docker-run-command.sh` が自動的に構築を試みます。手動で行う場合は `docker build -t fireball-dev -f .devcontainer/Dockerfile .` を実行してください。
- **Mount Issues**: コンテナ内の `//workspace` が空の場合、Docker 側のマウント権限や仮想ドライブの設定を確認してください。
