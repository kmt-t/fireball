---
name: architecture-review
description: architecture_overview.md の Tier 分類、責務境界、依存方向、契約配置、不要な責務・経路の追加、コンポーネント一覧とリンク整合性を監査するときに使う。
---

# Architecture Review

本スキルは [`architecture_overview.md`](../../../docs/architecture/architecture_overview.md) を構造の正本として監査する。個別アルゴリズム、ABI、形式モデル、実装品質は対象外とする。

## 目的

レビューの目的は、Tier 分類、責務、依存方向、契約配置、一覧・図・リンクの一致を確認することである。責務と経路の必要性も評価する。概要書に下位仕様の詳細が重複していれば指摘する。

## 対象

対象は `architecture_overview.md`、`document_structure.md`、各 Tier のコンポーネント一覧と概要書から参照されるリンク先である。下位の形式検証、概念コード、テスト仕様は、リンクと境界の確認に必要な範囲だけ読む。

## 手順

### 1. 範囲と証拠の確認

`document_structure.md` で Tier と契約配置の規則を確認し、以下の収集結果を実ファイルと突合する。

```powershell
python .agents/skills/architecture-review/scripts/collect_arch_context.py --json
```

収集結果には Tier 別一覧、図のノード、概要書のリンク、解決できないリンクが含まれる。

### 2. 責務と経路の必要性

[仕様の必要性と既存機構の確認](../specification-review.md) を適用する。概要書の責務・経路・契約を要求と下位の正本へ対応付ける。未結線の既存機構と、新しい責務が必要な問題を分ける。下位仕様はこの判定に必要な範囲を確認し、個別アルゴリズムやABIの適否は component-review で扱う。

### 3. 構造の照合

[architecture review rubric](references/architecture_review_rubric.md) の全評価軸を適用する。

## チェックリスト

ルーブリックの全評価軸を確認する。

- Tier、契約と実装の配置を文書階層の正本と照合する。
- 責務、依存方向、図の辺を各コンポーネント文書の根拠と照合する。
- Tier 表、図、一覧、WIT、Markdown リンクが同じ構成を示すか確認する。
- 下位仕様を概要書に重複させていないか確認する。

重複を見つけた場合は正本を特定し、重複側に置く参照先を示す。

## 結果

所見の重大度と報告形式は [共通レビュー手順](../review-protocol.md) に従う。責務と経路の必要性の判定も報告する。修正案には共通の必要性確認を再適用する。全評価軸の判定と根拠、正本との不整合、未確認の範囲と必要な追加調査を報告した時点でレビュー完了とする。
