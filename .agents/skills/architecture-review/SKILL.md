---
name: architecture-review
description: architecture_overview.md の Tier 分類、責務境界、依存方向、コンポーネント一覧、契約配置、リンク整合性を監査するときに使う。
---

# Architecture Review

本スキルは [`architecture_overview.md`](../../../docs/architecture/architecture_overview.md) を構造の正本として監査する。個別アルゴリズム、ABI、形式モデル、実装品質は対象外とする。

## 目的

レビューの目的は、Tier 分類、責務、依存方向、契約配置、一覧・図・リンクの一致を確認することである。概要書に下位仕様の詳細が重複していれば指摘する。

## 対象

対象は `architecture_overview.md`、`document_structure.md`、各 Tier のコンポーネント一覧と概要書から参照されるリンク先である。下位の形式検証、概念コード、テスト仕様は、リンクと境界の確認に必要な範囲だけ読む。

## 手順

### 1. 範囲と証拠の確認

`document_structure.md` で Tier と契約配置の規則を確認し、以下の収集結果を実ファイルと突合する。

```powershell
python .agents/skills/architecture-review/scripts/collect_arch_context.py --json
```

収集結果には Tier 別一覧、図のノード、概要書のリンク、解決できないリンクが含まれる。追加の細目は [architecture review rubric](references/architecture_review_rubric.md) を適用する。

## チェックリスト

ルーブリックの全評価軸を確認する。

- Tier、契約と実装の配置を文書階層の正本と照合する。
- 責務、依存方向、図の辺を各コンポーネント文書の根拠と照合する。
- Tier 表、図、一覧、WIT、Markdown リンクが同じ構成を示すか確認する。
- 下位仕様を概要書に重複させていないか確認する。

重複を見つけた場合は正本を特定し、重複側に置く参照先を示す。

## 結果

所見の重大度と報告形式は [共通レビュー手順](../review-protocol.md) に従う。アーキテクチャ固有の例はルーブリックを参照する。全評価軸に判定があり、一覧・図・依存関係・リンクが正本と一致し、所見を再確認できる証拠とともに報告した時点で完了とする。
