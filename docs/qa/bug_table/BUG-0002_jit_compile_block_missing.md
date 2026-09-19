# BUG-0002 JITコンパイル対象PCの基本ブロックが取得できず停止する

| 項目 | 内容 |
| :--- | :--- |
| 重大度 | 重大 |
| 状態 | 修正済み |
| 対象 | `runtime_engine` |
| 発見日 | 2026-09-19 |

## 1. 現象

Tier 3でclang生成のWASMを実行すると、`RuntimeEngine.idle_hook` が `assert block is not None` で停止する。

停止箇所は [`runtime_engine.py:443`](experiments/pysim/tier2_runtime/runtime_engine.py) である。

コンパイル待ちキューから取り出したPCに対し、`get_block(pc)` が `None` を返している。

呼び出し経路は次のとおりである。

1. `RuntimeEngine.on_yield`
2. `RuntimeEngine.drain_compile_queue`
3. `RuntimeEngine.idle_hook`

## 2. 再現手順

```bash
.venv/Scripts/python.exe experiments/pysim/benchmarks/profile/bench_vtune_workload.py --phase suite_jit --kernel k_crc32:4
```

期待結果は、`[PASS]` と、Tier 2およびwasmtimeと一致する結果である。

実際の結果は、`AssertionError` による停止である。

## 3. 対象カーネル（実測）

各カーネルを単独で実行した結果を示す。実行日は2026-09-19である。

`k_crc32`, `k_fmatmul`, `k_nbody`, `k_mandel`, `k_sieve`, `k_expr`, `k_vm`, `k_hash`, `k_int64`, `k_text` の10件で停止した。

## 4. 原因

ホットブロックの記録判定が、4バイト単位のカードだけを見ていた。ブロックの取得結果を確認していなかった。

`br_if` の不成立経路は、`end` 命令の位置（ブロック先頭ではない）に落ちる。この位置は、直後のブロック先頭と同じカードに入ることがある。

同じカードなので、判定は「記録対象」となる。記録されたPCがホットになり、コンパイルキューへ入る。コンパイル時に、PCに対応するブロックが存在せず、`assert` に当たった。

`k_crc32` の例では、記録されたPCが `0x101be`（`end` の位置）だった。実際のブロック先頭は `0x101bf` だった。

## 5. 影響

BUG-0001と同じである。

## 6. 修正

[`runtime_engine.py`](experiments/pysim/tier2_runtime/runtime_engine.py) の `run` と、その生成器版で、記録の条件へ「ブロックが取得できる」ことを加えた。

## 7. 検証

- `TEST-JITR-54`（修正前は停止、修正後は通過）を追加した。
- 停止していた10カーネルのうち、この不具合だけで停止していた `k_crc32`、`k_sieve`、`k_int64` が、wasmtimeと一致した。
- 残りの7カーネルは、修正後に別の不具合（BUG-0008、BUG-0009、BUG-0001）へ進んだ。それらも修正済みである。

