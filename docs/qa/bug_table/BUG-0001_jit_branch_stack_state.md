# BUG-0001 JITトレース実行後の分岐で制御・値スタックが不整合になる

| 項目 | 内容 |
| :--- | :--- |
| 重大度 | 重大 |
| 状態 | 修正済み |
| 対象 | `runtime_engine`, `x64_jit`, `native_stacks` |
| 発見日 | 2026-09-19 |

## 1. 現象

Tier 3（インタープリタとJITの併用）でclang生成のWASMを実行すると、`assert` で停止する。

停止箇所は2種類ある。

- [`native_stacks.py:160`](experiments/pysim/tier2_runtime/native_stacks.py) の `assert outer_result_arity <= final_size`
- [`native_stacks.py:156`](experiments/pysim/tier2_runtime/native_stacks.py) の `assert 0 <= depth <= frame_count`

どちらも `Interpreter.step` の分岐処理（`br` / `br_if`）で発生する。

## 2. 再現手順

```bash
.venv/Scripts/python.exe experiments/pysim/benchmarks/profile/bench_vtune_workload.py --phase suite_jit --kernel k_sha256:8
```

期待結果は、`[PASS]` と、Tier 2およびwasmtimeと一致する結果である。

実際の結果は、`AssertionError` による停止である。

## 3. 対象カーネル（実測）

各カーネルを単独で実行した結果を示す。実行日は2026-09-19である。

| 停止箇所 | カーネル |
| :--- | :--- |
| `native_stacks.py:160` | `k_sha256`, `k_sort`, `k_matmul`, `k_fir` |
| `native_stacks.py:156` | `k_lz` |

## 4. 切り分け結果

`k_sha256`（8単位）と `k_sort`（1単位）で、次の結果を得た。

| 実行方法 | 結果 |
| :--- | :--- |
| `Interpreter.call`（Tier 2） | 通過、wasmtimeと一致 |
| `Interpreter.start` / `step` のブロック単位実行 | 通過 |
| `RuntimeEngine`（JITコンパイラなし） | 通過 |
| `RuntimeEngine`（JITコンパイラあり） | `assert` で停止 |

この切り分けは、JITトレースの実行後に制御フレームが不整合になることを示した。原因は、「6. 原因」に示す。

## 5. 影響

- 実プログラムがTier 3で動かない。AO-Benchと既存テストは手書きWATであり、この経路に当たらない。
- Tier 3の性能評価とJITのVTune計測ができない。

## 6. 原因

`block`、`loop`、`if` は制御フレームを積む命令である。トレースは制御フレームを積まない。

これらの命令で終わるブロックのトレースは、命令を飛び越えて次のブロック先頭へ進んでいた。その結果、制御フレームが足りない状態になった。

インタープリタの `br_if` は、事前解決済みの分岐先を持っていても、先にフレームスタックを使って分岐を解決する。フレーム数が0のとき、ループへ戻る `br_if 0` を関数レベルの分岐と誤判定した。関数レベルの分岐は戻り値アリティ1を要求するため、空のオペランドスタックで `assert` に当たった。

## 7. 修正

[`runtime_engine.py`](experiments/pysim/tier2_runtime/runtime_engine.py) の `_compile_trace` を変更した。

- `block`、`loop`、`if` で終わるブロックのトレースは、終端トレースとして扱う。
- 終端トレースは、ネイティブチェインの対象にならない。
- 実行後は、インタープリタがこれらの命令を実行する。
- `if` の条件値は、トレースの残余値としてオペランドスタックに残る。

## 8. 検証

- `TEST-JITR-53`（修正前は停止、修正後は通過）と `TEST-JITR-59` を追加した。
- 修正前に停止していた5カーネル（`k_sha256`、`k_sort`、`k_matmul`、`k_lz`、`k_fir`）が、wasmtimeと一致した。
- 全17カーネルの通し実行は、Tier 2と同一のチェックサム（`0xC09373A7`）になった。

