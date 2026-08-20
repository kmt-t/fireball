---
name: risk-assessor
description: Fireball 仕様書から設計課題・非同期競合・セキュリティ境界を評価し、docs/components/<tier>/formal/ 配下の形式検証モデルの要件を導出するスキル。
---

# Risk Assessor (リスク評価 & 形式検証割り当てスキル)

## 概要

`risk-assessor` は、`docs/requires/requirement_list.md` や各コンポーネント設計書からシステムの並行処理競合、割り込み安全制御、WASM ゲストメモリアクセス等のハイリスク項目を抽出し、`docs/components/<tier>/formal/` に配備すべき形式検証（pyModelChecking）モデルの不変条件を整理するスキルです。

---

## 優先検証テーマと形式検証モデルの対応

| 優先リスクテーマ | 対象コンポーネント | 形式検証モデルスクリプト | 検証不変条件 (CTL / Invariants) |
| :--- | :--- | :--- | :--- |
| **Mutex / Scheduler Progress** | `tier1_core` | `components/tier1_core/formal/mutex_model.py` | 相互排除 `AG !(crit1 & crit2)`, 進捗 `AG (wait1 -> EF crit1)` |
| **CSP Handoff Starvation** | `tier1_interface` | `components/tier1_interface/formal/csp_handoff_model.py` | 排他所有権 `AG !(owner_s & owner_r)`, 受取保証 `AG (in_flight -> AF owner_r)` |
| **JIT Cache & Execution Safety** | `tier2_jit` | `components/tier2_jit/formal/jit_cache_model.py` | ダーティ実行禁止 `AG !(dirty & safe_exec)`, フラッシュ後到達 `AG (flushed -> EF safe_exec)` |

---

## 検証パイプラインの実行

```powershell
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -clean
```
