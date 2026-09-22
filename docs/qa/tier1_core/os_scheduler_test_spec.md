# COOSスケジューラ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md)
参考実装: [`scheduler_concept.py`](docs/components/tier1_core/concepts/scheduler_concept.py)

純粋協調型ラウンドロビンスケジューラ（`{ADR_CoosPureRoundRobin}`）の、タスクライフサイクル・READYキュー・イベント駆動起床（`{ADR_EventDrivenWakeQueue}`）に関する振る舞いを定義する。CSPランデブーと値の所有権移譲は対象外とする。ハンドオフ上限到達時のスケジューラ制御復帰のみ、本書のGOTCHAとして扱う。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SCHED-01 | yieldしたタスクのFIFOディスパッチ | タスクA・Bをspawnし、両タスクが各実行後にyieldする | A→yield→B→yield→A(継続)→B(継続) の順で実行 | READYキューの順にディスパッチされ、優先度による割り込みは発生しない。このケースはyieldしないタスクを含む全タスクの公平性を検証しない | `{ADR_CoosPureRoundRobin}` |
| TEST-SCHED-02 | spawn直後はREADYキュー末尾 | タスクA実行中に新規タスクCをspawn | Cのspawn後、現在のRUNNING(A)がyield | 次に実行されるのはREADYキューの先頭（Cがその時点で末尾にいた場合は他のREADYタスクが先） | `os_scheduler.md` 実行可能列 |
| TEST-SCHED-03 | yield はREADYキュー末尾へ移動 | 単一タスクが2回yield | yieldごとに状態を観測 | yield直後はREADY、次サイクルで再度RUNNINGに戻る | `os_scheduler.md` 状態遷移図 |
| TEST-SCHED-04 | block/unblockサイクル | タスクがBLOCKされる原因(reason)付きでblock | block直後にunblock_taskを呼ぶ | BLOCKED→READYに遷移し、READYキュー末尾に追加される | `os_scheduler.md` 状態遷移図 |
| TEST-SCHED-05 | 終了(StopIteration)でTERMINATED | コルーチンが正常終了 | run_cycle/run_until_idle を実行 | タスク状態がTERMINATEDになり、以後READYキューにもBLOCKEDリストにも現れない | `os_scheduler.md` terminate |
| TEST-SCHED-06 | 全タスクBLOCKEDでアイドル検出 | 全タスクをblock | 1サイクル実行 | `schedule_next` が `None` を返し、`idle_handler`（設定済みの場合）が呼び出される | `{GLOBAL_IdleDetection}` |
| TEST-SCHED-07 | 割り込み通知によるREADY復帰 | vSoCランタイムタスクが`vector_id`待ちでBLOCKED | 固定5ワードの`interrupt-event`を`notify_interrupt`へ渡す | 呼び出し直後はFIFOに投入されるのみで対象タスクの状態は変化せず、イベントループがイベントを処理した時点で5ワードを対象タスクへ引き渡し、対象タスクのみREADYキュー末尾に追加する（他のBLOCKEDタスクは無関係） | `{GLOBAL_InterruptWakeup}` |
| TEST-SCHED-08 | イベント駆動起床はO(1)（線形スキャン禁止） | 多数のBLOCKEDタスクが異なるevent_keyで待機 | 1つのevent_keyのみnotify | notifyされたevent_keyのタスクのみが起床し、他のBLOCKEDタスクの状態には一切触れない（実装が全BLOCKEDタスクを走査していないことをコード/モックで確認） | `{ADR_EventDrivenWakeQueue}` |
| TEST-SCHED-09 | 最大タスク数の上限 | `FB_CONF_MAX_TASKS`（既定16）に達するまでspawn | 上限+1個目をspawn | 拒否される（アサーション相当のエラー） | scheduler_concept.py `assert len(self.tasks) < self.max_tasks` |
| TEST-SCHED-10 | 重複task_idの拒否 | 既存のtask_idを再度spawn | 同一IDでspawn | 拒否される | scheduler_concept.py `assert task_id not in self.tasks` |
| TEST-SCHED-11 | run_until_idle/run_to_completionの停止性 | 相互にnotifyし合わないBLOCKEDタスクが残る | run_to_completionを実行 | 無限ループにならず、上限到達で明示的なエラーを返す | 実装固有の安全策 |
| TEST-SCHED-12 | 原因レコードの順序と未登録ドロップ | FIFOに複数イベント、待機先が一部未登録 | `notify_interrupt`とドレインを実行 | 登録済みの待機先だけが受付順に起床し、未登録イベントはドロップされる | `{GLOBAL_InterruptWakeup}` |
| TEST-SCHED-13 | READY循環リストの両端操作と定員境界 | 容量4のREADYキューを用意する | 先頭取り出し、末尾追加、先頭追加、任意タスクの除去を行う | FIFO順と循環する前後リンクを保つ。満杯時は追加を拒否し、既存タスクの順序を変えない。各リンク操作はキュー長に依存しない | `{ADR_IntrusiveTcbList}` |
| TEST-SCHED-14 | 割り込み再スケジュール世代の一巡 | READYタスクA・Bが存在し、割り込み通知を1回以上受け付ける | FIFOをドレインし、A・Bを順にディスパッチする | `reschedule_generation`は保留バーストごとに1回だけ進み、各対象タスクの`last_seen_generation`が一度ずつ更新される。全対象の観測後に`reschedule_pending`が解除される | `{ADR_InterruptRescheduleGeneration}` |
| TEST-SCHED-15 | 一巡中に生成されたタスクの対象外化 | 世代要求が保留中にタスクCをspawnする | 現在世代の対象マスクを確定してCをディスパッチする | Cは現在世代の`round_target_mask`に含まれず、C自身の通常の協調実行を開始する。既存対象の観測完了を待つ | `{ADR_InterruptRescheduleGeneration}` |
| TEST-SCHED-16 | 終了タスクのTCBスロット返却 | TCBが満杯で、一部のタスクが終了済み。別の場合として、全タスクが生存している | 新しいタスクをspawnする | 終了済みの最古のスロットが返却され、生成が成功する。生存タスクは回収されない。全タスクが生存している場合は、容量超過で停止する。新しいタスクIDは、過去のIDと重複しない | `{CooperativeMultitasking}` |
| TEST-SCHED-17 | 割り込みFIFOのロックフリー性と容量境界 | ISR producerとCOOS consumerが同一の固定長SPSC FIFOを使用 | producerが満杯まで投入し、consumerが順に取り出し、空きスロットへ再投入する | mutex／スピンロックなしでFIFO順序を保ち、満杯時は既存イベントを上書きせず`false`を返し、consumer後に再利用できる | `{GLOBAL_InterruptWakeup}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-SCHED-01 | 連続直接ハンドオフ上限後のスケジューラ復帰 | 2つのタスクが CSP Rendezvous でピンポン通信し、連続ハンドオフ数が設定上限に達している | 上限到達後にさらにランデブーを成立させる | 直接遷移せず `YIELD` でスケジューラへ制御を戻し、連続回数を0に戻す。上限は全タスクの公平性や実時間応答上限を保証しない。**pysim実装テストの観測範囲**: `TEST-COOS-07` は `YIELD` とカウンタリセットを検証し、READYキュー順序は検証しない | `os_scheduler.md` , `{Challenge_CspHandoffStarvation}` |
| GOTCHA-SCHED-02 | 割り込み保留中の直接ハンドオフ連鎖停止 | CSPランデブー成立時に`reschedule_pending=true` | 直接ハンドオフ可能な相手を成立させる | ランデブーと所有権移譲は完了するが、相手への直接遷移を行わず`YIELD`でスケジューラへ戻り、世代観測の機会を確保する | `{ADR_InterruptRescheduleGeneration}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- CSP Handoffによる直接コンテキストスイッチ（[`os_coos_test_spec.md`](docs/qa/tier1_core/os_coos_test_spec.md)側の責務）。
- C++実装の対称遷移（Symmetric Transfer）自体の性能特性（[`direct_context_switch_bench.py`](docs/components/tier1_core/benchmarks/direct_context_switch_bench.py)が正本）。
