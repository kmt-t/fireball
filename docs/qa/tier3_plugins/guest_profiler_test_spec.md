# ゲストプロファイラ テスト仕様書

## 1. 目的と対象範囲

正本: [`guest_profiler.md`](docs/components/tier3_plugins/guest_profiler.md)
関連正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)、[`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md)

Runtimeの関数入退出イベントからプロファイルログへの変換と、ホスト側の集計を検証する。

## 2. テストケース一覧
<!-- traceability: {RuntimeEventSink} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-PROF-01 | enter辞書ID | 関数入イベント | プロファイラへ渡す | `0x0501`と関数ID・呼出ID・tickを記録する | 4.1 |
| TEST-PROF-02 | exit辞書ID | 関数退出イベント | プロファイラへ渡す | `0x0502`と関数ID・呼出ID・tickを記録する | 4.1 |
| TEST-PROF-03 | イベント順序 | 入れ子の入退出列 | バッチを変換する | 対象イベントの順を保ち、他のイベントを無視する | 4.2 |
| TEST-PROF-04 | 再帰集計 | 再帰呼出IDが異なる記録 | ホスト集計する | 呼出数と自己時間を呼出単位で対応付ける | 5.2 |
| TEST-PROF-05 | 入れ子・辺集計 | 親子の入退出記録 | ホスト集計する | 呼出辺、呼出回数、包括時間、自己時間が一致する | 5.2 |
| TEST-PROF-06 | 対応しない退出 | 空または異なるスタック先頭 | ホスト集計する | スタックを変更せず、件数を増やす | 4.1 |
| TEST-PROF-07 | tick循環 | 入退出が32ビット境界をまたぐ | ホスト集計する | 差分を`(end-start) & 0xFFFF_FFFF`で求める | 4.1 |
| TEST-PROF-08 | 実Runtime結合 | 固定時計、InterpreterおよびJIT | Runtimeイベントをログへ送り、復号して集計する | 関数別回数・時間が一致し、ゲスト状態と実行結果を保つ | 5.1/5.2 |

## 3. テストコードと形式検証


形式検証は [`guest_profiler_model.py`](docs/components/tier3_plugins/formal/guest_profiler_model.py) を参照する。辞書ID・引数数・tick符号化と入退出順序をCTLで検査し、`guards=False`で変異を検出する。

## 4. 既知の制限・スコープ外

- デバッグ出力バスは記録の到達を保証しない。集計は欠落した区間を含む可能性を明記し、欠落を推定しない。
- tick差分は一周未満の呼出区間だけ正確である。既定の1 GHz時計では一周は約4.295秒である。
- 固定時計のtickは実機の実行時間や性能を示さない。
- Debuggerの停止・再開・ステップ制御は [`debugger_test_spec.md`](docs/qa/tier3_plugins/debugger_test_spec.md) を参照する。
