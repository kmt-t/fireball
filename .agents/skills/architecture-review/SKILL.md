---
name: architecture-review
description: architecture_overview.md の Tier 分類、責務境界、依存方向、コンポーネント一覧、およびリンク整合性を監査するスキル。
---

# Architecture Review

本スキルは、最上位の [`architecture_overview.md`](../../docs/architecture/architecture_overview.md) をシステム構造の地図として監査する。個別コンポーネントのアルゴリズムや ABI の詳細を再検証するスキルではない。

## 目的

レビューの目的は、次の4点を明確にすることである。

1. 全コンポーネントが適切な Tier に分類されていること。
2. architecture_overview.md がコンポーネントの責務を重複なく一文で表していること。
3. 依存関係が契約から実装へ一方向に整理され、逆流や循環がないこと。
4. Tier の一覧、図のノード、コンポーネント文書、Markdown リンクが一致していること。

architecture_overview.md には、状態機械、データ構造、アルゴリズム、ABI レイアウト、形式検証モデル、テスト条件、リソース内訳を追加しない。これらは各コンポーネント文書の責務であり、概要書に再掲されている場合は重複として指摘する。

## 監査対象

- `docs/architecture/architecture_overview.md`
- `docs/architecture/document_structure.md`
- `docs/components/tier1_core/`
- `docs/components/tier1_interface/`
- `docs/components/tier2_runtime/`
- `docs/components/tier3_executer/`
- `docs/components/tier3_plugins/`
- `docs/components/tier3_platform/`
- architecture_overview.md から参照されるリンク先

`docs/components/` 内の形式検証、概念コード、テスト仕様は、リンク先の存在確認と Tier・責務・依存関係の確認に必要な範囲だけ参照する。詳細アルゴリズムの正しさは component-review の対象とする。

## 監査手順

### 1. コンテキスト収集

```powershell
python .agents/skills/architecture-review/scripts/collect_arch_context.py --json
```

出力には、Tier 別のコンポーネント一覧、概要図のノード、概要書のリンク先、存在しないリンクが含まれる。収集結果と実ファイルを突合し、一覧の抜けや古いリンクを特定する。

### 2. Tier 監査

- Tier が document_structure.md の配置規則と一致している。
- 契約・抽象化を定義する文書と具体実装を定義する文書の階層が逆転していない。
- Interpreter と JIT は tier3_executer、Debugger と Guest Profiler は tier3_plugins、物理ドライバとゲスト側アダプタは tier3_platform に分類されている。
- Tier の昇格・降格に、責務または依存方向上の根拠がある。

### 3. 責務境界監査

- コンポーネントごとの責務が一文で区別できる。
- 同じ責務を複数コンポーネントが所有していない。
- 上位概要書が下位コンポーネントの詳細仕様を複製していない。
- 実装責務、契約責務、観測フック責務が混在していない。
- 実行系と交換可能なプラグインの境界が明示されている。

重複を見つけた場合は、どの文書を正本にするか、もう一方をどの参照リンクへ置き換えるかを示す。

### 4. 依存関係監査

- 図の矢印の意味が一貫している。
- Tier 3 から Tier 2、Tier 2 から Tier 1 へ向かう契約利用の方向が守られている。
- 下位 Tier が上位 Tier の具体実装へ依存していない。
- 同一 Tier 内の依存が責務境界を越えていない。
- 循環依存、未接続ノード、図にない文書間依存がない。
- runtime_observability などの Tier 2 契約を、Tier 3 プラグインが実装詳細として取り込んでいない。

### 5. 一覧・リンク監査

- Tier 表、Mermaid 図、コンポーネント一覧が同じ集合を表す。
- 各一覧項目に対応する Markdown 正本が存在する。
- リンクの相対パスが実ファイルを指す。
- 廃止された Tier 名、コンポーネント名、旧ファイル名が残っていない。
- 新しいコンポーネント文書が一覧・図・リンクのいずれかから漏れていない。

## 判定

重大度は [`architecture_review_rubric.md`](references/architecture_review_rubric.md) に従う。レビュー結果は、ファイル、行、該当する Tier または依存関係、問題の種類、修正案を示す。

- CRITICAL: Tier 逆転、依存循環、契約境界の逆流など、アーキテクチャの成立を壊す不整合。
- MAJOR: 責務重複、コンポーネント一覧の欠落、リンク切れ、図と文書の不一致。
- MINOR: 表現の曖昧さ、説明不足、命名やリンク表記の軽微な不整合。

## 完了条件

- Tier 別コンポーネントの集合が docs/components/ と一致する。
- 各コンポーネントの責務が重複せず、詳細仕様の正本が明確である。
- 依存図に循環、逆方向依存、未接続ノードがない。
- 概要書のすべてのコンポーネントリンクが存在する。
- architecture_overview.md が構造情報だけを持ち、下位仕様を再掲していない。
