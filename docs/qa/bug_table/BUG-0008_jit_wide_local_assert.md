# BUG-0008 f64ローカルを持つフレームでJITトレースの実行が停止する

| 項目 | 内容 |
| :--- | :--- |
| 重大度 | 高 |
| 状態 | 修正済み |
| 対象 | `runtime_engine` |
| 発見日 | 2026-09-19 |

## 1. 現象

i64またはf64のローカルを持つ関数で、コンパイル済みのJITトレースを実行すると、`assert frame.local_i32_only` で停止する。

停止箇所は、`RuntimeEngine._invoke_trace` である。

## 2. 再現手順

```bash
.venv/Scripts/python.exe experiments/pysim/benchmarks/profile/bench_vtune_workload.py --phase suite_jit --kernel k_fmatmul:6
```

修正前の結果は、`AssertionError` である。

## 3. 原因

ローカルは、型に関係なく1つ8バイトの固定スロットを持つ。したがって、トレースはローカルの番号からスロット位置を直接計算できる。

JITコンパイラは、幅が1でないローカルに触れるブロックだけをコンパイル対象から外す。コンパイル済みのトレースは、i32のローカルだけを読み書きする。

この `assert` は、実際のハザードを防いでいなかった。f64ローカルを持つ関数の、i32だけを扱うブロックまで停止させた。

## 4. 修正

[`runtime_engine.py`](experiments/pysim/tier2_runtime/runtime_engine.py) の `_invoke_trace` から、この `assert` を削除した。

## 5. 検証

- `TEST-JITR-56`（修正前は停止、修正後は通過）を追加した。
- `k_fmatmul`、`k_nbody`、`k_mandel` が、wasmtimeと一致した。

## 6. 関連する設計変更

フレームごとにスロット幅を切り替える設計を実装した。幅は、フレーム内で最大の変数サイズで決める。JITは、関数ごとのスロット幅を見てコードを出すため、i32のみのフレームに限定しない。詳細は、[BUG-0006_local_stack_budget_mismatch.md](docs/qa/bug_table/BUG-0006_local_stack_budget_mismatch.md) を参照する。
