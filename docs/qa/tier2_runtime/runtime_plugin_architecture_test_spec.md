# Runtime Plugin 構成 テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_plugin_architecture.md`](docs/components/tier2_runtime/runtime_plugin_architecture.md)

構成選択、無効機能の除去、初期化・終了順序、Runtime間の状態分離を対象とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-PLUGIN-01 | 構成の共存 | 異なるRuntime構成 | 同一プログラムで生成・実行する | 各構成が選択した実行器とプラグインだけを使う | 構成選択 |
| TEST-PLUGIN-02 | 観測とJITの独立選択 | Interpreter／JITと観測有効／無効 | 各構成で呼出しを実行する | 一方の選択が他方の生成・記録・分析を変えず、有効な観測先だけへ配送する | 独立選択 |
| TEST-PLUGIN-03 | 無効機能の除去 | 一部または全プラグインが無効 | 生成と実行を観測し、C++生成物も調べる | 無効機能を生成・呼出し・破棄しない。全観測無効なら時計も読まない | 無効機能の除去 |
| TEST-PLUGIN-04 | 初期化と終了順序 | 複数の選択済み要素 | 正常起動、初期化失敗、破棄を実行する | 依存順に初期化し、初期化済み要素だけを逆順に終了する | ライフサイクル |
| TEST-PLUGIN-05 | Debugger構成の排他 | Debugger有効 | Interpreter構成とJIT構成を合成する | Interpreterだけを選択し、JITとの同時構成は要素生成前に拒否する | `{DebuggerInterpreterComposition}` |
| TEST-PLUGIN-06 | Runtime間の隔離 | 独立した複数Runtime | 状態更新と片方の破棄を実行する | 他方のモジュール、イベント、JIT状態を変更しない | 状態所有権 |

## 3. テスト検証実績と網羅状況

C++構成は具象型で固定し、実行中に有効性を判定しない。
実行入口は [`test_runtime_composer.py`](experiments/pysim/qa/tier2_runtime/test_runtime_composer.py) とする。
全観測無効2構成と、logger／profilerの単独・同時選択6構成を独立に実行する。
2回のcallで引数、結果、観測先、イベント列、相関IDを照合し、Debugger構成の排他も確認する。
失敗時の通知は [`runtime_observability_test_spec.md`](docs/qa/tier2_runtime/runtime_observability_test_spec.md) に従う。

## 4. 未検証・スコープ外

- C++生成物のコード除去、ROM/RAM、TEST-PLUGIN-04の終了順序、TEST-PLUGIN-06の破棄時隔離は、このpysim試験で判定しない。
- イベントABIは [`runtime_observability_test_spec.md`](docs/qa/tier2_runtime/runtime_observability_test_spec.md) を参照する。
- JIT履歴、Debuggerプロトコル、Profilerの集計は各コンポーネントのテスト仕様を参照する。
