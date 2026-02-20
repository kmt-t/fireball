---
name: Friction Auditor
description: >-
  ドキュメント内のキーワード表記揺れ、未定義キーワード、および要求トレーサビリティを監査するツール。
  WHEN: ドキュメント品質チェック, CIでのバリデーション, トレーサビリティ検証時
  SCOPE: docs/**/*.md
  RELATED: project_arch_design, general_docker_run
---

# フリクション監査 スキル設計書

システム内で定義された公式キーワードと、実際のドキュメントやコードにおける使用状況の乖離（Friction）を検出し、情報の整合性を担保するスキルです。

## 1. 概要

ドキュメントが大規模化するにつれて発生する「情報の腐敗」を防ぎ、全関係者が常に同じ語彙で意思疎通できる環境を維持します。

- **トレーサビリティの保証**: 要求仕様 (`requirement_list.md`) で定義されたキーワードが、設計や実装へ正しくリンクされているかを検証します。
- **表記揺れの排除**: 類似する綴りのキーワードを検出し、公式な用語への統一を促します。
- **孤立情報の防止**: 参照先が存在しないキーワードや、更新が古いまま放置されたドキュメントを特定します。

## 2. 環境・前提条件

- **Docker コンテナ 推奨**: 決定論的な監査結果を得るために、コンテナ環境での実行を強く推奨します。
- **WSL2 Bash**: Windows 環境ではパス解釈の問題を避けるため WSL2 シェルを使用してください。

## 3. 使用方法

### 統合実行 推奨

`docker-audit-friction.sh` を使用して、コンテナ内で監査を一括実行します。

```bash
# 全ドキュメントの総合監査 (Friction + Traceability)
bash .agent/skills/general_docker_run/scripts/docker-audit-friction.sh
```

### 個別実行 WSL2 Bash

特定の監査のみを高速に実行したい場合。

```bash
# 用語の表記揺れ・未定義チェック
python3 .agent/skills/project_friction_audit/scripts/audit_friction.py

# 要求キーワードの網羅性チェック
python3 .agent/skills/project_friction_audit/scripts/check_traceability.py
```

## 4. 構成要素の詳細

### scripts/
- **[audit_friction.py](.agent/skills/project_friction_audit/scripts/audit_friction.py)**: キーワードのタイポ検出や未定義チェックを行います。
- **[check_traceability.py](.agent/skills/project_friction_audit/scripts/check_traceability.py)**: 要求仕様書と各設計・実装ファイル間のリンクを検証します。

## 5. 品質・検証ルール

本ツールは以下の項目を品質基準として検証します。

- **Typo?**: 公式リストと類似度が高い単語。
- **Unknown/Undefined**: 定義リストに存在しない独自キーワード。
- **Broken Link**: 参照先のドキュメントが存在しない。
- **Stale Reference?**: 参照元より参照先の方が新しく、情報の同期が漏れている可能性があるもの。

## 6. トラブルシューティング

**レポートが生成されない**:
- 出力先ディレクトリ `docs/temp/` が存在し、書き込み可能か確認してください。
- 公式キーワードリスト `docs/requires/requirement_list.md` が存在するか確認してください。

**意図しない単語が検知される**:
誤検知である場合は、そのキーワードを公式リストに追加するか、監査対象から除外する設定をスクリプトに追加することを検討してください。
