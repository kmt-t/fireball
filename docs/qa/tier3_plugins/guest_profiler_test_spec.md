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
| TEST-PROF-03 | トラップ時のフレーム終了 | 1個以上の関数フレームが開いている | `TRAP` イベントを渡す | 全フレームが閉じ、包括時間・自己時間が集計され、統計が推定値になる | `test_trap_closes_open_frames_as_estimated` |
| TEST-PROF-04 | 欠落イベントと固定表容量超過 | 関数統計表または辺表の容量が小さい | `DROPPED` 通知と容量超過する関数イベントを渡す | 欠落数と推定値数が増え、既存統計が推定値になる | `test_dropped_event_and_fixed_table_overflow_are_reported` |
| TEST-PROF-05 | 再帰スタック超過の隔離 | 関数スタック容量を1に設定する | 同一関数の入退出イベントを2階層分渡す | 追跡できない子関数の exit は超過深度で消費し、親フレームを早期に閉じない | `guest_profiler_model.py`, `test_recursive_stack_overflow_does_not_close_parent_frame_early` |

## 3. テスト検証実績と網羅状況

- コールグラフ、包括時間、自己時間、トラップ終了、欠落状態、表容量超過、および再帰スタック超過の5ケースを定義する。
- 実行可能なケースは [`test_guest_profiler.py`](experiments/pysim/qa/tier3_plugins/profiler/test_guest_profiler.py) に実装する。形式モデルは `guards=False` 変異で安全性・活性の両方を反証する。

## 4. 未検証・スコープ外

- `RuntimeEngine` へのイベント結線、外部ログ形式の具体的な符号化、物理搬送。
- 命令単位トレーサおよびサンプリング周期の補正。
- Debugger の実行制御。
