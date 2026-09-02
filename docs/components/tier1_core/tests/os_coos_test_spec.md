# COOS (CSPチャネル) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/os_coos.md`
参考実装: `docs/components/tier1_core/concepts/coos_concept.py`

ホーアCSPに基づく**バッファなし同期ランデブーチャネル**（`{ADR_RendezvousChannel}`）と、直接コンテキストスイッチ（CSP Handoff）、割り込みイベント駆動起床、アイドル検知の振る舞いを定義する。

### 2.1 CSP通信と状態遷移 直交表マトリクス (`os_coos.md` §6.2)
<!-- traceability: {CSP_Handoff} {ADR_RendezvousChannel} {GLOBAL_InterruptWakeup} -->

チャネル通信時のタスク状態とスケジューラの挙動を検証する組み合わせ直交表。チャネルは値を保持しないため（`{ADR_RendezvousChannel}`）、状態は「待機者なし / 送信待機 / 受信待機」の3値のみを取り、バッファ満杯ケースは存在しない。

| ケース | 自タスク要求 | チャネル待機者 | 相手状態 | 期待される動作 (自) | 期待される動作 (他) | 対応テストID |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | SEND | なし | - | `SUSPENDED_CSP` へ遷移。値は自フレームに保持 | (なし) | COOS-01 |
| 2 | SEND | 受信待機 (RECV) | `SUSPENDED_CSP` | **READY へ遷移** | **READY へ遷移し、値の所有権を取得** | COOS-04 |
| 3 | RECV | なし | - | `SUSPENDED_CSP` へ遷移 | (なし) | COOS-03 |
| 4 | RECV | 送信待機 (SEND) | `SUSPENDED_CSP` | **READY へ遷移し、値の所有権を取得** | **READY へ遷移。自フレームの値は無効化** | COOS-02 |
| 5 | SEND | 送信待機 (SEND) | `SUSPENDED_CSP` | **設計上到達不能**（1チャネル1待機者違反をアサーション検出） | - | COOS-05 |
| 6 | RECV | 受信待機 (RECV) | `SUSPENDED_CSP` | **設計上到達不能**（同上） | - | COOS-05 |
| 7 | ハンドオフ上限到達 | 受信/送信待機 | `SUSPENDED_CSP` | **READY へ遷移し、対称遷移せずスケジューラへ復帰** | **READY へ遷移し READY キュー末尾へ** | COOS-07 |
| 8 | ISR通知 | - | `SUSPENDED_CSP`/`READY` | (継続) | **INT イベント投入 → yield点でドレイン → READY 遷移** | COOS-08 |

### 2.2 テストケース詳細一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| COOS-01 | 送信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクAが`channel_send`を呼ぶ | Aは`SUSPENDED_CSP`に遷移し、値はAのフレームに保持されたまま（チャネルには値が存在しない） | §2.1 直交表 ケース1 |
| COOS-02 | 受信者到着でランデブー成立（送信側視点） | Aが送信待機中(SEND) | タスクBが`channel_recv`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る。Aの`pending_val`は破棄される（二重所有防止） | §2.1 直交表 ケース4 |
| COOS-03 | 受信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクBが`channel_recv`を呼ぶ | Bは`SUSPENDED_CSP`に遷移 | §2.1 直交表 ケース3 |
| COOS-04 | 送信者到着でランデブー成立（受信側視点） | Bが受信待機中(RECV) | タスクAが`channel_send`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る | §2.1 直交表 ケース2 |
| COOS-05 | 1チャネル1待機者の強制（同方向多重待機は不可能） | Aが送信待機中(SEND) | 別タスクCが同じチャネルへ`channel_send` | 到達不能ケースとして`assert`で検出される（設計違反） | §2.1 直交表 ケース5/6 |
| COOS-06 | CSP Handoffは対称遷移でREADYキューの先頭に挿入 | ランデブー成立 | `_handoff_or_yield`の返り値を観測 | `consecutive_handoffs < FB_CONF_MAX_CONSECUTIVE_HANDOFFS` の間は`("DIRECT_SWITCH", target)`を返し、target が READYキュー先頭に挿入される | `{CSP_Handoff}` |
| COOS-07 | 連続ハンドオフの上限でスケジューラへ復帰 | `consecutive_handoffs`が`FB_CONF_MAX_CONSECUTIVE_HANDOFFS`（既定4）に到達 | さらにハンドオフが発生する状況を作る | `consecutive_handoffs`が0にリセットされ、`("YIELD", None)`を返してメインループへ復帰する（対称遷移しない） | §2.1 直交表 ケース7, `{Challenge_CspHandoffStarvation}` |
| COOS-08 | 割り込みは状態を直接変更しない | タスクがirq_id待ち | `notify_interrupt(irq_id)`を呼ぶ | 呼び出し直後はイベントキューに追加されるのみで、タスク状態は変化しない。`drain_interrupts`実行後に初めてREADYへ遷移する | §2.1 直交表 ケース8, `{GLOBAL_InterruptWakeup}` |
| COOS-09 | 割り込みイベントキュー枯渇時のドロップ | (該当する場合)イベントキュー満杯 | 追加でnotify_interrupt | ドロップされ、ドロップカウンタがインクリメントされる（os_scheduler.md `notify-interrupt`のキュー満杯時挙動と共通） | os_scheduler.md 5.1 notify-interrupt |
| COOS-10 | アイドル検出はREADYキュー空 かつ 全タスクBLOCKED | 全タスクをSUSPENDED_CSP/BLOCKEDにする | `run_step`を実行 | READYキューが空である限りアイドルフックが呼ばれる（`idle_hook_called`） | 4.1 Idle Detection |
| COOS-11 | 二重所有不在（形式検証と整合するサニティ確認） | ランデブー成立の瞬間を観測 | 送信側の値保持フィールドと受信側の値保持フィールドを同時にチェック | どの時点でも送信側・受信側が同時に同じ値を「所有」している状態が存在しない | `../formal/coos_channel_model.py` AG(Not(double_owned)) |
| COOS-12 | デッドロック不在（クライアント・サーバ規律） | 循環しないチャネル依存グラフを構築 | 一連の送受信を実行 | 循環待ちが発生しない（形式モデルの結果が実装のふるまいと矛盾しない） | `../formal/coos_channel_model.py` AG(Not(deadlock)) |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| COOS-GOTCHA-01 | チャネル自身のデータスロット完全不在と単一所有権 | 送信側がブロック中 | チャネル構造体のフィールドを検証 | チャネル自身はメッセージバッファ（値スロット）を一切持たず（`not hasattr(ch, "buffer")`）、値は送信側フレーム内にのみ留まる。受信側が到着した瞬間に直接手渡しされ、二重所有が原理的に発生しない。**実装の勘所**: チャネルに値スロットを設けると、バッファオーバーフローの管理やロールバック処理が必要となり、ゼロコピー保証が失われる | `os_coos.md` §3.3, `{ADR_RendezvousChannel}` |
| COOS-GOTCHA-02 | 1チャネル1待機者の強制（多重待機はプログラミングエラー） | チャネルに既に送信側Aが待機中 | 別タスクBが同チャネルへ送信を試行 | 即座にアサーション違反で停止する。**実装の勘所**: 待機列を設けてキューイングすると、実行時メモリ割り当て（malloc）や優先度逆転の複雑さを招く。1チャネル1待機者を設計不変条件として静的に強制する | `os_coos.md` §6.2, `{Orthogonal_Design}` |
| COOS-GOTCHA-03 | ISR コンテキストとスケジューラ境界の分離 | タスクが IRQ 待ちでブロック中 | ISR 模擬ルーチンから `notify_interrupt` を呼び出し | 呼び出し時点ではタスク状態は BLOCKED のまま変化せず、イベントキューに記録されるのみ。スケジューラの `run_step` 開始時に `drain_interrupts` が実行されて初めて READY に遷移する。**実装の勘所**: ISR 内でスケジューラ状態や優先度キューを直接書き換えると、ロック競合やクリティカルセクションの肥大化を招く | `os_coos.md` §4.2, `{ISR_Safety}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義されたテストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `co_mem`（メモリパーティション貸与）はこの仕様書の対象外（platform_memory.md側）。
- C++20コルーチンの対称遷移そのもののレイテンシ特性は `../benchmarks/direct_context_switch_bench.py` が正本。
