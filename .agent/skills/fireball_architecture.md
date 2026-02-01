---
name: Fireball Architecture
description: 本プロジェクト固有のアーキテクチャパターン（3-Tier, IoC, Harness）の概要
---

# Fireball アーキテクチャスキル

本プロジェクト（Fireball）の設計・実装において遵守すべき構造的ルールとパターンの概要です。
詳細は各パターンドキュメントを参照してください。

## 1. 3-Tier モジュール分離

システムの複雑度に応じたモジュール設計アプローチ（Architecture / Subsystem / Implementation）。

- **参照**: [docs/orders/patterns/interface.md](../../docs/orders/patterns/interface.md)
  - 3つのTier定義と選択基準

## 2. 制御の反転 (IoC) とサービスファサード DEPRECATED

Tier 1 (Architecture Domain) における結合度低減手法。

- **参照**: [docs/orders/patterns/ioc.md](../../docs/orders/patterns/ioc.md)
  - URIベースの依存性注入
  - サービスファサードによるIPC隠蔽

## 3. Harness と Stateless Interface

Tier 2 (Subsystem Domain) における依存関係整理手法。

- **参照**: [docs/orders/patterns/harness.md](../../docs/orders/patterns/harness.md)
  - Stateless Interface (振る舞いのみ)
  - Concrete Object (内部状態のみ)
  - HarnessによるStatic DI
