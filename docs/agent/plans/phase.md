# 開発フェーズ

## 概要

`docs/agent/architecture/overview.md`に従い、コンポーネントの開発スケジュールをフェーズ分けする。

## フェーズと日程

2025年12月1日時点で開発は以下の5つのフェーズに分割されている。

- 設計 Phase0（～2025年12月末）
- 実装1 Phase1（～2026年5月末）
- 実装2 Phase2（～2026年10月末）
- PoC Phase3（～2026年12月末）
- OSS Phase4（2027年1月～）

## 設計 Phase0

設計ドキュメンを作成し、設計の詳細を明確化する。並行してAIエージェントによる設計アシスト環境の整備。

## 実装1 Phase1

- vSoC
  - インタープリタ
  - vOffloader
    - MMIO
    - システムコール
- COOSカーネル
- IPCルータ
- HAL
  - x64上での標準入出力のみ

## 実装2 Phase 2

- vSoC
  - ロギング
  - デバッガ
- 開発環境整備
  - 標準ビルド環境
  - リンカスクリプト

## PoC Phase 3

- ターゲットボード移植
  - Microbit
  - Zephyr
- デバッグ
- テスト

## OSS Phase4

- vSoC (Virtual System-on-Chip)
  - JITコンパイラ
  - vOffloader
    - アクセラレータ
