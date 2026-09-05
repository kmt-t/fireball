# システムロギング テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_logging.md`](docs/components/tier1_core/system_logging.md)
参考実装: [`logging_concept.py`](docs/components/tier1_core/concepts/logging_concept.py)

**適用範囲外の明記**: `system_logging.md` 冒頭は「本コンポーネントが扱うのはビルド時に辞書登録された固定フォーマットの内部状態ログのみである」と明示し、ゲストの `wasi:cli/stdout`/`stderr`（`print`/`eprint`）は別経路（`interface_wit.md` `console-output`）で扱うとしている。したがって本テスト仕様書は **辞書ベースの内部ログ** のみを対象とし、生バイト出力（`ConsoleOutput`）は `../../tier1_interface/tests/interface_wit_test_spec.md` 側の責務とする。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOG-01 | 辞書オフセット+4引数のフォーマット | 辞書に`0x01: "System booted in %d ms (RAM free: %d bytes)"`を登録 | `log_event`→`flush`後の文字列を確認 | `arg0`,`arg1`が正しく埋め込まれる | 4.1 `log_event`, logging_concept.py |
| LOG-02 | 未登録オフセットのフォールバック表示 | 辞書に存在しないoffsetを指定 | `format`を呼ぶ | `UNKNOWN_FORMAT_OFFSET_<offset>`のようなフォールバック文字列を返す（クラッシュしない） | logging_concept.py `LogDictionary.format` |
| LOG-03 | 4個未満の`%`指定子を持つフォーマット文字列 | 辞書に`%d`が1個だけのフォーマットを登録し、4引数を渡す | `log_event`→`flush` | Pythonの`%`演算子がTypeErrorを起こさず、正しくフォーマットされる（C `printf`の可変長引数無視動作と等価） | 既知のバグ修正 |
| LOG-04 | 固定長リングバッファ・オーバーライト | バッファ容量（例:4）を満杯にする | 5件目をlog_event | 最古のエントリが上書きされ、`overwrite_count`がインクリメントされる。戻り値`"OVERWRITTEN"` | `{BufferedLogging}` |
| LOG-05 | ログレベルフィルタリング | `min_level=WARN`に設定 | DEBUG/INFOレベルでlog_event | `"FILTERED"`を返し、リングバッファに積まれない | Logger.log_event |
| LOG-06 | idle_hookでのフラッシュ | ログを複数件queueした状態 | idle_hook相当（`flush()`）を呼ぶ | バッファ内の全エントリが`transport`（DMA相当）へ一括転送され、バッファが空になる | `{GLOBAL_IdleDetection}` |
| LOG-07 | flush中の割り込み | `interrupt_pending`が一定回数後にTrueを返すコールバックを渡す | flushを実行 | 割り込み発生時点で処理を中断し、残りのエントリはバッファに残る | logging_concept.py `test_logger_flush_interruption` |
| LOG-08 | tick（timestamp）の単調増加 | 複数回log_event | 各エントリのtimestamp_tickを確認 | 呼び出し順に単調増加する | LogEntry.timestamp_tick |
| LOG-09 | ダングリングポインタ（実行時文字列）の禁止 | 実行時に構築した任意長文字列をdict_offset経由で渡そうとする | ログAPIの引数型を確認 | ログAPIは固定オフセット+u32引数4個のみを受け付け、任意長文字列やポインタ相当の値を安全に埋め込む手段が存在しないことを確認する（`{DictionaryBasedIPC}`の「実行時の辞書追加は不可」の裏付け） |  README `test_logger_cannot_carry_a_runtime_string_but_console_can` |
| LOG-10 | IPC経由でのログ要求（`fireball://logging/system/0`） | IPCルータに`logging`宛のルートが存在する状態（ギャップ項目参照） | `handle_ipc_message`相当のペイロード（level/dict_offset/arg0-3のdict）でIPC_SENDする | ログが`log_event`と同じ結果でキューイングされる | logging_concept.py `handle_ipc_message`, `test_logger_ipc_message_handling` |
| LOG-11 | ログ辞書ストレージ所有権分離 | 外部で実体ペア配列（ROM/静的バッファ）を定義 | ログ辞書初期化 | ログ辞書および非所有ビューが外部ストレージを参照し、実体を自己所有・複製しない | logging_concept.py `test_logger_storage_ownership_separation` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LOG-GOTCHA-01 | 実行時文字列ポインタの完全排除（ダングリングポインタ防止） | ログAPI呼び出し | 実行時文字列ポインタの受け渡しを試行 | ログAPIは固定長辞書オフセットと u32 スカラー引数4個のみを受け付け、任意長文字列を直接埋め込む手段が存在しない。**実装の勘所**: ログメッセージにポインタを含めると、対象タスクがクラッシュまたは終了した後にロガーが不正メモリを参照（Use-After-Free）する | `system_logging.md` , `{DictionaryBasedIPC}` |
| LOG-GOTCHA-02 | リングバッファ満杯時の最古上書き（システム非ブロック不変条件） | リングバッファが満杯 | さらに `log_event` を実行 | エラーやブロックを起こさず、最も古いエントリを上書きして直近のログを保存する。**実装の勘所**: ログ出力でタスクをブロックさせると、高負荷時や異常発生時にシステム全体がデッドロックに陥る | `system_logging.md` , `{DeterministicRingBuffer}` |
| LOG-GOTCHA-03 | 転送ループの割り込み即時応答性 | flush 実行中 | `interrupt_pending()` が True を返す | バッファ全フラッシュを強行せず、現在のエントリ送信完了時点で直ちにループを抜けてスケジューラへ制御を戻す。**実装の勘所**: ログフラッシュをアトミックに実行すると、長大なログ転送中に外部割り込みレイテンシが大幅に悪化する | `system_logging.md` , `{InterruptibleFlush}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `wasi:cli/stdout`/`stderr`（`console-output`）は対象外。`../../tier1_interface/tests/interface_wit_test_spec.md`を参照。
- 物理DMA転送そのもの（`MockHALTransport.start_dma`相当）の実ハードウェア挙動は`platform_hal.md`側。
