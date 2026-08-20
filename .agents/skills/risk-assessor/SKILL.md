---
name: risk-assessor
description: Fireball ドキュメントおよび仕様からデザインチャレンジ・競合リスク・不変条件を自動抽出・評価し、優先検証対象を確定するリスク評価スキル。
---

# Risk Assessor (リスク評価スキル)

## 概要

`risk-assessor` は、`docs/requires/requirement_list.md` や `docs/components/` から Fireball システムの設計リスク、非同期競合、割り込み安全制御、メモリアクセスセキュリティ等のハイリスク項目を自動評価・抽出するスキルです。

---

## ワークフロー

1. **リスク抽出と評価**:
   ```bash
   uv run python .agents/skills/risk-assessor/scripts/assess_risks.py
   ```
2. **優先リスクの特定**:
   - `InterruptSafety`: 割り込みハンドラとメインタスクの非同期競合
   - `CspHandoffStarvation`: CSP チャネルにおけるタスクスターベーション
   - `SyscallMemorySafety`: WASM ゲストメモリ境界のアクセスマッピング
   - `VirtualFdTable`: 仮想 FD マッピングのマルチタスク競合

3. **形式検証部品の割り当て**:
   抽出されたハイリスク課題に対し、`tools/verifier/components/` 内のモデル検査コンポーネントを適用・検証します。
