# 不具合表

`docs/qa/bug_table/` は、テストや計測で見つかった不具合を保存する。

不具合は 1 件につき 1 ファイルで記録する。この文書は、全件の索引と記録規約を持つ。

## 1. 記録規約

- 不具合IDは `BUG-<4桁連番>` とする。不具合IDは、メタキーワードではなく、テストケースIDと同じ種類の識別子である。
- 詳細ファイル名は `BUG-<連番>_<英小文字の要約>.md` とする。
- 再現手順は、そのまま実行できるコマンドまたはコードで書く。
- 原因が未特定の場合は、未特定と明記する。推測を原因として書かない。
- 数値は、実測した値と資料から読んだ値を区別して書く。
- 修正した場合は、状態を「修正済み」へ変え、修正の証跡を詳細ファイルへ追記する。行は削除しない。

### 1.1 重大度

| 重大度 | 基準 |
| :--- | :--- |
| 重大 | 主要機能が実行不能になる。または仕様の中核要求を満たさない。 |
| 高 | 仕様と実装が食い違い、通常の運用で制約に当たる。 |
| 中 | 補助的な入口や資料の不整合であり、回避できる。 |
| 低 | 影響が限定的である。または修正済みの記録である。 |

### 1.2 状態

| 状態 | 意味 |
| :--- | :--- |
| 未修正 | 再現済みで、修正していない。 |
| 要確認 | 不一致は確認したが、仕様側と実装側のどちらが正か未確認である。 |
| 修正済み | 修正し、検証を実行した。 |

## 2. 不具合一覧

| ID | 件名 | 重大度 | 状態 | 対象 | 発見経路 | 詳細 |
| :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| BUG-0001 | JITトレース実行後の分岐で制御・値スタックが不整合になる | 重大 | 修正済み | `runtime_engine`, `x64_jit` | 大規模ワークロード | [BUG-0001_jit_branch_stack_state.md](docs/qa/bug_table/BUG-0001_jit_branch_stack_state.md) |
| BUG-0002 | JITコンパイル対象PCの基本ブロックが取得できず停止する | 重大 | 修正済み | `runtime_engine` | 大規模ワークロード | [BUG-0002_jit_compile_block_missing.md](docs/qa/bug_table/BUG-0002_jit_compile_block_missing.md) |
| BUG-0003 | `k_life` がTier 3で終了しない | 重大 | 修正済み | `runtime_engine`, `x64_jit` | 大規模ワークロード | [BUG-0003_jit_life_hang.md](docs/qa/bug_table/BUG-0003_jit_life_hang.md) |
| BUG-0004 | 終了したタスクのTCBスロットが返却されない | 高 | 修正済み | `scheduler` | 大規模ワークロード | [BUG-0004_scheduler_tcb_not_reused.md](docs/qa/bug_table/BUG-0004_scheduler_tcb_not_reused.md) |
| BUG-0005 | `main.py` が起動できない | 中 | 修正済み | `main.py` | 保守調査 | [BUG-0005_main_entrypoint_broken.md](docs/qa/bug_table/BUG-0005_main_entrypoint_broken.md) |
| BUG-0006 | ローカル値領域の容量が予算資料と食い違う | 中 | 要確認 | `interpreter`, 予算資料 | 大規模ワークロード | [BUG-0006_local_stack_budget_mismatch.md](docs/qa/bug_table/BUG-0006_local_stack_budget_mismatch.md) |
| BUG-0007 | ロガーURIがIPCサービステーブルに未登録である | 低 | 修正済み | `ipc_router` | 大規模ワークロード | [BUG-0007_logger_uri_not_registered.md](docs/qa/bug_table/BUG-0007_logger_uri_not_registered.md) |
| BUG-0008 | f64ローカルを持つフレームでJITトレースの実行が停止する | 高 | 修正済み | `runtime_engine` | 大規模ワークロード | [BUG-0008_jit_wide_local_assert.md](docs/qa/bug_table/BUG-0008_jit_wide_local_assert.md) |
| BUG-0009 | JITが降ろしを代行した制御フレームが残り、結果が食い違う | 重大 | 修正済み | `runtime_engine` | 大規模ワークロード | [BUG-0009_jit_stale_control_frames.md](docs/qa/bug_table/BUG-0009_jit_stale_control_frames.md) |
| BUG-0010 | GOTCHA IDの多くがキーワード台帳に登録されていない | 中 | 修正済み | 台帳、テスト仕様書、検証ゲート | 設計変更の作業 | [BUG-0010_gotcha_ids_unregistered.md](docs/qa/bug_table/BUG-0010_gotcha_ids_unregistered.md) |
| BUG-0011 | JITの押し出し書き込みがオペランドスタックの容量を超える | 高 | 修正済み | `x64_jit`, `runtime_engine` | 大規模ワークロード | [BUG-0011_jit_operand_spill_overflow.md](docs/qa/bug_table/BUG-0011_jit_operand_spill_overflow.md) |

## 3. 関連資料

- 発見経路の「大規模ワークロード」は、[`bench_vtune_workload.py`](experiments/pysim/benchmarks/profile/bench_vtune_workload.py) を指す。
- 大規模ワークロードのゲストは、[`suite.c`](experiments/pysim/benchmarks/profile/guest/suite.c) からビルドした WASM である。
