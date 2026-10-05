# ゲストプロファイラ テスト仕様書

## 1. 目的と対象範囲

正本: [`guest_profiler.md`](docs/components/tier3_plugins/guest_profiler.md)
関連正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

Runtimeイベントからの呼出辺、時間、欠落の集計と、実ゲストへの非干渉を検証する。

## 2. テストケース一覧
<!-- traceability: {RuntimeEventSink} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-PROF-01 | 呼出辺と包括時間 | 親子の入退出が対応する | 入れ子のbatchを集計する | 辺、呼出回数、包括時間が一致する | 正本の時間意味論 |
| TEST-PROF-02 | 自己時間 | 親子の実行区間がある | 子の開始・終了tickを変える | 親の包括時間から子時間を一度だけ差し引く | 正本の時間意味論 |
| TEST-PROF-03 | Trap後の離脱 | 開いた親子フレーム | TRAP後、内側からexitを渡す | Trapだけで閉じず、対応exitで時間と推定値を確定する | 正本の終了処理 |
| TEST-PROF-04 | 累積欠落 | 複数batch | 欠落数5→5→8を渡す | 差分5→0→3を加算し、影響区間を推定値にする | 正本の過負荷契約 |
| TEST-PROF-05 | 容量超過 | 小さい関数表・辺表・スタック | 各容量を超える関数・再帰を入力する | 欠落を記録し、親フレームと時間を保持する。影響する統計は推定値にする | 正本の固定容量状態 |
| TEST-PROF-06 | Debugger停止 | 開いた親子フレーム | DEBUG_STOPを渡す | 停止tickで推定値として閉じ、Runtime状態を変えない | 正本の終了処理 |
| TEST-PROF-07 | 実行方式 | 同じ関数IDのInterpreter／JITイベント | 両方式を集計する | 同じ関数へ集計し、方式属性を保持する | 正本の時間意味論 |
| TEST-PROF-08 | 実ゲストとの結合 | Interpreter／実JIT、固定時計 | 正常2回、別関数、trap、正常再呼出しを実行する | 各回の結果・状態と、関数別回数・時間・推定値が一致する。フレームを残さない | 公開call境界・非干渉 |
| TEST-PROF-09 | 集計過負荷の隔離 | Profilerのスタック容量0 | 同じ実ゲスト履歴を観測有効／無効で実行する | 欠落を記録し、結果・trap・PC・スタック深さ・メモリ・globalを変えない | 正本の容量不足契約 |

## 3. テスト検証実績と網羅状況

集計単体は [`test_guest_profiler.py`](experiments/pysim/qa/tier3_plugins/profiler/test_guest_profiler.py) でTEST-PROF-01〜07を検査する。
TEST-PROF-06は入力batchの不変性だけを検査し、Debuggerと実Runtimeの状態保存は示さない。
TEST-PROF-07の方式属性保持は未実装であり、同じIDへの呼出2回と時間6の集計だけを検査する。

結合は [`test_guest_profiler_runtime.py`](experiments/pysim/qa/integration/test_guest_profiler_runtime.py) でTEST-PROF-08/09を各方式で実行する。
Loader、native Interpreter、RuntimeEngine、構成器、Sink、転送ABI、Adapter、実GuestProfilerを通す。
QAアダプタはnative完了・trapを構成器の結果契約へ変換する。
イベント生成・配送と集計を代替しない。
時計入力を固定し、公開call境界の時間を独立した期待値で照合する。
JIT構成は実生成コードの実行も確認する。
全ゲストメモリとglobalを各callで照合する。
call完了時のPC、値・local・呼出・制御スタックの深さ、結果を観測無効時とも比較する。

形式検証は [`guest_profiler_model.py`](docs/components/tier3_plugins/formal/guest_profiler_model.py) を参照する。
呼出対応、時間分類、trap離脱、欠落、再帰超過を通常モデルと`guards=False`変異モデルで検査する。

## 4. 未検証・スコープ外

- 結合試験のイベントは公開call境界に限る。WASM内部の親子呼出辺、host call、yield、Debugger停止の実結線は未検証である。
- 固定時計のtickは実機の実行時間や性能を示さない。
- 方式属性の保持、複数Runtime／moduleの識別子分離は未検証である。
- Debuggerの停止・再開・ステップ制御は [`debugger_test_spec.md`](docs/qa/tier3_plugins/debugger_test_spec.md) を参照する。
