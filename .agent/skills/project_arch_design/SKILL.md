---
name: Fireball Architecture
description: >-
  Fireballプロジェクト固有のアーキテクチャパターン（3-Tier）、型語彙、およびリスクベース設計（Tiering）。
  WHEN: 新コンポーネント設計, 依存関係の構造決定, 型名選定, 設計詳細度の判断
  SCOPE: システム構造設計、データ構造、検証レベル
  RELATED: embedded_cpp_check
---

# Fireball アーキテクチャ設計 スキル設計書

本プロジェクト（Fireball）の設計・実装において遵守すべき構造的ルール、パターン、および設計哲学を定義します。

## 1. 概要

統一されたアーキテクチャ原則を適用することで、リソース制約と拡張性の両立を実現し、長期的な保守を可能にします。

- **リスク管理の効率化**: `{Risk_Tiering}` により、設計の投資対効果を最適化します。
- **ゼロコスト抽象化**: `{StaticDI}` と `{ComponentHarness}` により、メモリとCPUのオーバーヘッドを発生させずにテスト容易性を確保します。
- **実装への一意なマッピング**: `{Type_Vocabulary}` により、自然言語の曖昧さを排除し、WIT から C++ への正確な導出を保証します。

## 2. 思考のガイドライン (使用方法)

具体的な指示やコマンドではなく、設計時の「思考のガイドライン」として活用します。

### 2.1 階層分離の判断 (3-Tier Decision)

| 条件 | 判定結果 | 対象コンポーネントの例・性質 |
| :--- | :--- | :--- |
| **Cross System Boundary?** | **Tier 1** | IoC / URI-DI, 外部システムとの境界 |
| **High Complexity / Testing Needed?** | **Tier 2** | Stateless Interface, 複雑なビジネスロジック (複雑な場合はHarnessでデコンポジション) |
| **Otherwise** | **Tier 3** | 一般的なオブジェクト指向 (カプセル化された内部状態) |

### 2.2 設計の詳細度 (Risk-based Tiering)

各階層ごとに要求される設計の「深さ」を定義します。

- **Tier 1 (低リスク)**: 概要、Contract (契約)、主要シーケンス
- **Tier 2 (中リスク)**: Tier 1 の成果物 ＋ 構成要素、状態遷移図
- **Tier 3 (高リスク)**: Tier 2 の成果物 ＋ 直交表、コンセプトコード

## 3. 構成要素と設計原則

### 3.1 設計原則 (Core Axioms)

- **WIT-First**: 主要境界のインターフェースは WIT で定義する。
- **IoC (Inversion of Control)**: インターフェイス仕様は利用側が定義する。
- **Concept-Based Dependency**: C++ 側では仮想関数を使わず、テンプレートと Concept による静的 DI を行う。

### 3.2 関連ドキュメント

- **[general_design_rule.md](.agent/rules/general_design_rule.md)**: 全体設計の核心哲学。
- **[embedded_cpp_rule.md](.agent/rules/embedded_cpp_rule.md)**: 組み込み特化の C++ コーディング規約。

## 4. 品質・検証ルール

設計完了時のチェックポイントです。

- [ ] 境界が URI で抽象化されているか。
- [ ] (Tier 2で複雑な場合) ハーネスによってデコンポジションされ、依存関係が完全に注入可能か（モック可能か）。
- [ ] 可変状態 (Data) と不変のロジック (Logic) が分離されているか。

## 5. 環境・前提条件

アーキテクチャ設計自体は環境に依存しませんが、関連するコード生成や検証ツールは **Dockerコンテナ** 内で実行されます。詳細は [Docker Workaround](.agent/skills/general_docker_run/SKILL.md) を参照してください。

## 6. トラブルシューティング

> [!WARNING]
> **コード生成で依存関係が解決しない場合**
> WIT 定義におけるインターフェース間の依存が循環していないか確認してください。循環依存は Fireball の 3-Tier 原則に反します。

> [!WARNING]
> **実行時のオーバーヘッドが大きい場合**
> 仮想関数 (vtable) を多用していないか確認してください。特に Tier 2 内部での依存解決には Concept による静的 DI を優先してください。
