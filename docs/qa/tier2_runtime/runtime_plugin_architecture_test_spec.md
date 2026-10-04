# Runtime Plugin 構成 テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_plugin_architecture.md`](docs/components/tier2_runtime/runtime_plugin_architecture.md)

本仕様は、Runtimeの型構成、独立したプラグイン選択、初期化・終了順序、および未選択機能の除去を検証する。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-PLUGIN-01 | 構成別Runtimeの同時生成 | Interpreter、任意JIT拡張、Debugger、Profiler、Event Sinkの具象型が利用可能 | 異なる構成を同一プログラム内で生成・実行する | 各Runtimeが選択された型だけを保持し、構成選択分岐や状態を他インスタンスと共有しない |
| TEST-PLUGIN-02 | Runtime Event SinkとJIT拡張の独立選択 | 両スロットの有効・無効構成を用意する | 両機能の全有効組合せをインスタンス化して実行する | 一方の選択が他方の生成・記録・分析経路に影響しない |
| TEST-PLUGIN-03 | 無効スロットの除去 | 1つ以上のプラグインを無効にした構成を用意する | 型構成、生成コード、リンクmapを調べる | 無効機能の状態、初期化、呼出し、破棄コードがRuntime生成物に含まれない |
| TEST-PLUGIN-04 | 依存順初期化と逆順終了 | 複数の選択済みコンポーネントを接続する | 正常起動、初期化失敗、Runtime破棄を実行する | 依存先から初期化し、失敗時と破棄時は逆順に終了する。未初期化要素を終了しない |
| TEST-PLUGIN-05 | Debugger構成の実行器選択 | Debugger有効構成とInterpreter/JITが利用可能 | Debugger付き構成を生成し、JIT型を選んだ構成も試す | Debugger付きRuntimeはInterpreterのみを選び、Debugger+JITの不正構成は起動前に拒否する |
| TEST-PLUGIN-06 | 共有Runtime状態の隔離 | 同じ実行ファイル内に独立した複数Runtimeを生成する | 各Runtimeで別モジュール、イベント履歴、JIT拡張状態を更新する | Runtime破棄まで各状態が分離され、片方の破棄で他方の状態が変化しない |

## 3. 判定条件

Runtime構成、初期化ログ、失敗時の破棄順序、生成コード、および資源量を直接比較する。構成の差はコンパイル時の
具象型選択に現れ、実行中の有効性分岐として現れてはならない。

## 4. 対象外

- Runtime Event Sinkのレコード形式・転送ABI。詳細は [`runtime_observability_test_spec.md`](docs/qa/tier2_runtime/runtime_observability_test_spec.md) に置く。
- JIT拡張のホットスポット履歴と実行区間ごとのカード分析。詳細は [`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md)「JIT拡張ホットスポット履歴」に置く。
- Debuggerのプロトコル処理、Profilerの集計方針、JITコンパイルアルゴリズム。
