---
name: WASM Development
description: >-
  WebAssembly/WASI仕様, WAMR実装, LLVMバックエンド定義の参照パスと調査手順。
  WHEN: WASM命令の挙動確認, バイナリフォーマット調査, WASI API設計, JITバックエンド実装
  SCOPE: 外部リファレンスへのナビゲーション。Fireball固有の設計判断はfireball_architectureを参照。
  RELATED: project_code_generate, project_arch_design
---

# WASM 開発・調査 スキル設計書

WebAssemblyエコシステム（Spec, WASI, WAMR, LLVM）に関連するリソースを参照し、設計・実装を支援するための統合スキルです。

## 1. 概要

Fireballプロジェクトは WebAssembly を実行エンジンとして採用しており、外部仕様（標準）と実装（WAMR/LLVM）の深い理解が不可欠です。

- **正確な仕様準拠**: `references/` 下の Core Spec を参照することで、曖昧な実装を排除します。
- **実装リファレンスの活用**: WAMR のコードを調査し、JIT/Interpreter の設計判断材料を得ます。
- **ハードウェアマッピング**: LLVM Backend の定義（`.td`）から、特定アーキテクチャへの命令展開規則を導出します。

## 2. 環境・前提条件

- **リファレンスデータ**: `references/` ディレクトリ内に各仕様・ソースが配置されている必要があります。
- **探索ツール**: Clang AST解析などを行う場合は Docker コンテナが必要です。詳細は [Docker Workaround](.agent/skills/general_docker_run/SKILL.md) を参照してください。

## 3. 使用方法

### 仕様・コードの調査

| 対象 | 参照パス Base Path | 主な調査方法 |
| :--- | :--- | :--- |
| **WASM Core Spec** | `references/webassembly/document/core/` | 命令のセマンティクス（`exec/`）やバイナリ構造（`binary/`）の確認。 |
| **WASI Spec** | `references/wasi/` | `proposals/` 下の `.wit` および `.md` で API 契約を確認。 |
| **WAMR Impl** | `references/wamr/` | `core/iwasm/` 下のソースを `run-explorer.sh` で解析。 |
| **LLVM Backend** | `references/llvm/llvm/lib/Target/` | `*InstrInfo.td` を `grep` し、命令エンコーディングを調査。 |

### 推奨コマンド

```bash
# WAMRの特定モジュールを詳細解析（シンボル抽出 + インクルードパス指定）
bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh symbols \
  references/wamr/core/iwasm/common/wasm_runtime_common.c \
  -- -I references/wamr/core/iwasm/include \
  -I references/wamr/core/shared/utils
```

## 4. 構成要素の詳細

本スキルは主に `references/` 下の静的リソースと、[Codebase Explorer](.agent/skills/general_codebase_explore/SKILL.md) を組み合わせた調査フローで構成されます。

- **WebAssembly Core**: `syntax/`, `binary/`, `exec/`, `valid/`
- **WAMR**: Interpreter (`core/iwasm/interpreter/`), AOT/JIT (`core/iwasm/aot/`)
- **LLVM**: Target-specific definitions (`.td` files)

## 5. 品質・検証ルール

- **真実の源泉 Single Source of Truth**: 命令実行時の挙動に関する疑義は、WAMR の実装よりも WebAssembly Core Spec の `exec/` 下にある形式的記述を優先して正解と見なします。
- **WIT 整合性**: WASI API の設計変更時は、`references/wasi/` 下の既存定義とのセマンティクス的整合性を検証します。

## 6. トラブルシューティング

**シンボルの定義が見つからない**:
WAMR などの大規模コードでは、ビルドオプションによって定義が切り替わる場合があります。`run-explorer.sh` 実行時に正確な `-I` (Include Path) とマクロ定義を渡しているか確認してください。

**`.td` ファイルの読み方がわからない**:
LLVM の TableGen 構文については、`references/llvm/` 内のドキュメントを参照するか、既存の命令定義（例: `i32.add`）をテンプレートとして検索してください。
