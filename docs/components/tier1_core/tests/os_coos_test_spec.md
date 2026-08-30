# COOS (CSPチャネル) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/os_coos.md`
参考実装: `docs/components/tier1_core/concepts/coos_concept.py`

ホーアCSPに基づく**バッファなし同期ランデブーチャネル**（`{ADR_RendezvousChannel}`）と、直接コンテキストスイッチ（CSP Handoff）、割り込みイベント駆動起床、アイドル検知の振る舞いを定義する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| COOS-01 | 送信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクAが`channel_send`を呼ぶ | Aは`SUSPENDED_CSP`に遷移し、値はAのフレームに保持されたまま（チャネルには値が存在しない） | 6.2 ケース1 |
| COOS-02 | 受信者到着でランデブー成立（送信側視点） | Aが送信待機中(SEND) | タスクBが`channel_recv`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る。Aの`pending_val`は破棄される（二重所有防止） | 6.2 ケース4 |
| COOS-03 | 受信が先着した場合はSUSPENDED_CSP | チャネルに待機者なし | タスクBが`channel_recv`を呼ぶ | Bは`SUSPENDED_CSP`に遷移 | 6.2 ケース3 |
| COOS-04 | 送信者到着でランデブー成立（受信側視点） | Bが受信待機中(RECV) | タスクAが`channel_send`を呼ぶ | A・B双方がREADYに遷移し、値の所有権がAからBへ移る | 6.2 ケース2 |
| COOS-05 | 1チャネル1待機者の強制（同方向多重待機は不可能） | Aが送信待機中(SEND) | 別タスクCが同じチャネルへ`channel_send` | 到達不能ケースとして`assert`で検出される（設計違反） | 6.2 ケース5/6、注1 |
| COOS-06 | CSP Handoffは対称遷移でREADYキューの先頭に挿入 | ランデブー成立 | `_handoff_or_yield`の返り値を観測 | `consecutive_handoffs < FB_CONF_MAX_CONSECUTIVE_HANDOFFS` の間は`("DIRECT_SWITCH", target)`を返し、target が READYキュー先頭に挿入される | `{CSP_Handoff}` |
| COOS-07 | 連続ハンドオフの上限でスケジューラへ復帰 | `consecutive_handoffs`が`FB_CONF_MAX_CONSECUTIVE_HANDOFFS`（既定4）に到達 | さらにハンドオフが発生する状況を作る | `consecutive_handoffs`が0にリセットされ、`("YIELD", None)`を返してメインループへ復帰する（対称遷移しない） | 6.2 ケース7、§6.1 メインループ復帰保証 |
| COOS-08 | 割り込みは状態を直接変更しない | タスクがirq_id待ち | `notify_interrupt(irq_id)`を呼ぶ | 呼び出し直後はイベントキューに追加されるのみで、タスク状態は変化しない。`drain_interrupts`実行後に初めてREADYへ遷移する | 6.2 ケース8、注3 |
| COOS-09 | 割り込みイベントキュー枯渇時のドロップ | (該当する場合)イベントキュー満杯 | 追加でnotify_interrupt | ドロップされ、ドロップカウンタがインクリメントされる（os_scheduler.md `notify-interrupt`のキュー満杯時挙動と共通） | os_scheduler.md 5.1 notify-interrupt |
| COOS-10 | アイドル検知はREADYキュー空 かつ 全タスクBLOCKED | 全タスクをSUSPENDED_CSP/BLOCKEDにする | `run_step`を実行 | READYキューが空である限りアイドルフックが呼ばれる（`idle_hook_called`） | 4.1 Idle Detection |
| COOS-11 | 二重所有不在（形式検証と整合するサニティ確認） | ランデブー成立の瞬間を観測 | 送信側の値保持フィールドと受信側の値保持フィールドを同時にチェック | どの時点でも送信側・受信側が同時に同じ値を「所有」している状態が存在しない | `../formal/coos_channel_model.py` AG(Not(double_owned)) |
| COOS-12 | デッドロック不在（クライアント・サーバ規律） | 循環しないチャネル依存グラフを構築 | 一連の送受信を実行 | 循環待ちが発生しない（形式モデルの結果が実装のふるまいと矛盾しない） | `../formal/coos_channel_model.py` AG(Not(deadlock)) |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `co_mem`（メモリパーティション貸与）はこの仕様書の対象外（platform_memory.md側）。
- C++20コルーチンの対称遷移そのもののレイテンシ特性は `../benchmarks/direct_context_switch_bench.py` が正本。
