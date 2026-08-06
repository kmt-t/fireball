# 汎用形式検証ツールキット (`tools/verifier`)
## Python DSL ➔ TLA+ 変換 & TLC バックエンド搭載

`tools/verifier` は、特定のプロジェクトに依存しない汎用的な形式検証ツールキットです。
Python DSL または 宣言的 `StateMachine` DSL で記述したモデルから **TLA+ 仕様ファイル (`.tla`)** および **TLC 設定ファイル (`.cfg`)** を自動生成（トランスパイル）し、バックエンドの **TLC (TLA+ Model Checker)** による厳密な形式モデル検査を実行できます。

---

## ディレクトリ構成

- `tools/verifier/`:
  - `core.py`: イミュータブル State, Rule, Invariant, VerificationResult 構造体
  - `checker.py`: TLA+ (TLC) バックエンドモデルチェッカー
  - `dsl.py` & `state_machine.py`: 宣言的ステートマシン DSL
  - `tla_generator.py`: TLA+ / TLC 設定コードトランスパイラ
  - `tlc_runner.py`: System TLC 並列実行 & ログ解析器
  - `risk_extractor.py`: ドキュメントからの検証テーマ自動抽出器
  - `evaluate_logs.py`: 実行ログ評価・結果サマリ自動出力器
  - `examples/`: 各種検証モデルサンプル
  - `tests/`: ユニットテスト

---

## 使い方

### 1. Python DSL / StateMachine でモデル記述

```python
from tools.verifier import StateMachine, ModelChecker, TLAGenerator

sm = StateMachine("MutexModel", allow_deadlock=False)
sm.variable("p1", "idle").variable("mutex", None)
sm.transition("P1_Acquire", src={"p1": "idle", "mutex": None}, dst={"p1": "critical", "mutex": "P1"})
sm.transition("P1_Release", src={"p1": "critical"}, dst={"p1": "idle", "mutex": None})
sm.invariant("MutualExclusion", lambda s: not (s['p1'] == 'critical'))
```

### 2. TLA+ コードの自動生成 & TLC バックエンドによる検証実行

```python
# TLA+ コード出力
gen = TLAGenerator(sm.to_model())
print(gen.generate_tla())

# TLC バックエンドで検証
checker = ModelChecker(sm.to_model(), backend="tlc")
result = checker.verify()
```

---

## テストとサンプル

```bash
# ユニットテストの実行
python -m unittest discover -s tools/verifier/tests

# 宣言的 StateMachine & TLA+ デモ
python -m tools.verifier.examples.state_machine_demo
```
