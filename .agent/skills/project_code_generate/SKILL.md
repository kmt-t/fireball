---
name: Code Generation
description: >
  JSON/WITからの自動生成、および公理的意味論（Hoare論理）に基づく契約主導設計（MDD）。
  WHEN: 命令セット定義, レジスタマップ生成, 契約定義（pre/post/inv）, スタブ生成
  SCOPE: コード生成と契約検証。
  RELATED: embedded_wasm_research
---

# コード自動生成 スキル設計書

WIT IDLからC++ヘッダを自動生成し、品質チェックまで一貫して実行するスキル。

## 1. 概要

**WIT-First開発**における自動生成ワークフローを提供し、設計と実装の乖離を最小化します。

- **認知的一貫性の維持**: 「ドキュメントとコードのどちらが正しいか？」という迷いを排除します。WITを修正すれば、ヘッダと検証ルールが同期されます。
- **レビュー負荷の外部化**: `void*` の使用や命名規則の違反を機械が指摘するため、人間は設計の本質的な論理に集中できます。
- **インターフェースの強制**: 実装が設計（WIT）を裏切ることを防ぎ、コードの腐敗を食い止めます。
- **契約の公理的導出**: `@pre`, `@post`, `@inv` からアサーションとテストケースを生成します。

## 2. 環境・前提条件

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](.agent/skills/general_docker_run/SKILL.md) を参照してください。
- **Windowsユーザー**: PowerShell から `bash` と入力して **WSL2 Ubuntu** シェルに入り、そこからスクリプトを実行してください。

## 3. 使用方法

### 統合実行 推奨

`docker-generate-code.sh` を使用して、コンテナ内で安全に生成とチェックを行います。
全てのサブコマンドは `codegen.sh` に委譲されます。

```bash
# 全ワークフロー実行 (生成 -> チェック -> ビルドテスト)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh all

# 特定のサブコマンド実行 (例: 生成のみ)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh generate

# 特定のサブコマンド実行 (例: 品質チェックのみ)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh check
```

### 個別実行 コンテナ内またはLinux/Mac

```bash
# サブコマンドのヘルプ表示
bash .agent/skills/project_code_generate/scripts/run-codegen.sh --help

# 生成のみ
bash .agent/skills/project_code_generate/scripts/run-codegen.sh generate
```

## 4. 構成要素の詳細

### scripts/
- **[run-codegen.sh](.agent/skills/project_code_generate/scripts/run-codegen.sh)**: コード生成ワークフローの統合エントリポイント ディスパッチャー。
- **[generate_cpp.py](.agent/skills/project_code_generate/scripts/generate_cpp.py)**: wasm-toolsベースのWIT→C++変換器。
- **[check_violations.py](.agent/skills/project_code_generate/scripts/check_violations.py)**: 禁止パターンの検出。
- **[check_naming.py](.agent/skills/project_code_generate/scripts/check_naming.py)**: 命名規則の検証。

## 5. 品質・検証ルール

本スキルによって生成・検証されるコードは以下のルールを遵守します。

- **禁止パターン**: `void*`, `malloc/free`, `try/catch/throw` 等の使用はエラーとなります。
- **命名規則**: 型名は `snake_case`、列挙型は `UPPER_SNAKE_CASE` である必要があります。
- **アノテーション**: `constexpr` 等の特殊なアノテーションが正しく展開されているか検証されます。

## 6. トラブルシューティング

**ERROR: wasm-tools not found**:
ホスト環境にツールがありません。`docker-generate-code.sh` を使用してください。

**WIT構文エラー**:
`error: expected kebab-case identifier`: `device_id` を `device-id` に修正してください WIT標準。
