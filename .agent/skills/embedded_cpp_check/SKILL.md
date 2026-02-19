---
name: Embedded C++ Optimization
description: >-
  組み込み環境（RAM 64KB）における禁止/許可ライブラリ、コンテナ代替、メモリ管理パターン、エラーハンドリング。
  WHEN: C++実装, ライブラリ選定, コンテナ選択, メモリ戦略決定, エラー処理設計
  SCOPE: 実装レベルの技術判断。アーキテクチャ構造はproject_arch_designを参照。
  RELATED: project_arch_design, project_code_generate, embedded_cpp_rule
---

# Embedded C++ Optimization

リソース制約の厳しい環境（RAM 64KB等）で要求される特殊なC++実装技術と設計判断基準を定義します。

## 1. 概要 (Overview / Benefits)

本プロジェクトにおける「組み込みの法（Embedded Laws）」を定義し、ハードウェアの限界内で安全かつ効率的なコードを記述するためのガイドラインを提供します。

- **ランタイム・セーフティのガードレール**: 禁止API（`vector`等）を機械的にチェックし、開発者の負担を軽減します。
- **ヒープ破壊リスクの最小化**: 動的確保を禁止し、安全な代替手段（`economic_function`等）を提示します。
- **早期フィードバック**: 実行不可能なコードが混入するのを未然に防ぎ、設計パターンの再確認を促します。

## 2. 環境・前提条件 (Prerequisites)

- **Docker Workaround**: チェックツールの実行にはDockerコンテナを推奨します。詳細は [Docker Workaround](../general_docker_run/SKILL.md) を参照してください。
- **Windowsユーザー**: PowerShell から `bash` と入力して **WSL2 (Ubuntu)** シェルを使用してください。

## 3. 使用方法 (Usage)

### 統合実行 (推奨)

`docker-cppcheck.sh` を使用して、コンテナ内で安全にルールチェックを行います。

```bash
# 特定のファイルを検証
bash .agent/skills/embedded_cpp_check/scripts/docker-cppcheck.sh src/main.cxx

# ディレクトリ以下の全ファイルを一括検証
find src -name "*.cxx" | bash .agent/skills/embedded_cpp_check/scripts/docker-cppcheck.sh
```

### 個別実行

```bash
# ローカル環境での検証
python3 .agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py src/
```

## 4. 構成要素の詳細 (Component Details)

### 4.1 規則一覧 (L1: Library Rules)

| カテゴリ | 禁止機能 | 許可される代替案 |
| :--- | :--- | :--- |
| **コンテナ** | `std::vector`, `std::map`, `std::string` | `std::array`, `std::span`, `Sorted Indexed Array` |
| **ポインタ** | `std::unique_ptr`, `std::shared_ptr` | 静的ライフサイクル、独自 `Ref` 構造体 |
| **I/O・並行** | `<iostream>`, `<thread>`, `<exception>` | `<cassert>`, `coos::task` |
| **型消去** | `std::function` | `economic_function<Capacity>` |

### 4.2 メモリ管理・エラー処理
- **メモリ戦略**: `constexpr` 配列、パーテーション確保、配置 `new`。
- **エラー処理**: `assert/panic` (回復不可)、`Result<T, E>` パラダイム (回復可)。

### 4.3 スクリプト
- **[check_embedded_rules.py](file:///w:/mysrc/fireball/.agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py)**: 禁止APIの使用を静的解析で検出します。

## 5. 品質・検証ルール (Quality & Validation)

- **不変条件 (Invariants)**: 本スキルによるチェックを通過したコードは、実行時に暗黙的なヒープ確保や例外送出を行わないことが保証されます。
- **バリデーション**: [embedded_cpp_rule](../../rules/embedded_cpp_rule.md) との整合性が自動的にチェックされます。

## 6. トラブルシューティング (Troubleshooting)

**ERROR: 'std::vector' usage detected**:
動的確保は禁止されています。固定サイズの `std::array` または、外部からバッファが供給される `std::span` への書き換えを検討してください。

**コンテナ内でマウントが空**:
Windows環境特有の問題です。[Docker Workaround](../general_docker_run/SKILL.md) のトラブルシューティングを参照してください。
