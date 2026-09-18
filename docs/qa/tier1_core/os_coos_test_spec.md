# COOS (CSPチャネル) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`os_coos.md`](docs/components/tier1_core/os_coos.md)
参考実装: [`coos_concept.py`](docs/components/tier1_core/concepts/coos_concept.py)

ホーアCSPに基づく**バッファなし同期ランデブーチャネル**（`{ADR_RendezvousChannel}`）と、直接コンテキストスイッチ（CSP Handoff）、割り込みイベント駆動起床、アイドル検知の振る舞いを定義する。

### 2.1 CSP通信と状態遷移 直交表マトリクス
<!-- traceability: {CSP_Handoff} {ADR_RendezvousChannel} {GLOBAL_InterruptWakeup} -->

チャネル通信時のタスク状態とスケジューラの挙動を検証する組み合わせ直交表。チャネルは値を保持しないため、待機状態は「待機者なし / 送信待機 / 受信待機」の3種類とする。バッファ満杯の状態は存在しない。

| ケース | 自タスク要求 | チャネル待機者 | 相手状態 | 期待される動作 (自) | 期待される動作 (他) | テストケースID |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | SEND | なし | - | `SUSPENDED_CSP` へ遷移。値は自フレームに保持 | (なし) | TEST-COOS-01 |
| 2 | SEND | 受信待機 (RECV) | `SUSPENDED_CSP` | **READY へ遷移** | **READY へ遷移し、値の所有権を取得** | TEST-COOS-04 |
| 3 | RECV | なし | - | `SUSPENDED_CSP` へ遷移 | (なし) | TEST-COOS-03 |
| 4 | RECV | 送信待機 (SEND) | `SUSPENDED_CSP` | **READY へ遷移し、値の所有権を取得** | **READY へ遷移。自フレームの値は無効化** | TEST-COOS-02 |
| 5 | SEND | 送信待機 (SEND) | `SUSPENDED_CSP` | **設計上到達不能**（1チャネル1待機者違反をアサーション検出） | - | TEST-COOS-05 |
| 6 | RECV | 受信待機 (RECV) | `SUSPENDED_CSP` | **設計上到達不能**（同上） | - | TEST-COOS-05 |
| 7 | ハンドオフ上限到達 | 受信/送信待機 | `SUSPENDED_CSP` | **直接切替せずスケジューラへyield** | **READYへ遷移** | TEST-COOS-07 |
| 8 | ISR通知 | - | `SUSPENDED_CSP`/`READY` | (継続) | **INT イベント投入 → yield点でドレイン → READY 遷移** | TEST-COOS-08 |

### 2.2 テストケース詳細一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-COOS-01 | 送信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクAが`channel_send`を呼ぶ | Aは`SUSPENDED_CSP`に遷移し、値はAのフレームに保持されたまま（チャネルには値が存在しない） | 直交表 ケース1 |
| TEST-COOS-02 | 受信者到着でランデブー成立（送信側視点） | Aが送信待機中(SEND) | タスクBが`channel_recv`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る。Aの`pending_val`は破棄される（二重所有防止） | 直交表 ケース4 |
| TEST-COOS-03 | 受信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクBが`channel_recv`を呼ぶ | Bは`SUSPENDED_CSP`に遷移 | 直交表 ケース3 |
| TEST-COOS-04 | 送信者到着でランデブー成立（受信側視点） | Bが受信待機中(RECV) | タスクAが`channel_send`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る | 直交表 ケース2 |
| TEST-COOS-05 | 1チャネル1待機者の強制（同方向多重待機は不可能） | (1) Aが送信待機中(SEND) または<br>(2) Bが受信待機中(RECV) | (1) 別タスクCが同チャネルへ`channel_send`<br>(2) 別タスクDが同チャネルへ`channel_recv` | 到達不能ケースとして`assert`で即座に検出される（設計違反フェイルファスト） | 直交表 ケース5/6 |
| TEST-COOS-06 | CSP Handoffは待機相手へ直接切り替える | ランデブー成立、ハンドオフ回数が上限未満 | ランデブー完了時の結果を観測 | 結果が直接切替を示し、切替先が待機相手である | `{CSP_Handoff}` |
| TEST-COOS-07 | 連続ハンドオフ上限後のスケジューラ復帰 | `max_handoffs=2` とし、2タスク間で2回の直接ハンドオフを完了 | 3回目のランデブーを成立させる | 操作結果が `YIELD` となり、連続回数が0へ戻る。これは制御譲渡とカウンタの検証であり、READYキュー順序、全タスクの公平性、実時間応答上限は検証しない | `{GOTCHA-SCHED-01}`, `{Challenge_CspHandoffStarvation}` |
| TEST-COOS-08 | 割り込み通知を協調境界で処理して待機タスクを起床する | vSoCランタイムタスクが`vector_id`待ち | 固定5ワードの`interrupt-event`を`notify_interrupt`へ渡し、スケジューラにイベントを処理させる | 通知イベントが協調境界で処理され、5ワードを対応する待機タスクへ引き渡したうえでREADYとなって再開する | 直交表 ケース8, `{GLOBAL_InterruptWakeup}` |
| TEST-COOS-09 | 割り込みイベントFIFO満杯時のドロップ | FIFO満杯 | 原因レコードを追加で`notify_interrupt` | イベントがドロップされ、ドロップカウンタがインクリメントされる（os_scheduler.md `notify-interrupt`の挙動と共通） | os_scheduler.md `notify-interrupt` |
| TEST-COOS-10 | READYタスクがない場合のアイドル検出 | 少なくとも1タスクがブロック中で、READYキューと割り込みイベントFIFOが空 | スケジューラを実行する | アイドルフックが呼び出される | `os_coos.md` §4.1, pysim `test_coos_10_idle_detection_when_all_blocked` |
| TEST-COOS-11 | ランデブー完了時の単一所有 | 送信側Aが値を保持して待機中 | 受信側Bを到着させ、ランデブー直後の両タスクを確認 | Aの値がクリアされ、Bが値を保持する | [`coos_channel_model.py`](docs/components/tier1_core/formal/coos_channel_model.py), pysim `test_coos_11_no_double_ownership_sanity` |
| TEST-COOS-12 | ブロックタスク終了時の待機登録解除 | タスクがselect受信待ち、割り込み待ち、または現在実行中 | `task_killed`を呼び、select対象チャネルと割り込みイベントを処理する | selectグループ内の全チャネル登録とIRQ登録が解除され、タスクは`TERMINATED`のまま再起床しない。コルーチン参照も破棄される。未登録IDおよび終了済みタスクへの要求は`false`を返し、現在実行中のタスクはアサーションで拒否する | `os_coos.md` §4.3, pysim `test_coos_12_task_killed_removes_csp_and_irq_wait_registrations` |
| TEST-COOS-13 | 原因レコードのFIFO順序保持 | 複数の`interrupt-event`を同一FIFOへ投入 | 異なる`cause_code`とpayloadを順に投入してドレイン | 受付順と同じ順序でイベントが観測され、5ワードが欠落・混在しない | `{GLOBAL_InterruptWakeup}` |
| TEST-COOS-14 | 未登録待機先のイベントドロップ | `vector_id`に対応する待機タスクなし | 原因レコードを投入してドレイン | ゲストや無関係なタスクを起床せず、イベントをドロップして診断カウンタだけを更新する | `{GLOBAL_InterruptWakeup}` |
| TEST-COOS-15 | 割り込み再スケジュール世代の一巡 | READYタスクが複数存在し、割り込みイベントを受け付ける | スケジューラを協調境界まで進める | 同一保留バーストのイベントは一世代へ集約され、対象タスクは各一回だけ世代を観測してから通常のREADY巡回へ戻る | `{ADR_InterruptRescheduleGeneration}` |
| TEST-COOS-16 | 世代保留中のCSP直接ハンドオフ停止 | `reschedule_pending=true` でCSPランデブーが成立する | `channel_send`/`channel_recv`の結果を観測する | 値の所有権移譲は成立するが、直接切替は行わず`YIELD`を返す | `{ADR_InterruptRescheduleGeneration}` |

### 2.3 形式検証の確認項目

以下は実行時の単体テストケースではなく、[`coos_channel_model.py`](docs/components/tier1_core/formal/coos_channel_model.py)に対する証明・変異検査の義務である。`TEST-COOS-*` のIDとは分離する。

| 形式検証ID | 検証項目 | 性質 | 期待結果 |
| :--- | :--- | :--- | :--- |
| FORMAL-COOS-01 | 非循環チャネル依存下のデッドロック不在 | `AG(Not(deadlock))` | 正常モデルで成立し、循環待ちを許す変異モデルで反証される |
| FORMAL-COOS-02 | ランデブー中の単一所有 | `AG(Not(sender_owns AND receiver_owns))` | 正常モデルで成立し、原子的移譲を壊す変異モデルで反証される |
| FORMAL-COOS-03 | ブロックした送信側・受信側の再開 | 各待機状態から対応する相手とのランデブー後に再開する | 正常モデルで成立し、循環待ち変異モデルで反証される |
| FORMAL-COOS-04 | ハンドオフ上限時のスケジューラ復帰 | `AG(at_max_limit -> AF(main_loop))` | 正常モデルで成立し、強制yieldを外す変異モデルで反証される |
| FORMAL-COOS-05 | ハンドオフ上限時のカウンタ初期化と末尾登録 | `AG(at_max_limit -> AF(counter_reset AND target_ready_tail))` | 正常モデルで成立し、強制yieldを外す変異モデルで反証される |
| FORMAL-COOS-06 | 先行READYタスクへの実行機会 | `AG(at_max_limit -> AF(other_ready_dispatched))` | 既存READYタスクが1つあるモデルで成立し、強制yieldを外す変異モデルで反証される。実時間の応答上限は対象外 |
| FORMAL-COOS-07 | 割り込み再スケジュール世代の完了 | `AG(reschedule_pending -> AF(generation_complete))` | 正常モデルでは対象スナップショットとタスク観測を経て成立し、対象確定ガードを外した変異モデルで反証される。モデル: [`coos_channel_model.py`](docs/components/tier1_core/formal/coos_channel_model.py) |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-COOS-01 | チャネル自身が送信値を保持しないこと | 送信側がブロック中 | チャネルのデータ状態と送信側フレームを確認 | チャネルに送信値が存在せず、値は送信側フレームにのみ保持される。受信側が到着すると直接移譲される。**実装の勘所**: 特定フィールド名の不在ではなく、チャネルが値を保持しないという契約を検証する | `os_coos.md`, `{ADR_RendezvousChannel}` |
| GOTCHA-COOS-02 | 1チャネル1待機者の強制（多重待機はプログラミングエラー） | チャネルに既に送信側Aが待機中 | 別タスクBが同チャネルへ送信を試行 | 実行時アサーションで違反を検出する。**実装の勘所**: 待機列を設けてキューイングすると、実行時メモリ割り当て（malloc）や優先度逆転の複雑さを招く | `os_coos.md`, `{Orthogonal_Design}` |
| GOTCHA-COOS-03 | ISR コンテキストとスケジューラ境界の分離 | タスクが IRQ 待ちでブロック中 | ISR 模擬ルーチンから `notify_interrupt` を呼び、スケジューラにイベントを処理させる | イベントは協調境界でドレインされ、対応する待機タスクだけがREADYになる。**実装の勘所**: ISR内でタスク状態や実行可能キューを直接変更すると、クリティカルセクションが肥大化する | `os_coos.md`, `{ISR_Safety}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義されたテストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `co_mem`（メモリパーティション貸与）はこの仕様書の対象外（`system_memory.md`/`runtime_memory.md`側）。
- C++20コルーチンの対称遷移そのもののレイテンシ特性は [`direct_context_switch_bench.py`](docs/components/tier1_core/benchmarks/direct_context_switch_bench.py) が正本。
