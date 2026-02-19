---
name: Fireball Architecture
description: >-
  Fireballプロジェクト固有のアーキテクチャパターン（3-Tier）、型語彙、およびリスクベース設計（Tiering）。
  WHEN: 新コンポーネント設計, 依存関係の構造決定, 型名選定, 設計詳細度の判断
  SCOPE: システム構造設計、データ構造、検証レベル
  RELATED: embedded_cpp_check
---

# Fireball Architecture

本プロジェクト（Fireball）の設計・実装において遵守すべき構造的ルール、パターン、および設計哲学を定義します。

## 1. 概要 (Overview / Benefits)

統一されたアーキテクチャ原則を適用することで、リソース制約と拡張性の両立を実現し、長期的な保守を可能にします。

- **リスク管理の効率化**: `{Risk_Tiering}` により、設計の投資対効果を最適化します。
- **ゼロコスト抽象化**: `{StaticDI}` と `{ComponentHarness}` により、メモリとCPUのオーバーヘッドを発生させずにテスト容易性を確保します。
- **実装への一意なマッピング**: `{Type_Vocabulary}` により、自然言語の曖昧さを排除し、WIT から C++ への正確な導出を保証します。

## 2. 環境・前提条件 (Prerequisites)

アーキテクチャ設計自体は環境に依存しませんが、関連するコード生成や検証ツールは **Dockerコンテナ** 内で実行されます。詳細は [Docker Workaround](../general_docker_run/SKILL.md) を参照してください。

## 3. 使用方法 (Usage)

具体的な指示やコマンドではなく、設計時の「思考のガイドライン」として活用します。

### 3.1 階層分離の判断 (3-Tier Decision)
1. **Cross System Boundary?** → Yes: **Tier 1** (IoC / URI-DI)
2. **High Complexity / Testing Needed?** → Yes: **Tier 2** (Harness / Stateless Interface)
3. **Otherwise** → **Tier 3** (Natural OO)

### 3.2 設計の詳細度 (Risk-based Tiering)
- **Tier 1**: 概要、Contract、主要シーケンス（低リスク）
- **Tier 2**: Tier 1 + 構成要素、状態遷移図（中リスク）
- **Tier 3**: Tier 2 + 直交表、コンセプトコード（高リスク）

## 4. 構成要素の詳細 (Component Details)

### 設計原則 (Core Axioms)
- **WIT-First**: 主要境界のインターフェースは WIT で定義する。
- **IoC (Inversion of Control)**: インターフェイス仕様は利用側が定義する。
- **Concept-Based Dependency**: C++ 側では仮想関数を使わず、テンプレートと Concept による静的 DI を行う。

### 関連ドキュメント
- **[general_design_rule.md](file:///w:/mysrc/fireball/docs/architecture/general_design_rule.md)**: 全体設計の核心哲学。
- **[embedded_cpp_rule.md](file:///w:/mysrc/fireball/.agent/rules/embedded_cpp_rule.md)**: 組み込み特化の C++ コーディング規約。

## 5. 品質・検証ルール (Quality & Validation)

- **設計完了チェックリスト**:
    - [ ] 境界が URI で抽象化されているか。
    - [ ] ハーネスによって依存関係が完全に注入可能か（モック可能か）。
    - [ ] 可変状態 (Data) と不変のロジック (Logic) が分離されているか。

## 6. トラブルシューティング (Troubleshooting)

**コード生成で依存関係が解決しない**:
WIT 定義におけるインターフェース間の依存が循環していないか確認してください。循環依存は Fireball の 3-Tier 原則に反します。

**実行時のオーバーヘッドが大きい**:
仮想関数 (vtable) を多用していないか確認してください。特に Tier 2 内部での依存解決には Concept による静的 DI を優先してください。
