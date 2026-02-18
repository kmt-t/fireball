---
name: Code Generation
description: >
  JSON/WITからの自動生成、および公理的意味論（Hoare論理）に基づく契約主導設計（MDD）。
  WHEN: 命令セット定義, レジスタマップ生成, 契約定義（pre/post/inv）, スタブ生成
  SCOPE: コード生成と契約検証。
  RELATED: wasm_development
---

# Code Generator

WIT IDLからC++ヘッダを自動生成し、品質チェックまで一貫して実行するスキル。

## 1. 概要

**WIT-First開発**における自動生成ワークフローを提供します:
- WIT IDL → C++ヘッダの自動生成（wasm-tools使用）
- **契約の公理的導出**: `@pre`, `@post`, `@inv` からアサーションとテストケースを生成
- 生成コードの品質自動チェック（禁止パターン、命名規則）

## 2. 環境・前提条件

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。
- **Windowsユーザー**: お使いの環境で直接実行するのではなく、PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルに入り、そこからスクリプトを実行してください。

## 3. 使用方法 (Usage)

### 統合実行（推奨）

`docker-codegen.sh` を使用して、コンテナ内で安全に生成とチェックを行います。

```bash
# 全ワークフロー実行 (生成 -> チェック -> ビルドテスト)
bash .agent/skills/docker_workaround/scripts/docker-codegen.sh

# 特定のサブコマンド実行
bash .agent/skills/docker_workaround/scripts/docker-codegen.sh wit_check.sh
```

### 個別実行 (コンテナ内またはLinux/Mac)

```bash
# 生成のみ
bash .agent/skills/code_generator/workflows/wit_gen.sh

# チェックのみ
bash .agent/skills/code_generator/workflows/wit_check.sh
```

## 4. 生成スクリプト詳細

### wit_to_cpp.py

wasm-toolsベースのWIT→C++変換器。
- パッケージ全体処理
- 依存関係自動解決
- ビットフィールド対応（`@bitfield`）
- Contract抽出（`@pre/@post/@inv`）

## 5. 品質チェックスクリプト

### check_violations.py
禁止パターン検出: `void*`, `malloc/free`, `try/catch/throw` 等

### check_naming.py
命名規則検証: Type(`snake_case`), Enum(`UPPER_SNAKE_CASE`)

## 6. トラブルシューティング

**ERROR: wasm-tools not found**:
ホスト環境にツールがありません。`docker-codegen.sh` を使用してください。

**WIT構文エラー**:
`error: expected kebab-case identifier`: `device_id` を `device-id` に修正してください（WIT標準）。
