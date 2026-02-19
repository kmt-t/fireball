---
name: Code Generation
description: >
  JSON/WITからの自動生成、および公理的意味論（Hoare論理）に基づく契約主導設計（MDD）。
  WHEN: 命令セット定義, レジスタマップ生成, 契約定義（pre/post/inv）, スタブ生成
  SCOPE: コード生成と契約検証。
  RELATED: embedded_wasm_research
---

# Code Generator

WIT IDLからC++ヘッダを自動生成し、品質チェックまで一貫して実行するスキル。

## 1. 概要 (Overview / Benefits)

**WIT-First開発**における自動生成ワークフローを提供し、設計と実装の乖離を最小化します。

- **認知的一貫性の維持**: 「ドキュメントとコードのどちらが正しいか？」という迷いを排除します。WITを修正すれば、ヘッダと検証ルールが同期されます。
- **レビュー負荷の外部化**: `void*` の使用や命名規則の違反を機械が指摘するため、人間は設計の本質的な論理に集中できます。
- **インターフェースの強制**: 実装が設計（WIT）を裏切ることを防ぎ、コードの腐敗を食い止めます。
- **契約の公理的導出**: `@pre`, `@post`, `@inv` からアサーションとテストケースを生成します。

## 2. 環境・前提条件 (Prerequisites)

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../general_docker_run/SKILL.md) を参照してください。
- **Windowsユーザー**: PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルに入り、そこからスクリプトを実行してください。

## 3. 使用方法 (Usage)

### 統合実行（推奨）

`docker-generate-code.sh` を使用して、コンテナ内で安全に生成とチェックを行います。
ホスト環境のツール欠如（wasm-tools等）を気にせず、常に決定論的なコード生成が可能です。

```bash
# 全ワークフロー実行 (生成 -> チェック -> ビルドテスト)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh

# 特定のサブコマンド実行 (例: 生成のみ)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh generate-code.sh

# 特定のサブコマンド実行 (例: 品質チェックのみ)
bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh check-quality.sh

# 特定のインターフェースのみをパイプで検証
ls wit/jit.wit | bash .agent/skills/general_docker_run/scripts/docker-generate-code.sh generate-code.sh
```

### 個別実行 (コンテナ内またはLinux/Mac)

```bash
# 生成のみ
bash .agent/skills/project_code_generate/workflows/generate-code.sh

# チェックのみ
bash .agent/skills/project_code_generate/workflows/check-quality.sh
```

## 4. 構成要素の詳細 (Component Details)

### scripts/
- **[generate_cpp.py](file:///w:/mysrc/fireball/.agent/skills/project_code_generate/scripts/generate_cpp.py)**: wasm-toolsベースのWIT→C++変換器。
  - 依存関係自動解決、`@bitfield` 対応、`@constexpr` 対応。
- **[check_violations.py](file:///w:/mysrc/fireball/.agent/skills/project_code_generate/scripts/check_violations.py)**: 禁止パターン（`void*`, `malloc`等）の検出。
- **[check_naming.py](file:///w:/mysrc/fireball/.agent/skills/project_code_generate/scripts/check_naming.py)**: 命名規則（`snake_case`等）の検証。

### workflows/
- **[generate-code.sh](file:///w:/mysrc/fireball/.agent/skills/project_code_generate/workflows/generate-code.sh)**: コード生成のメインワークフロー。
- **[check-quality.sh](file:///w:/mysrc/fireball/.agent/skills/project_code_generate/workflows/check-quality.sh)**: 品質チェックのメインワークフロー。

## 5. 品質・検証ルール (Quality & Validation)

本スキルによって生成・検証されるコードは以下のルールを遵守します。

- **禁止パターン**: `void*`, `malloc/free`, `try/catch/throw` 等の使用はエラーとなります。
- **命名規則**: 型名は `snake_case`、列挙型は `UPPER_SNAKE_CASE` である必要があります。
- **アノテーション**: `constexpr` 等の特殊なアノテーションが正しく展開されているか検証されます。

## 6. トラブルシューティング (Troubleshooting)

**ERROR: wasm-tools not found**:
ホスト環境にツールがありません。`docker-generate-code.sh` を使用してください。

**WIT構文エラー**:
`error: expected kebab-case identifier`: `device_id` を `device-id` に修正してください（WIT標準）。
