---
name: pysim-review
description: experiments/pysim の実装を仕様・組込み移植性・型・メモリ・計算量・Tier境界に照らしてレビューするときに使う。
---

# pysim Review

## 目的

experiments/pysim/ の参照シミュレータと製品仕様の整合性、および組込み C++23 への移植可能性を確認する。評価条件と違反の重大度は [pysim review rubric](references/pysim_review_rubric.md)、実装規約は [Python coding standards](../../rules/coding-standards-python.md) を正本とする。

## 対象

ユーザーが指定したファイル、モジュール、ディレクトリと、直接関係する要求・コンポーネント仕様・テスト・設定を調べる。広いシステム境界を扱う場合は、[document structure](../../../docs/architecture/document_structure.md) と [architecture overview](../../../docs/architecture/architecture_overview.md) を先に確認する。

## 手順

1. **製品側の正本を確定する**: 対応する要求、WIT、コンポーネント仕様から製品の責務、API、ABI、メモリ契約を確認する。
2. **仕様とシミュレータの境界を監査する**: Python固有のクラス、ファイル構成、寿命、APIを製品仕様の根拠としていないか確認する。識別子や型名はpysimだけで判断せず、製品側の定義を探す。製品側に根拠がない要件を実装から推測しない。
3. **機械チェックを行う**: 対象範囲を指定して AST スキャナを実行し、続けて spec-integrator.yaml の pysim_imports 設定を使うソースチェックを確認する。

   ~~~bash
   uv run python .agents/skills/pysim-review/scripts/scan_pysim_anti_patterns.py <target_path> --json
   ./tools/check-src.sh -g pysim
   ~~~

   スキャン結果を実ファイルと照合し、設定上の除外範囲を確認する。
4. **ルーブリックを適用する**: [pysim review rubric](references/pysim_review_rubric.md) の9評価軸をすべて確認する。状態・副作用・境界を直接検査するテストかも確認する。
5. **所見をまとめる**: 仕様上の違反、実装詳細の漏れ、推定、未確認事項を区別し、対象コードと製品側の正本の両方を引用する。

## チェックリスト

- **仕様と境界**: 状態、契約、GOTCHA、テストが一致し、Python固有の実装詳細が製品仕様に混入していない。
- **型と制御**: 型注釈、Any/object、nullable契約、例外、実行時型判定が規約に沿う。
- **コンテナとメモリ**: 固定容量コンテナ、ROM/RAM配置、オブジェクト寿命、容量、重複保持が予算と仕様に合う。
- **計算量と決定性**: ホットパス、探索、事前計算、設定値、最悪時コストに根拠がある。
- **互換性とコード健全性**: 不要なフォールバック、デッドコード、Tier越境、テスト境界の混在がない。

各項目の例外、重大度、詳しい確認方法はルーブリックと適用ルールに従う。最小ターゲット予算は推定で緩和せず、資料間で不一致があれば不一致として報告する。

## 結果

[共通レビュー手順](../review-protocol.md) の所見形式と判定を使う。全適用項目に判定を付け、仕様・実装・テストの根拠を追跡できる状態で報告した時点で完了とする。
