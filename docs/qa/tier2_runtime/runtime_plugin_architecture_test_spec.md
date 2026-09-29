# Runtime Plugin 構成モデル テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_plugin_architecture.md`](docs/components/tier2_runtime/runtime_plugin_architecture.md)

本仕様はpysim `RuntimeComposer` の構成時選択を検証する。実機C++生成物のコード除去、Pluginの実行時初期化・終了、SystemへのRuntime統合は対象外とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 実装 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-PLUGIN-01 | 実行器とDebugger構成の選択 | Interpreter/JIT factoryが存在する | 各実行構成をcomposeする | 選択したfactoryだけを呼び、Debugger+JITはfactory呼出し前に拒否する | `test_runtime_composer.py` `test_debugger_composition_constructs_interpreter_only_runtime`, `test_debugger_cannot_be_composed_with_jit` |
| TEST-PLUGIN-02 | Observerの選択と保持 | Logger/Debugger/Profiler factoryが存在する | Observer有効・無効の構成をcomposeする | 有効なfactoryだけを一度呼び、全無効ならObserver状態を保持しない | `test_runtime_composer.py` `test_disabled_plugins_are_not_constructed_or_retained`, `test_selected_plugins_receive_one_shared_event_stream` |

## 3. テスト検証実績と網羅状況

- 2ケースを [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) で実行する。
- 本仕様のテストはPython参照モデルに限定し、実機C++ RuntimeComposerを検証しない。

## 4. 未検証・スコープ外

- C++ `constexpr` 特殊化、翻訳単位・WIT境界の除去、リンカmap検査。
- Pluginの初期化・終了hook、失敗時ロールバック、Runtime実行ループとの統合。
- JITコンパイラ要求キューとProfilerイベント容量の実機構成。
