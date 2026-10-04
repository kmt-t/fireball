# COOSスケジューラ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md)
参考実装: [`scheduler_concept.py`](docs/components/tier1_core/concepts/scheduler_concept.py)

純粋協調型ラウンドロビンスケジューラ（`{ADR_CoosPureRoundRobin}`）の、タスクライフサイクル・READYキュー・イベント駆動起床（`{ADR_EventDrivenWakeQueue}`）に関する振る舞いを定義する。CSPランデブーと値の所有権移譲は対象外とする。実装上の注意点はコンポーネント仕様書を参照し、本書では対応する検証条件を扱う。

## 2. テストケース一覧
<!-- traceability: {ADR_EventDrivenWakeQueue} {GLOBAL_IdleDetection} {GOTCHA-SCHED-03} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SCHED-01 | yieldしたタスクのFIFOディスパッチ | タスクA・Bをspawnし、両タスクが各実行後にyieldする | A→yield→B→yield→A(継続)→B(継続) の順で実行 | READYキューの順にディスパッチされ、優先度による割り込みは発生しない。このケースはyieldしないタスクを含む全タスクの公平性を検証しない | `{ADR_CoosPureRoundRobin}` |
| TEST-SCHED-02 | spawn直後はREADYキュー末尾 | タスクA実行中に新規タスクCをspawn | Cのspawn後、現在のRUNNING(A)がyield | 次に実行されるのはREADYキューの先頭（Cがその時点で末尾にいた場合は他のREADYタスクが先） | `os_scheduler.md` 実行可能列 |
| TEST-SCHED-03 | yield はREADYキュー末尾へ移動 | 単一タスクが2回yield | yieldごとに状態を観測 | yield直後はREADY、次サイクルで再度RUNNINGに戻る | `os_scheduler.md` 状態遷移図 |
| TEST-SCHED-04 | block/unblockサイクル | タスクがBLOCKされる原因(reason)付きでblock | block直後にunblock_taskを呼ぶ | BLOCKED→READYに遷移し、READYキュー末尾に追加される | `os_scheduler.md` 状態遷移図 |
| TEST-SCHED-05 | 終了(StopIteration)でTERMINATED | コルーチンが正常終了 | run_cycle/run_until_idle を実行 | タスク状態がTERMINATEDになり、以後READYキューにもBLOCKEDリストにも現れない | `os_scheduler.md` terminate |
| TEST-SCHED-06 | 全タスクBLOCKEDでアイドル検出 | 全タスクをblock | 1サイクル実行 | `schedule_next` が `None` を返し、`idle_handler`（設定済みの場合）が呼び出される | `GLOBAL_IdleDetection` |
| TEST-SCHED-07 | 割り込み通知によるREADY復帰 | vSoCランタイムタスクが`vector_id`待ちでBLOCKED | 固定5ワードの`interrupt-event`を`notify_interrupt`へ渡す | 呼び出し直後はFIFOに投入されるのみで対象タスクの状態は変化せず、イベントループがイベントを処理した時点で5ワードを対象タスクへ引き渡し、対象タスクのみREADYキュー末尾に追加する（他のBLOCKEDタスクは無関係） | `{GLOBAL_InterruptWakeup}` |
| TEST-SCHED-08 | READYキューpush/popのO(1) | 多数のREADYタスクと侵入型ringがある | enqueue/dequeueを実行し、操作対象を確認する | 隣接リンクとhead/sizeだけを操作する。割り込み待機者検索の計算量は別途評価する | `ADR_EventDrivenWakeQueue`、`test_sched_08_ready_queue_link_operations_are_bounded` |
| TEST-SCHED-09 | 最大タスク数の上限 | `FB_CONF_MAX_TASKS`（既定16）に達するまでspawn | 上限+1個目をspawn | 拒否される（アサーション相当のエラー） | scheduler_concept.py `assert len(self.tasks) < self.max_tasks` |
| TEST-SCHED-10 | 重複task_idの拒否 | 既存のtask_idを再度spawn | 同一IDでspawn | 拒否される | scheduler_concept.py `assert task_id not in self.tasks` |
| TEST-SCHED-11 | run_until_idle/run_to_completionの停止性 | 相互にnotifyし合わないBLOCKEDタスクが残る | run_to_completionを実行 | 無限ループにならず、上限到達で明示的なエラーを返す | 実装固有の安全策 |
| TEST-SCHED-12 | 原因レコードの順序と未登録ドロップ | FIFOに複数イベント、待機先が一部未登録 | `notify_interrupt`とドレインを実行 | 登録済みの待機先だけが受付順に起床し、未登録イベントはドロップされる | `{GLOBAL_InterruptWakeup}` |
| TEST-SCHED-13 | READY循環リストの両端操作と定員境界 | 容量4のREADYキューを用意する | 先頭取り出し、末尾追加、先頭追加、任意タスクの除去を行う | FIFO順と循環する前後リンクを保つ。満杯時は追加を拒否し、既存タスクの順序を変えない。各リンク操作はキュー長に依存しない | `{ADR_IntrusiveTcbList}` |
| TEST-SCHED-14 | 割り込み再スケジュール世代の一巡 | READYタスクA・Bが存在し、割り込み通知を1回以上受け付ける | FIFOをドレインし、A・Bを順にディスパッチする | `reschedule_generation`は保留バーストごとに1回だけ進み、各対象タスクの`last_seen_generation`が一度ずつ更新される。全対象の観測後に`reschedule_pending`が解除される | `{ADR_InterruptRescheduleGeneration}` |
| TEST-SCHED-15 | 一巡中に生成されたタスクの対象外化 | 世代要求が保留中にタスクCをspawnする | 現在世代の対象マスクを確定してCをディスパッチする | Cは現在世代の`round_target_mask`に含まれず、C自身の通常の協調実行を開始する。既存対象の観測完了を待つ | `{ADR_InterruptRescheduleGeneration}` |
| TEST-SCHED-16 | 終了タスクのTCBスロット返却とID再利用 | TCBが満杯で一部が終了済み、または全タスクが生存している。終了タスクには待機登録と所有資源がある | 生成・終了を固定幅ID空間を超える回数反復する | 終了済みの最古スロットを返却し、生存タスクを保持する。終了タスクの待機登録と資源を解放してからIDを再利用する。生存IDは重複せず、待機マスクは構成範囲内である。全タスク生存時は容量超過で停止する | `{CooperativeMultitasking}`、`{ADR_TaskIdLifetime}` |
| TEST-SCHED-17 | 割り込みFIFOのロックフリー性と容量境界 | ISR producerとCOOS consumerが同一の固定長SPSC FIFOを使用 | producerが満杯まで投入し、consumerが順に取り出し、空きスロットへ再投入する | mutex／スピンロックなしでFIFO順序を保ち、満杯時は既存イベントを上書きせず`false`を返し、consumer後に再利用できる | `{GLOBAL_InterruptWakeup}` |
| TEST-SCHED-18 | 時刻待機中の他タスク実行・アイドルフック・期限起床 | 時刻待機タスクとREADYタスクが各1つ存在し、単調時計を制御できる | 待機タスクが未来期限を登録して`BLOCKED_TIMER`へ移る | READYタスクが時刻を進める前に実行される。READYキューが空になった時点でアイドルフックを呼び、その後に次回期限まで待つ。待機タスクは期限到達後にREADYキュー末尾へ戻って完了する | `{GLOBAL_IdleDetection}` |
| TEST-SCHED-19 | タイマー待ちタスク終了時の期限解除 | 異なる未来期限を持つ`BLOCKED_TIMER`タスクが2つある | 早い期限のタスクを`task_killed`し、残るタスクを実行する | 終了タスクは期限到達で再開せず、残るタスクは自身の期限で一度だけ再開する。取り消した期限で余分な起床・待機周期を発生させない | pysim `test_sched_19_killing_timed_waiter_clears_only_its_deadline`, `GOTCHA-SCHED-03` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-SCHED-01} {GOTCHA-SCHED-02} {GOTCHA-SCHED-03} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-SCHED-01 | 連続直接ハンドオフ上限後のスケジューラ復帰 | 2つのタスクが CSP Rendezvous でピンポン通信し、連続ハンドオフ数が設定上限に達している | 上限到達後にさらにランデブーを成立させる | 直接遷移せず `YIELD` でスケジューラへ制御を戻し、連続回数を0に戻す。上限は全タスクの公平性や実時間応答上限を保証しない。**pysim実装テストの観測範囲**: `TEST-COOS-07` は `YIELD`、カウンタリセット、先行READYタスクの後ろへの登録、および次の実ディスパッチで先行タスクが選ばれることを検証する | [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md) , `{Challenge_CspHandoffStarvation}` |
| GOTCHA-SCHED-02 | 割り込み保留中の追加ハンドオフ連鎖停止 | CSPランデブー成立時に`reschedule_pending=true` | ランデブーを成立させ、遷移先の世代観測と追加連鎖を観測する | 所有権移譲と成立に必要な直接遷移は完了する。遷移先が世代を観測した後、追加の直接ハンドオフ連鎖を停止してスケジューラへ制御を戻す | [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md), `{ADR_InterruptRescheduleGeneration}` |
| GOTCHA-SCHED-03 | 最早タイマー期限の取消し後に最小期限を再計算 | 異なる未来期限を持つ`BLOCKED_TIMER`タスクが2つある | 最早期限のタスクを終了させる | 期限キャッシュは残るタスクの最小期限へ更新され、終了した期限で不要なアイドル待機サイクルや起床を発生させない | [`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md), `TEST-SCHED-19` |

## 3. テスト検証実績と網羅状況

pysimの実装テストは [`test_scheduler.py`](experiments/pysim/qa/tier1_core/test_scheduler.py) を正本とする。
テスト名の番号は本書のケースIDに対応する。
複数の契約を一つの操作列で検査する場合は、docstringに全ケースIDを記載する。

| ケースID | 実装テスト | 直接観測する結果 |
| :--- | :--- | :--- |
| TEST-SCHED-01 | `test_sched_01_pure_round_robin_fifo` | A・Bの実行順 |
| TEST-SCHED-02 | `test_sched_02_spawn_appends_behind_existing_ready_peer` | RUNNING中のspawn後のREADY順と次回ディスパッチ |
| TEST-SCHED-03 | `test_sched_03_yield_returns_to_ready_and_runs_again` | 各yield後のREADYと各実行中のRUNNING |
| TEST-SCHED-04、06、07 | `test_sched_04_06_07_interrupt_defers_targeted_wakeup_until_drain` | BLOCKED時のidle、通知直後の状態不変、対象だけのREADY復帰、5ワード値 |
| TEST-SCHED-05 | `test_sched_05_terminated_task_is_never_redispatched` | 終了状態、READYからの除去、再実行なし |
| TEST-SCHED-08、13 | `test_sched_08_ready_queue_link_operations_are_bounded` | 列長0/1/4/16/64で先頭・末尾追加、取り出し、既知タスク除去のリンク操作数とFIFO保存 |
| TEST-SCHED-09、10 | `test_sched_09_task_capacity_limit`、`test_sched_10_duplicate_task_id_rejected` | 拒否と既存タスク・READY順の保存 |
| TEST-SCHED-11 | `test_sched_11_unnotified_waiter_reaches_explicit_sweep_limit` | 保留待機タスクに対する指定上限での明示的失敗 |
| TEST-SCHED-12 | `test_sched_12_interrupt_fifo_orders_registered_targets_and_drops_unknown` | 受付順の起床、原因値、未登録イベントのドロップ |
| TEST-SCHED-13 | `test_sched_13_detached_task_reattaches_once`、`test_sched_13_ready_queue_intrusive_ring_two_ended_fifo` | メンバー重複なし、FIFO順、前後リンク、定員超過後の順序保存 |
| TEST-SCHED-14、15 | `test_sched_14_15_interrupt_generation_is_observed_once_by_existing_targets` | バースト1世代、各対象の一回観測、新規タスクの除外、保留解除 |
| TEST-SCHED-16 | `test_sched_16_terminated_task_returns_its_tcb_slot_on_spawn`、`test_sched_16_task_ids_stay_unique_after_a_slot_is_reclaimed` | 最古終了スロット返却と生存タスク保存を確認する。過去ID非再利用の確認は採用契約へ未追従であり、解放後のID再利用と有限範囲での反復生成は未検証である |
| TEST-SCHED-17 | `test_sched_17_interrupt_fifo_rejects_overflow_then_reuses_consumed_slot` | 満杯拒否、既存原因レコード保存、消費後の再利用 |
| TEST-SCHED-18、19 | `test_sched_18_timed_wait_runs_ready_peers_before_idle_sleep`、`test_sched_19_killing_timed_waiter_clears_only_its_deadline` | READY優先、期限時刻、idle・sleep列、取消し後の生存待機者 |

同じファイルに残る `test_mem_10_shared_block_move_semantics_csp_rendezvous` は `TEST-MEM-10` の結合検査である。
所有権移譲、送信元の無効化、受信先の内容を観測し、スケジューラケース数へは含めない。

2026-10-01にLinux・CPython 3.14.6・uv環境で局所スイートを実行した。
実行コマンドを示す。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --no-sync python -m pytest -q experiments/pysim/qa/tier1_core/test_scheduler.py
```

18件成功、0件失敗、0件skipである。
この実行件数はpytestの収集件数であり、仕様ケースの全項目達成を表さない。
隔離コピーでspawnをREADY末尾追加から先頭追加へ変える変異を作り、TEST-SCHED-02が失敗することも確認した。

2026-10-02にLinux・プロジェクトのuv環境で同スイートを再実行した。
対象ソースは[`scheduler.py`](experiments/pysim/tier1_core/scheduler.py)である。
19件成功、0件失敗、0件skip、0件xfailである。

```bash
UV_CACHE_DIR=/tmp/fireball-test-refactor-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier1_core/test_scheduler.py
```

TEST-SCHED-08/13の追加ケースは、QA側の`Task`派生クラスで前後リンクへの読み書きを数える。
各操作は読出し8回以下、書込み8回以下とする。
満杯拒否はリンク操作0回とし、既存のFIFO順を保存する。
状態比較と試験準備の走査は、検査対象の操作数へ含めない。

## 4. 未検証・スコープ外

- CSP Handoffによる直接コンテキストスイッチ（[`os_coos_test_spec.md`](docs/qa/tier1_core/os_coos_test_spec.md)側の責務）。
- C++実装の対称遷移（Symmetric Transfer）自体の性能特性（[`direct_context_switch_bench.py`](docs/components/tier1_core/benchmarks/direct_context_switch_bench.py)が正本）。

- 割り込み待機者検索は`drain_interrupts`のO(T)走査である。正本のO(1)要求はREADYキューpush/popに適用する。対象だけの起床を検索O(1)の証拠へ転用しない。待機者検索の応答時間は未計測である。
- TEST-SCHED-13の操作時間とTEST-SCHED-17の並行実行時のロックフリー性は、上記の逐次機能検査では未検証である。C++実装の構造監査と並行実行の証拠が必要である。
