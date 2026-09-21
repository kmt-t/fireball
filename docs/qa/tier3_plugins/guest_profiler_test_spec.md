# ゲストプロファイラ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`guest_profiler.md`](docs/components/tier3_plugins/guest_profiler.md)
関連正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

Tier 2 の Runtime 観測イベントを受信し、ゲスト関数のコールグラフ、実行時間、欠落状態を集計する Tier 3 プラグインを検証する。Debugger の停止・再開・ステップ処理は本仕様の対象外とする。

## 2. テストケース一覧

### プロファイル集計

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-PROF-01 | 関数コールグラフと包括時間の集計 | `function_enter`／`function_exit` が正常に対応する | 親関数から子関数を呼び出すイベント列を入力する | 親子の呼出辺、呼出回数、包括時間が正しく集計される | `guest_profiler.md` §4.1 |
| TEST-PROF-02 | 自己時間と欠落状態の集計 | 子関数イベントまたは通常イベントの欠落が発生する | 入退出イベント列と欠落通知を入力する | 自己時間が二重計上されず、欠落後の統計へ推定値フラグが設定される | `guest_profiler.md` §4.1、§4.2 |

## 3. 未検証・スコープ外

- 外部ログ形式の具体的な符号化と物理搬送。
- 命令単位トレーサおよびサンプリング周期の補正。
- Debugger の実行制御。
