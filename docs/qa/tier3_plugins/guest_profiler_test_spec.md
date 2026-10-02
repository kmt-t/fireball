# ゲストプロファイラ テスト仕様書

## 1. 目的と対象範囲

正本: [`guest_profiler.md`](docs/components/tier3_plugins/guest_profiler.md)
関連正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

本仕様は、Runtimeイベントバッチからコールグラフ、実行時間、欠落状態を集計するGuest Profilerを検証する。
Runtimeの実行制御とDebuggerの停止・再開・ステップ処理は対象外とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-PROF-01 | 関数コールグラフと包括時間 | 親関数・子関数の`function_enter`/`function_exit`が対応する | 入れ子のイベント列をバッチで渡す | 親子の呼出辺、呼出回数、包括時間が正しく集計される |
| TEST-PROF-02 | 自己時間の計算 | 親関数の実行中に子関数イベントがある | 子関数の開始・終了tickを変えて集計する | 子の包括時間を親の子時間から一度だけ差し引き、自己時間を正しく計算する |
| TEST-PROF-03 | Trap後の関数離脱 | 1個以上の関数フレームが開いている | `trap`の後、Runtime契約どおり内側から外側の`function_exit`を渡す | Trap受信時点でフレームを閉じず、各離脱イベントに対応するフレームを推定値として閉じる |
| TEST-PROF-04 | バッチの累積欠落数 | 前回より大きい`dropped_count`を持つバッチがある | 通常イベントと欠落バッチを順に集計する | 欠落差分を欠落統計へ一度だけ加え、影響する時間統計に推定値を付ける |
| TEST-PROF-05 | 固定表と呼出スタックの容量超過 | 関数表、辺表、または呼出スタックの容量が小さい | 容量を超える関数・辺・再帰を入力する | 欠落または超過を記録し、追跡中の親フレームを誤って閉じず、影響した統計を推定値にする |
| TEST-PROF-06 | Debugger停止時の開いたフレーム | 1個以上の関数フレームが開いている | `debug_stop`を入力する | 開いているフレームを停止tickで推定値として閉じ、Runtime状態を変更しない |
| TEST-PROF-07 | 実行方式フラグの取扱い | 同一関数がInterpreterとJITの両方から実行される | 両方式のイベント列を集計する | 実行方式フラグを統計属性として保持し、コールグラフへ別関数を追加しない |

## 3. 判定条件

関数統計、呼出辺、包括時間、自己時間、欠落数、推定値フラグ、および開いたフレーム数を直接比較する。
ProfilerがRuntimeのPC、スタック、メモリ、停止状態を変更した場合は不合格とする。

## 4. 形式検証との対応

`guest_profiler_model.py`で呼出対応、包括・自己時間、Trap離脱、欠落時の推定状態、および再帰スタック超過の性質を
検証する。通常モデルで各性質が成立し、`guards=False`変異モデルで対応する違反遷移を反証する。

## 5. 現行pysimとの対応

| ケース | 実行入口 | 判定範囲 |
| :--- | :--- | :--- |
| TEST-PROF-01/02 | `test_call_graph_and_time_accounting` | 実RuntimeEventBatchから全呼出回数、辺、包括・自己時間、フレーム終了を照合する |
| TEST-PROF-03 | `test_trap_waits_for_ordered_exits_as_estimated` | Trap直後は2フレームを保持する。内側のexitで親を閉じず、最終exitまで各時間を照合する |
| TEST-PROF-04 | `test_cumulative_batch_loss_is_counted_once` | 累積欠落5→5→8の差分と、推定値付きの時間7を照合する |
| TEST-PROF-05 | 容量超過3ケース | 関数表、辺表、再帰スタックを個別に超過させ、親フレームと時間を保存する |
| TEST-PROF-06 | `test_debug_stop_closes_nested_frames_at_stop_tick` | 停止tickで親子を閉じ、自己時間と推定属性を照合する。入力batchの不変性を検査する |
| TEST-PROF-07 | `test_interpreter_and_jit_events_share_one_function_identity` | 同じIDへ呼出2回と時間6を集計する部分を検査する。方式フラグを統計属性として保持する製品機構は未実装である |

TEST-PROF-06のbatch不変性だけで実Runtime全状態の保存を証明しない。
実Runtimeとの結合証拠と、TEST-PROF-07の方式属性は残る製品・統合検証項目である。
