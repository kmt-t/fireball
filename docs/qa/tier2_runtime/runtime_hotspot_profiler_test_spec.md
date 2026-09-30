# Runtime Hotspot Profiler テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_hotspot_profiler.md`](docs/components/tier2_runtime/runtime_hotspot_profiler.md)
関連正本: [`interpreter.md`](docs/components/tier3_executer/interpreter.md)、
[`jit_runtime.md`](docs/components/tier3_executer/jit_runtime.md)

本仕様は、Interpreterによる基本ブロック履歴の記録、実行区間終了時の一括分析、JITカード更新、履歴容量超過時の
近似状態、およびRuntime Event Sinkからの独立性を検証する。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-HOTSPOT-01 | 履歴レコードとInterpreter記録順 | JIT有効・Hotspot Profiler有効で、複数の適格ブロックを通る関数がある | Interpreter区間を実行し、履歴を読み出す | 各レコードが`(module_id, unified_pc)`を保持し、実行順と一致する。非適格ブロックは含まれない |
| TEST-HOTSPOT-02 | 基本ブロック実行経路の処理制約 | Runtime Event Sink、時計、Python APIを利用できる構成である | 適格ブロックを反復実行し、呼出し・時刻読出し・割当てを記録する | Interpreterは履歴へ固定幅レコードを書くだけで、Event Sink、時計、Python API、動的確保を経由しない |
| TEST-HOTSPOT-03 | Interpreter区間終了時の一括分析 | 同じPCを複数回含む未分析履歴がある | yield、fallback、trap、関数完了の各終了理由でInterpreter区間を抜ける | 各終了境界で一度だけ履歴順にカードを更新し、必要なcompile requestを登録してからRuntimeへ戻る |
| TEST-HOTSPOT-04 | JITのみを実行した区間 | 常駐traceとchainが実行可能で履歴が空である | Interpreterを通らないJIT trace/chain区間を実行する | 履歴を追加せず、Hotspot Profilerを呼び出さず、既存カードを変更しない |
| TEST-HOTSPOT-05 | 履歴容量超過と近似状態 | 容量Nの履歴へN+K件を記録する | 次の分析境界で履歴と分析結果を確認する | 直近N件を保持し、上書き数Kと履歴欠落状態を示す。分析済み範囲を消費する |
| TEST-HOTSPOT-06 | Runtime Event Sinkとの状態分離 | Event SinkとHotspot Profilerを独立に有効・無効化する | 基本ブロック列を実行し、両履歴の内容と容量超過を比較する | Hotspot履歴はRuntimeイベントリングへ入らず、Event Sinkの状態はHotspot記録・分析に影響しない |
| TEST-HOTSPOT-07 | 無効構成の処理・状態除去 | JIT無効またはHotspot Profiler無効の具象Runtimeを用意する | 型構成、生成コード、メモリ量を比較する | 無効構成には履歴領域、履歴書込み、分析処理が含まれず、実行結果・trap・yield・ゲスト状態は有効構成と一致する |
| TEST-HOTSPOT-08 | Runtime寿命とモジュール状態 | Runtimeにモジュールを登録し、履歴とカード状態を作る | Runtimeを破棄し、新しいRuntimeを生成する | 旧Runtimeの履歴・カード状態はRuntime破棄で解放され、モジュール単位のunload APIや状態再利用を要求しない |

## 3. 判定条件

履歴レコード列、カード遷移列、compile request、実行結果、上書き数、具象型構成、およびROM/RAM量を直接比較する。
Runtime Event Sinkの有効・無効がHotspot履歴へ影響した場合、またはJIT-only区間で記録・分析が発生した場合は不合格とする。

## 4. 形式検証との対応

[`runtime_hotspot_profiler_model.py`](docs/components/tier2_runtime/formal/runtime_hotspot_profiler_model.py)で履歴順序、分析境界、JIT-only区間での無記録、容量超過、構成無効化、Runtime破棄を状態遷移モデルで検証する。
各安全性・活性条件は通常モデルで成立し、`guards=False`変異で対応する違反遷移を到達可能にして反証する。
