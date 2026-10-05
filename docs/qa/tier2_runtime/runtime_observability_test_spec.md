# Runtime 観測イベント テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

イベント記録、転送ABI、安全点配送を対象とする。JIT履歴は対象外とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-OBS-01 | 固定レイアウト | 対象C++ ABI | サイズ、整列、全フィールド位置を調べる | 32バイト、8バイト境界で、全位置が一致する | 正本のレコード定義 |
| TEST-OBS-02 | 境界イベント | Sink有効 | 登録、call、JIT、host call、yield、trapを実行する | 種別、識別子、相関ID、終了理由が一致し、命令ごとの通知をしない | 正本のイベント種別 |
| TEST-OBS-03 | 容量超過 | 容量N | N+K件を記録し、読出し・再充填する。K≤NとK>Nを試す | 最新N件を発行順に保持し、累積欠落数へKを加える | `{ADR_RuntimeEventRetention}` |
| TEST-OBS-04 | batch復号 | 既知イベント列 | little-endian batchを復号する | ヘッダ、件数、時刻情報、全イベント値が一致し、C++配置をPython APIへ露出しない | 正本の転送ABI |
| TEST-OBS-05 | export拒否 | 未読イベントあり | 容量不足、未対応major、不正ハンドルを指定する | 状態と必要サイズを返し、読出位置を保存する | 正本のexport契約 |
| TEST-OBS-06 | 安全点と所有権 | 実行中／停止中 | exportを要求する | 実行中は拒否し、安全点で一括配送する。バッファを保持せず、ホットパスでPythonを呼ばない | 正本の配送契約 |
| TEST-OBS-07 | 無効機能の除去 | Sink有効／無効 | 同時生成・実行し、生成物とROM/RAMを比べる | 無効構成にSink、リング、時計、発行経路を含めない | 正本の構成契約 |
| TEST-OBS-08 | JIT履歴との独立性 | SinkとJIT拡張の全有効組合せ | 履歴更新、容量超過、無効化を実行する | JIT履歴の操作がRuntimeイベントと実行経路を変えない | 正本の独立性 |

## 3. テスト検証実績と網羅状況

公開call境界は [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) から実行する。
制御した実行器を使い、構成器・Sink・転送ABI・Adapterを通った結果を照合する。
期待イベントを実行結果から転記しない。
Sinkの有効・無効で戻り値、trap、yield、ゲスト状態を変えない。

| 条件 | 操作と期待結果 |
| :--- | :--- |
| 成功 | Interpreter／JITで2回呼ぶ。引数、結果、全イベント値が一致し、有効な観測先だけへ配送する。前回の列を再送しない |
| guest trap | 両実行方式でエラーを返す。元の結果を保持し、TRAPと中断終了を1回ずつ通知する。終了理由、相関ID、時刻も一致する |
| host failure | 両実行方式でエラーを返す。元の結果と中断理由が一致し、TRAPを通知しない |

TEST-OBS-03/05は同ファイルの`test_event_sink_retains_latest_events_in_issue_order`で検証する。容量1、2、4に対し、容量を超える回数の入力と再充填を行う。最新イベントの全値と発行順、累積欠落数、容量不足と未対応majorによるexport拒否後の保持を照合する。

## 4. 未検証・スコープ外

- イベントリングの容量超過試験はpysimの保持順序だけを判定し、C++実装の証拠に数えない。
- 公開call境界の構成試験は、内部命令実行イベント、実行中export拒否、C++のコード除去と資源量を判定しない。
- 構成選択は [`runtime_plugin_architecture_test_spec.md`](docs/qa/tier2_runtime/runtime_plugin_architecture_test_spec.md) を参照する。
- Profiler集計、JIT履歴、外部ログ形式は各コンポーネントのテスト仕様を参照する。
