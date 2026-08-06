---
name: formal-verifier
description: 汎用明示的状態モデルチェッカー (Explicit-State Model Checker) 及び Python DSL ➔ TLA+ コンパイラ・TLC バックエンド検証環境 (tools/verifier) の利用スキル。システム仕様、ステートマシン、プロトコル、排他制御、デッドロック、不変式の形式検証時に使用する。
---

# Verifier (汎用形式検証フレームワーク)

## 概要

`tools/verifier` は、特定のプロジェクトや特定 OS / 言語に依存しない汎用的な形式検証ツールキットです。
Python DSL または 宣言的 `StateMachine` DSL で定義したシステムモデルに対し、以下の検証が可能です：

1. **明示的状態モデルチェッカー (Explicit-State Model Checker)**:
   - 状態空間の全自動探索 (BFS / DFS)。
   - **Safety (不変条件/不変式)**, **Deadlock (デッドロック)**, **到達可能性 (Reachability)** の検証。
   - 最小ステップの**反例トレース (Counterexample Trace)** および **Mermaid 状態遷移図** の自動生成。
2. **Python DSL ➔ TLA+ 自動コンパイラ & TLC バックエンド (`TLAGenerator` / `TLCRunner`)**:
   - DSL で記述したモデルから `TLA+ (.tla)` および `TLC (.cfg)` を自動パース・生成。
   - `tlc2.TLC` (TLA+ Model Checker) をバックエンドで並列実行し、完全なモデル検査を実施。
3. **リスク抽出 & ログ評価モジュール**:
   - `tools/verifier/risk_extractor.py`: ドキュメントからの検証テーマ自動抽出
   - `tools/verifier/evaluate_logs.py`: 検証レポート・結果の自動評価サマリ

---

## 主なエントリポイント

- **パッケージ構造**: `tools/verifier`
- **主要モジュール**:
  - `tools.verifier.StateMachine`: 宣言的ステートマシン DSL (`src -> event -> dst`)
  - `tools.verifier.ModelChecker`: 検証実行エンジン (`backend="python" | "tlc"`)
  - `tools.verifier.TLAGenerator`: TLA+ / TLC 設定ファイル生成器
  - `tools.verifier.generate_markdown_report`: Markdown 形式結果レポート生成

---

## ワークフロー

### 1. 宣言的 StateMachine DSL によるモデル化

```python
from tools.verifier import StateMachine, ModelChecker, TLAGenerator

sm = StateMachine("MySystemModel", allow_deadlock=False)

# 変数と初期値
sm.variable("state_a", "init")
sm.variable("resource", None)

# 状態遷移 (src -> event -> dst)
sm.transition("Step1", src={"state_a": "init"}, dst={"state_a": "running", "resource": "acquired"})
sm.transition("Step2", src={"state_a": "running"}, dst={"state_a": "init", "resource": None})

# 不変式
sm.invariant("ResourceSafety", lambda s: not (s['state_a'] == "init" and s['resource'] is not None))
```

### 2. TLA+ コードの自動生成

```python
model = sm.to_model()
gen = TLAGenerator(model)
print(gen.generate_tla()) # .tla 仕様の出力
```

### 3. 検証の実行 (TLC または 内蔵チェッカー)

```python
checker = ModelChecker(model, backend="tlc") # または backend="python"
result = checker.verify()
```

---

## 単体テストとサンプル

- **サンプルコード**: `tools/verifier/examples/`
  - `mutex.py`: 排他制御モデル
  - `producer_consumer.py`: 有界バッファモデル
  - `bank_transfer.py`: 不変式違反 & 反例デモ
  - `state_machine_demo.py`: 宣言的 StateMachine & TLA+ 生成デモ
- **テストコマンド**:
  ```bash
  python -m unittest discover -s tools/verifier/tests
  ```
