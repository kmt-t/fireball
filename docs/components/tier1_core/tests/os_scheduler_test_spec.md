# COOSスケジューラ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/os_scheduler.md`
参考実装: `docs/components/tier1_core/concepts/scheduler_concept.py`
現行実装: `experiments/pysim/scheduler.py`

純粋協調型ラウンドロビンスケジューラ（`{ADR_CoosPureRoundRobin}`）の、タスクライフサイクル・READYキュー・イベント駆動起床（`{ADR_EventDrivenWakeQueue}`）に関する振る舞いを定義する。CSPチャネルによるハンドオフは対象外（`os_coos/test_spec.md` を参照）。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SCHED-01 | 純粋ラウンドロビン公平性 | タスクA・Bをspawn | A→yield→B→yield→A(継続)→B(継続) の順で実行 | 実行順が spawn 順のFIFOで、優先度による割り込みが一切発生しない | `{ADR_CoosPureRoundRobin}` |
| SCHED-02 | spawn直後はREADYキュー末尾 | タスクA実行中に新規タスクCをspawn | Cのspawn後、現在のRUNNING(A)がyield | 次に実行されるのはREADYキューの先頭（Cがその時点で末尾にいた場合は他のREADYタスクが先） | 3.3 実行可能列 |
| SCHED-03 | yield はREADYキュー末尾へ移動 | 単一タスクが2回yield | yieldごとに状態を観測 | yield直後はREADY、次サイクルで再度RUNNINGに戻る | 4.2 状態遷移図 |
| SCHED-04 | block/unblockサイクル | タスクがBLOCKされる原因(reason)付きでblock | block直後にunblock_taskを呼ぶ | BLOCKED→READYに遷移し、READYキュー末尾に追加される | 4.2 状態遷移図 |
| SCHED-05 | 終了(StopIteration)でTERMINATED | コルーチンが正常終了 | run_cycle/run_until_idle を実行 | タスク状態がTERMINATEDになり、以後READYキューにもBLOCKEDリストにも現れない | 5.1 terminate |
| SCHED-06 | 全タスクBLOCKEDでアイドル検知 | 全タスクをblock | 1サイクル実行 | schedule_nextがNoneを返す（またはpysimではidle_hookが発火） | 4.1 アイドル状態の検知 |
| SCHED-07 | 割り込み通知によるREADY復帰 | タスクがirq_id待ちでBLOCKED | `notify_interrupt(irq_id)` 相当のイベント発火 | 対象タスクのみREADYキュー末尾に追加される（他のBLOCKEDタスクは無関係） | `{GLOBAL_InterruptWakeup}` |
| SCHED-08 | イベント駆動起床はO(1)（線形スキャン禁止） | 多数のBLOCKEDタスクが異なるevent_keyで待機 | 1つのevent_keyのみnotify | notifyされたevent_keyのタスクのみが起床し、他のBLOCKEDタスクの状態には一切触れない（実装が全BLOCKEDタスクを走査していないことをコード/モックで確認） | `{ADR_EventDrivenWakeQueue}` |
| SCHED-09 | 最大タスク数の上限 | `FB_CONF_MAX_TASKS`（既定16）に達するまでspawn | 上限+1個目をspawn | 拒否される（アサーション相当のエラー） | scheduler_concept.py `assert len(self.tasks) < self.max_tasks` |
| SCHED-10 | 重複task_idの拒否 | 既存のtask_idを再度spawn | 同一IDでspawn | 拒否される | scheduler_concept.py `assert task_id not in self.tasks` |
| SCHED-11 | run_until_idle/run_to_completionの停止性 | 相互にnotifyし合わないBLOCKEDタスクが残る | run_to_completionを実行 | 無限ループにならず、上限到達で明示的なエラーを返す（pysimは`max_sweeps`超過で`RuntimeError`） | 実装固有の安全策 |

## 3. 現状のギャップ（pysim実装との差分）

- `experiments/pysim/scheduler.py` は `FB_CONF_MAX_TASKS` 相当の上限チェック（SCHED-09）と、重複spawn時のID再利用防止（SCHED-10相当、pysimは`_next_id`自動採番のため重複自体が起こらない設計であり、これは仕様と異なる思想だが問題ではない）を実装していない。
- `scheduler_concept.py`の`priority`引数（`spawn(task_id, coroutine, priority: int = 0)`）はos_scheduler.md本文に明記された機能ではなく、ADR-SCHED-002（`{ADR_CoosPureRoundRobin}`）により優先度制御自体が明示的に不採用とされているため、concept側の残存引数は無視してよい（優先度による並び替えを行わないことを確認するテストを追加すべき）。

## 4. 未検証・スコープ外

- CSP Handoffによる直接コンテキストスイッチ（`os_coos/test_spec.md`側の責務）。
- C++実装の対称遷移（Symmetric Transfer）自体の性能特性（`benchmarks/direct_context_switch_bench.py`が正本）。
