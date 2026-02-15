# Embedded System Development Gateway (Gemini CLI)

あなたは、リソース制約のある組み込みシステムおよびC++プロジェクトを開発するエージェントです。タスクを開始する際は、本ドキュメントを起点として、遵守すべき規約や設計原則を判断してください。

## 1. 📂 開発リソースの構成と参照判断基準

### ⚠️ 実装・レビューの原則 (Rules)
実装やコードレビューにおいて、品質と安全性を担保するための基準です。
- **[.agent/rules/cpp_coding_style.md](/.agent/rules/cpp_coding_style.md)**: 組み込み向けの命名規則、型語彙、メモリ安全性を高めるための記述制限。
- **[.agent/rules/protocols.md](/.agent/rules/protocols.md)**: 情報のトレーサビリティ確保、エージェントの行動指針、ドキュメント配置ルール。
- **[.agent/rules/documentation.md](/.agent/rules/documentation.md)**: 設計情報の構造化、日本語/英語の使い分け、図解による論理的説明のルール。

### 🛠 専門技能 (Skills)
特定の技術領域において、`activate_skill` を通じて高度な自動化や検証を行います。
- **自動コード生成**: 設計データ（JSON/IDL）からの再現性のあるコード生成。
- **組み込みC++最適化**: メモリ制約下でのライブラリ選択、ヒープ排除、静的解決。
- **アーキテクチャ検証**: 依存関係の逆転、疎結合なモジュール構造のチェック。

### 📚 設計・仕様ドキュメント (Documents)
プロジェクトの構造や要求を理解するために参照してください。各ディレクトリ内の **`FORMAT.md`** には、そのカテゴリのドキュメントが遵守すべき標準フォーマットが定義されています。

- **[docs/architecture/](/docs/architecture/)**: システム全体の構造、メモリマップ、インターフェース境界。
- **[docs/components/](/docs/components/)**: 各モジュールの責務、契約（Contracts）、データ構造。
- **[docs/patterns/](/docs/patterns/)**: 
    - [構造設計 (structural_patterns.md)](/docs/patterns/structural_patterns.md): 3-Tier分離、IoC、DI、Harness。
    - [リソース最適化 (resource_optimization_patterns.md)](/docs/patterns/resource_optimization_patterns.md): メモリ管理、標準ライブラリ制限、高速検索、ヒープレス関数。
    - [システム挙動 (system_behavior_patterns.md)](/docs/patterns/system_behavior_patterns.md): 回復戦略（Result型）、WITマッピング。
- **[docs/requires/](/docs/requires/)**: 満たすべき要求事項とキーワードベースのトレーサビリティ。
- **[docs/temp/](/docs/temp/)**: エージェントの自由な作業領域。検討プロセスや一時的な成果物をフラットに配置。

### 🔄 開発手順 (Workflows)
標準的な開発サイクルや、継続的な設計改善の手順を確認してください。
- **[.agent/workflows/development_cycle.md](/.agent/workflows/development_cycle.md)**: 設計、インターフェース定義、実装、検証のサイクル。
- **[.agent/workflows/bonsai_design.md](/.agent/workflows/bonsai_design.md)**: 継続的なリファクタリングと設計の洗練プロセス。

## 2. 🚦 タスク別・推奨アクション

- **新規機能の設計**: `requires` (要求) -> `architecture` (構造) -> `components` (責務)
- **コードの実装/修正**: `rules` (規約) -> `patterns` (定石) -> `skills` (最適化)
- **インターフェース定義**: `rules/documentation` (契約の記述) -> `code_generator` (自動生成)
- **問題の調査**: `architecture` (依存関係) -> `temp` (原因分析・プロトタイプ)

---
*注意: このファイルはエージェントが迷わないための地図です。常に最新の設計原則に基づき、最適なドキュメントを選択してください。*
