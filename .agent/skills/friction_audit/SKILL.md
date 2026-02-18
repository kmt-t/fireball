---
name: Friction Auditor
description: ドキュメント内のキーワード表記揺れ、未定義キーワード、および要求トレーサビリティを監査するツール。
WHEN: ドキュメント品質チェック, CIでのバリデーション, トレーサビリティ検証時
SCOPE: docs/**/*.md
RELATED: fireball_architecture, docker_workaround
---

# Friction Auditor スキル

## 1. 概要 (Overview)

システム内で定義された公式キーワード (`docs/requires/list.md`) と、実際のドキュメントで使用されているキーワードの差異（Friction）を検出します。
また、要求仕様と実装/設計のトレーサビリティ検証も行います。

## 2. 環境・前提条件

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。
- **Windowsユーザー**: お使いの環境で直接実行するのではなく、**Git Bash** を経由してスクリプトを実行してください。

## 3. 使用方法 (Usage)

`docker-friction.sh` を使用して、コンテナ内で監査を一括実行します。

```bash
# 全ドキュメントの監査を実行 (Friction + Traceability)
bash .agent/skills/docker_workaround/scripts/docker-friction.sh
```

実行後、`docs/temp/friction_report.md` にレポートが出力されます。

## 4. 監査内容詳細

### Friction check
`docs/concept/vocabulary.md` 等で定義された用語と異なる表記（例: `FireBall` vs `Fireball`）を検出します。
- **Typo?**: 綴りが似ているキーワード。修正が必要。
- **Unknown/Undefined**: 定義リストに存在しない独自キーワード。

### Traceability check
要求仕様書 (`docs/requires/list.md`) で定義された `{Keyword}` が、下流の設計書やソースコードで参照されているかを確認します。
- **Broken Link**: リンク先のファイルが存在しない。
- **Stale Reference?**: 参照先のファイルが、参照元より新しい（更新漏れの可能性）。

## 5. トラブルシューティング

**レポートが生成されない**:
スクリプトの実行権限を確認してください。また、Dockerコンテナが正しくマウントされているか `docker-workaround` の手順で確認してください。
システム内で定義された公式キーワード (`docs/requires/list.md`) と、実際のドキュメントで使用されているキーワードの差異（Friction）を検出する。

## 2. 機能 (Features)
- **タイポ検出**: 正規キーワードと類似した綴りの単語を検出し、修正を提案する。
- **未定義検出**: リストに存在しない独自キーワードの使用を検出する。
- **トレーサビリティ検証**: 要求仕様 (`docs/requires/list.md`) のキーワードが適切に参照されているかをチェックする。

## 3. 使用方法 (Usage)

ルートディレクトリで以下のコマンドを実行する。

```bash
# フリクション監査（タイポ・未定義・リンク切れ）
python3 .agent/skills/friction_audit/scripts/friction_audit.py

# トレーサビリティ検証（キーワード網羅性）
python3 .agent/skills/friction_audit/scripts/check_traceability.py
```

## 4. 出力 (Output)

`docs/temp/friction_report.md` にレポートが出力される。

### レポートの見方
- **Typo?**: 綴りが似ているキーワードが見つかった場合。修正が必要。
- **Unknown/Undefined**: 似ているものがない場合。
    - 本当に必要なキーワードなら `list.md` に追加する。
    - 不要なら削除する。
- **Broken Link**: リンク先のファイルが存在しない場合。
- **Stale Reference?**: 参照先のファイルが、参照元のファイルより**新しい**場合。
- **Missing Syntax**: `{Keyword}` 形式ではないが強調されているキーワード候補。

## 5. 環境・実行 (Environment)

- **推奨**: VSCode DevContainer または Git Bash (Windows)。
- **コンテナ実行**: 環境が整っていない場合は、**[Docker Workaround](../docker_workaround/SKILL.md)** を参照してください。
