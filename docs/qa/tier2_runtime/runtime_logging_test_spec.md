# システムロギング テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md)
参考実装: [`logging_concept.py`](docs/components/tier2_runtime/concepts/logging_concept.py)

**適用範囲外の明記**: `runtime_logging.md` はビルド時に辞書登録された固定フォーマットの内部ログを対象とする。ゲストの `wasi:cli/stdout` は別経路（`interface_wit.md` の `console-output`、`fireball://hal/stdout/0`）で扱う。TEST-LOG-14は、独立したTier 1 printkイベントが同じ可変長レコード形式を共有する境界検査である。

## 2. テストケース一覧
<!-- traceability: {BufferedLogging} {WasmCodeSectionPC} {GOTCHA-LOG-01} {GOTCHA-LOG-04} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-LOG-01 | 最大4引数レコードの符号化 | 辞書オフセットと4個のu32引数を指定 | `log_event`→`flush`後のバイト列を検査 | level:u8、辞書ID:u24 LE、4引数:u32 LEの順に20バイトが出力され、ログ呼び出し中に文字列展開しない | runtime_logging.md 4.2 |
| TEST-LOG-02 | ホスト側辞書展開と未登録ID | 既知のoffsetを含む可変長レコードと未知IDのヘッダを用意 | ホストデコーダーで展開 | 既知IDは辞書書式で展開し、未知IDではassertで停止する | runtime_logging.md 4.2 |
| TEST-LOG-03 | 辞書書式の静的検証 | 辞書ビルダーへ数値指定子と不正な書式を渡す | 辞書生成を実行 | 最大4個の数値指定子だけ受理し、`%s`/`%08s`/`%*d`/位置指定子/`%f`を生成時に拒否する | GOTCHA-LOG-01 |
| TEST-LOG-04 | 固定長リングバッファ・オーバーライト | バッファ容量4に連番0〜3を投入 | 4、5を追加してflushし、その後6を投入する | 0、1が除外され、2、3、4、5が順序どおり出力される。追加2件は`OVERWRITTEN`、上書き累計は2となる。flush後の6は`SUCCESS`となり、累計は増えない | `{BufferedLogging}`、`test_log_04_logger_overwrites_oldest_and_preserves_complete_fifo_order` |
| TEST-LOG-05 | ログレベルフィルタリング | `min_level=WARN`に設定 | DEBUG/INFOレベルでlog_event | `LogResult.FILTERED`を返し、リングバッファに積まれない | Logger.log_event |
| TEST-LOG-06 | idle_hookでのフラッシュ | ログを複数件queueした状態 | `System.scheduler.run_until_idle()`で実idle hookを発火する | 全レコードがSinkへ順序どおり出力され、バッファが空になる。単体の直接flushとscheduler結線の検査を分ける | `{GLOBAL_IdleDetection}` |
| TEST-LOG-07 | flush中の割り込み | 連番0〜3を投入し、2件単位のバッチ境界で割り込みを通知する | flush後に出力と残存先頭を検査し、再度flushする | 通知は先頭2件の送信後に1回行われる。最初の出力は0、1となり、2、3が残る。再開後の出力は2、3となり、バッファが空になる | `{InterruptibleFlush}`、`test_log_07_interrupt_preserves_unsent_second_batch_in_fifo_order` |
| TEST-LOG-08 | ログ投入時の事前割当て | ロガー生成後のエントリ配列を保持 | 複数回log_eventとflushを実行 | 既存の固定長レコードを再利用し、レコードオブジェクト・引数タプル・整形済み文字列をログ投入ごとに生成しない | `{META_ZeroOverhead}` |
| TEST-LOG-09 | ダングリングポインタ（実行時文字列）の禁止 | 実行時に構築した任意長文字列をdict_offset経由で渡そうとする | ログAPIの引数型を確認 | 内部ログAPIは固定オフセット+u32引数4個のみを受け付け、任意長文字列やポインタ相当の値を渡せない | `logging_concept.py` `test_logger_cannot_carry_a_runtime_string_but_console_can` |
| TEST-LOG-10 | Runtimeイベントから内部ログへの通知 | ロガーとRuntime Event Loggerが注入済み | 関数イベントを通知し、ログをflushして固定レコードを復号する | 内部Observerが辞書オフセットと関数IDをキューイングし、イベント名がホスト側で復号される | pysim `test_runtime_events_are_queued_as_fixed_dictionary_records` |
| TEST-LOG-11 | ログ辞書ストレージ所有権分離 | 外部で固定辞書ストレージを定義 | ログ辞書初期化 | ログ辞書および非所有ビューは外部ストレージを参照し、実行中の辞書追加を提供しない | logging_concept.py `test_logger_storage_ownership_separation` |
| TEST-LOG-12 | インタープリタ実行時トラップの診断ログ出力 | Python参照とNativeの双方で関数index=1の`local.get 0; local.get 1; i32.div_s; end`にロガーを結線する | 直接呼出しと別callerからの呼出しで第2引数を0とし、trap後にflushする | Code section payloadは関数数、unused関数のbody、対象bodyのsizeとlocals宣言を含むため、`i32.div_s`のPCはpayload先頭相対`0x0000000A`となる。ERROR、イベントID=`0x030F`、PC=`0x0000000A`、PC引数1個の8バイトが出力される。ホスト展開も同じPCを示す | 4.2.1、`GOTCHA-LOG-04`、`{WasmCodeSectionPC}`、`test_log_12_interpreter_trap_diagnostic_logging` |
| TEST-LOG-13 | トラップログ辞書のインタープリタ・ロガー間同期 | `interpreter.TRAP_LOG_EVENTS` と `logger.STANDARD_DIAGNOSTIC_EVENTS` を突合 | 全17トラップ原因コードを走査 | 各イベントIDおよびフォーマット文字列が両モジュール間で完全一致する（登録漏れ・字面ずれの機械的検出） | 4.2.1, `GOTCHA-LOG-04` |
| TEST-LOG-14 | Tier 1 printkイベントとのレコード互換性 | COOS/IPCの異常条件と共有printk Sinkを用意する | 各操作の拒否結果とSinkへ同期出力された全レコードを照合する | COOS/IPCはTier 2 Loggerを呼ばず、同じ可変長レコードをprintkの書込み時に復号して同期出力する。全8イベントの改行付きテキストと未使用引数の省略を照合する | [`printk.md`](docs/components/tier1_core/printk.md)、`test_printk_14_coos_and_ipc_diagnostics_preserve_event_ids_and_arguments` |
| TEST-LOG-15 | 辞書IDによる転送長と境界の復元 | 引数0〜4個の辞書を送受信側で同一内容から生成する | 異なる長さを連続送信し、独立した期待バイト列と全展開結果を照合する | 各長さは4、8、12、16、20バイトとなる。未使用スロットの非0値は送信されず、使用引数の0値は省略されない。`%%`は引数数に含めない。送信経路で書式を走査しない | runtime_logging.md 4.2、`test_log_15_variable_records_share_build_time_argument_counts` |
| TEST-LOG-16 | 境界を決定できない入力の拒否 | 未登録IDと欠損レコードを用意する | 送信側の状態と受信側の拒否を検査する | 未登録IDは投入前にassertで拒否され、リング・Sinkに副作用がない。受信側は未知ID、不正レベル、ヘッダ欠損、引数欠損でassertとなる | runtime_logging.md 4.2、`test_log_16_invalid_records_are_rejected_without_guessing_boundaries` |

| TEST-LOG-17 | printk書込み時の復号 | 同一辞書をロガーとprintkへ注入する | ログ投入後にflushし、物理Sinkの出力とリング状態を照合する。不正レコードと出力拒否も検査する | 投入時は展開せず、書込み時に改行付きUTF-8テキストを出力する。戻り値は入力長となる。0〜4引数、全レベル、`%%`、日本語を照合する。不正入力は出力前にassertとなり、出力拒否時は未送信エントリを保持する | runtime_logging.md 4.2、`test_log_17_printk_decodes_at_write_boundary` |

| TEST-LOG-18 | printkのBase64バイナリ送信 | 共有printk Sinkと任意のバイナリを用意する | `write_base64`の戻り値、全出力バイト、復元結果、作業領域を照合する。出力拒否と部分書込みも検査する | 標準Base64の文字集合と末尾パディングを使い、末尾LFを1個付ける。空データはLFだけとなる。入力0〜2バイトの端数、15バイトのチャンク境界、全256バイト値を復元できる。途中パディングや改行はなく、固定領域を再利用する。出力失敗はassertで停止する | [`printk.md`](docs/components/tier1_core/printk.md) 2.3、`test_log_18_printk_base64_known_vectors` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-LOG-01} {GOTCHA-LOG-02} {GOTCHA-LOG-03} {GOTCHA-LOG-04} {DeterministicRingBuffer} {InterruptibleFlush} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GOTCHA-LOG-01` | 実行時文字列ポインタの完全排除（ダングリングポインタ防止） | ログAPI呼び出し | 実行時文字列ポインタの受け渡しを試行 | ログAPIは固定長辞書オフセットと u32 スカラー引数4個のみを受け付け、任意長文字列を直接埋め込む手段が存在しない。 | [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md) §4.1, `GOTCHA-LOG-01` |
| `GOTCHA-LOG-02` | リングバッファ満杯時の最古上書き（システム非ブロック不変条件） | リングバッファが満杯 | さらに `log_event` を実行 | エラーやブロックを起こさず、最も古いエントリを上書きして直近のログを保存する。 | [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md) §4.1, `GOTCHA-LOG-02` {DeterministicRingBuffer} |
| `GOTCHA-LOG-03` | バッチ完了後の協調復帰 | flush 実行中 | `batch_size`件以内の同期書込み完了後に`interrupt_pending()`がTrueを返す | 次バッチを開始せず、残りのログを保持してスケジューラへ制御を戻す。書込み途中の中断や実時間応答上限は判定しない | [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md), `GOTCHA-LOG-03`, {InterruptibleFlush} |
| `GOTCHA-LOG-04` | トラップ発生箇所での診断情報捕捉順序 | インタープリタがトラップを検知 | 呼出しフレーム解体前にCode section payload相対`unified_pc`とトラップ原因コードを確定してロガーへ渡す | 診断ログにモジュール内命令位置が記録される。PC単独はモジュールを特定しない。 | [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md) §4.1 / §4.2.1, [`interpreter.md`](docs/components/tier2_runtime/interpreter.md), `GOTCHA-LOG-04` |

## 3. テスト検証実績と網羅状況

実行コマンドを示す。

```bash
uv run pytest -q experiments/pysim/qa/tier2_runtime/test_logging.py
```

概念コードの`test_printk_decodes_during_flush`は、書込み時の復号と改行付きテキストを検査する。概念コードの`test_printk_base64_uses_bounded_chunks`は、チャンク境界と空データ、出力拒否を検査する。TEST-LOG-17はpysimの物理出力境界を検査する。

同ファイルはTEST-LOG-01〜08、11〜18に対応する。旧コードの辞書書式検証、上書き、所有権、printkレコード互換性、割り込みのIDを、それぞれ03、04、11、14、07へ合わせた。

TEST-LOG-01はu24 IDと4個の異なるu32を独立した20バイト列で照合する。辞書展開のspyで、投入とflush中の展開が0回であることを検査する。TEST-LOG-02はエンコーダーを使わない既知のバイト列でホスト側の展開と未知IDの拒否を検査する。TEST-LOG-15/16は転送長の全5境界と不正入力を検査する。TEST-LOG-04/07は全出力順序と残存状態を検査する。TEST-LOG-05/06はレベル境界と空flushの副作用を検査する。

TEST-LOG-12はPython参照とNativeで、直接呼出しとcallee呼出しの4構成を実行する。関数indexと命令オフセットはともに非0である。レコードのサイズや文字列の一部だけを根拠とせず、原因ID、PC、未使用引数の省略とホスト展開を照合する。TEST-LOG-13は登録間の同期検査であり、全トラップの実行検査ではない。

## 4. 未検証・スコープ外

- TEST-LOG-08は固定レコードの再利用を検査する。引数タプルや一時文字列を含む全割当ての不在は、同一性検査だけでは証明しない。
- TEST-LOG-09の実行時文字列禁止は、本ファイルでは未検査である。コンセプト側の合格だけでpysimの実行経路を検証済みとしない。
- TEST-LOG-10はRuntime Event Logger側の`test_runtime_events_are_queued_as_fixed_dictionary_records`で扱う。連続する2 batchの3レコードを、既存4引数の全wire列と展開結果で照合する。
- `wasi:cli/stdout`/`stderr`（コンソール生バイト出力経路）は対象外。[`interface_wit_test_spec.md`](docs/qa/tier3_platform/interface_wit_test_spec.md)を参照。
- Tier 1 `printk`の実機物理デバイスの挙動は本テストの対象外である。pysimの復号と出力Sink境界はTEST-LOG-17で検査する。RuntimeロガーにDMA開始・完了割り込み処理は要求しない。

### 2026-10-02のidle結線確認

TEST-LOG-06の統合入口は`test_vsoc.py::test_idle_02_logging_flush_on_idle`である。
ログ投入直後のSink出力0、実scheduler idle後の全2件の順序、リング空を検査する。
`flush()`の直接呼出しだけでscheduler結線を合格にしない。
