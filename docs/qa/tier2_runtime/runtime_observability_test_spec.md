# Runtime 観測イベント テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

本仕様は、pysim の `RuntimeComposer` が公開 `call` 境界で発行するイベントと、Observer の選択を検証する。Interpreter 内部の関数イベント、JIT/ホストコール/COOS境界の観測、実機 C++ のコード除去は対象外とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 実装 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-OBS-01 | 無効Observerの除去 | Logger/Debugger/Profilerが全て無効 | Runtimeをcomposeしてcallする | Observer factory と時刻源を呼ばず、イベント状態も保持しない | `test_disabled_plugins_are_not_constructed_or_retained` |
| TEST-OBS-02 | 共通イベント列と時刻 | LoggerとProfilerが有効 | 同じ関数をcallし、固定値を返す時計を注入する | 両Observerへ同じenter/exitレコードが届き、未提供module/PCにはsentinelを設定し、tickは単調増加する | `test_selected_plugins_receive_one_shared_event_stream` |
| TEST-OBS-03 | ゲストトラップとホスト障害の区別 | RuntimeExecutorが `Result` で実行結果または失敗種別を返す | ゲストトラップとホスト障害の結果でcallする | ゲストトラップはTRAP、ホスト障害はABORTED/ESTIMATED exitを発行し、同じ失敗種別を呼出元へ返す | `test_guest_trap_is_distinguished_from_host_failure` |
| TEST-OBS-04 | Debugger構成と実行器選択 | Debuggerが有効 | Interpreter構成とJIT構成をcomposeする | Interpreterだけを生成し、Debugger+JITは生成前にassertで拒否する | `test_debugger_composition_constructs_interpreter_only_runtime`, `test_debugger_cannot_be_composed_with_jit` |

## 3. テスト検証実績と網羅状況

- 4ケースを [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) で実行する。
- このテストは実機C++ Runtimeへの統合や生成物からの未使用コード除去を証明しない。

## 4. 未検証・スコープ外

- Interpreter内部の関数呼出、JIT入退出、WASI/host-call境界、COOS境界のイベント生成。
- `System.runtime_engine` とのイベントObserver結線。
- イベントリング、予約容量、Observer過負荷、ログ搬送、ターゲットMCUでの時刻精度。
- 実機C++の `constexpr` weave除去とリンカmap検査。
