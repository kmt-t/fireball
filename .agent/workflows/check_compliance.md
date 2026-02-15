---
description: >-
  コーディング標準・設計方針への適合性チェック手順。
  WHEN: コード実装完了後のレビュー, リリース前検証, /check_compliance
  RELATED: cpp_linting（スタイル検証）, cpp_embedded（禁止ライブラリ検査）, fireball_architecture（構造チェック）
---

1. **対象範囲の特定**
   - ユーザーにチェック対象のファイルまたはディレクトリを確認する。
   - 指定がない場合は、`git status` や `git diff --name-only` を使用して変更されたファイルを特定し、対象候補として提示する。

2. **関連スキルの確認と基準の確立**
   - 以下のスキル概要を確認し、チェック基準を把握する。詳細な手順が必要な場合は各SKILL.mdを参照する。

   - **C++ Linting** (`cpp_linting/SKILL.md`)
      - **目的**: プロジェクト固有のコーディングスタイルと命名規則の自動検証。
      - **要求**:
         - 命名: 変数/関数 `snake_case`, 定数 `UPPER_SNAKE_CASE`, メンバ変数 `_` 接尾辞。
         - 書式: インデント2スペース, K&Rスタイル, 最大100文字/行。
         - 実行: `.agent/skills/cpp_linting/scripts/linter.py` を使用して検証すること。

   - **Type Vocabulary** (`fireball_vocabulary/SKILL.md`)
      - **目的**: 実装非依存な型定義による可読性とトレーサビリティの向上。
      - **要求**:
         - C++のプリミティブ型(`uint32_t`等)を直接使用せず、`using` 定義された語彙型(`address`, `offset`等)を使用すること。
         - メモリ領域の参照には `std::span` ベースのビュー型(`binary_view`等)を使用すること。

   - **Fireball Architecture** (`fireball_architecture/SKILL.md`)
      - **目的**: メモリ制約(64KB)と静的解決を優先した堅牢な設計。
      - **要求**:
         - 原則: ヒープ割り当て(`malloc`/`new`)の禁止、例外の禁止、RAIIによるリソース管理。
         - 構造: 3-Tier分離の適用。コンポーネントは Stateless Interface + Harness (DI) + Context (Data) で構成すること。
         - 分離: Logic(処理), Data(可変状態), View(不変データ) を明確に分離すること。

   - **Risk Assessment** (`risk_assessment/SKILL.md`)
      - **目的**: 実装リスクに応じた設計の詳細化と検証の義務付け。
      - **要求**:
         - 複雑なロジックやリソース制約が厳しい箇所は Tier 2(構造図示) 以上で設計すること。
         - クリティカルなロジックは Tier 3(直交表・コンセプトコード) で検証すること。

3. **静的解析とスタイルチェック**
   - 上記基準に基づき、対象ファイルを確認する。
   - **Lint実行**: `python3 .agent/skills/cpp_linting/scripts/linter.py <path>` を実行し、結果を確認する。
   - **目視チェック**:
     - `fireball_vocabulary` に従わない「生ポインタ」や「生プリミティブ型」の使用がないか。
     - 禁止されている `std::vector` や `std::string` 等の動的コンテナが使われていないか。
     - `harness` パターンに従い、状態とロジックが分離されているか。

4. **アーキテクチャ整合性チェック**
   - **依存関係**: 上位レイヤーから下位レイヤーへの依存違反がないか。
   - **Static DI**: 依存性注入が `behavior` やテンプレートを用いて静的に行われているか。
   - **WITとの整合性**: WIT定義ファイル (`.wit`) と実装されたC++インターフェースが一致しているか。

5. **レポートと修正提案**
   - チェック結果をまとめ、違反箇所と理由をMarkdown形式で提示する。
   - 修正が必要な箇所について、具体的な修正後のコードスニペットまたはドキュメントドラフトを提示する。
   - ユーザーの承認を得て修正を適用する。