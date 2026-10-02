# サービス テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_service.md`](docs/components/tier1_interface/system_service.md)
参考実装: [`service_concept.py`](docs/components/tier1_interface/concepts/service_concept.py)

サービス分離・障害隔離・自己再起動、およびサービス境界の振る舞いを検証する。WASI Preview1 からHAL IFへの変換はTier 3ゲストアダプタのテスト仕様の責務とする。

## 2. テストケース一覧
<!-- traceability: {CleanArchitecture} {IPCRouter} {SelfReboot_via_Event} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SVC-01 | サービスロード成功 | 有効なURI | `load_service(uri)` | `SUCCESS`を返し、サービスを`Loaded`にする。ゲスト実行はまだ開始しない | `service_concept.py` |
| TEST-SVC-02 | サービスロード失敗時のリカバリー戦略 | 固定構成URIと下位初期化結果`RETRY`/`RESTART`/`PANIC` | `load_service(uri)`へ各結果を与えて初期化完了前に返す | 同じ回復戦略を返し、サービスを`Running`へ遷移させない。`IGNORE`は非適用。再試行回数やエスカレーションは`RecoveryManager`の責務 | `service_concept.py` `test_load_failures_return_recovery_strategy_without_starting_service`, `test_recovery.py` |
| TEST-SVC-03 | サービスから公開IFへのアクセス境界 | サービスが稼働中 | サービスが公開インターフェースを呼び出す | サービスはゲスト側アダプタや物理ドライバの内部構造を保持しない | `{META_FaultIsolation}` |
| TEST-SVC-04 | サービス障害の局所化 | サービスが異常終了 | 他サービスの状態を確認する | 失敗したサービス以外の状態は変更されない | `{META_FaultIsolation}`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-05 | サービス再起動後の隔離 | サービスが再起動する | 対象サービスを再ロードする | 対象のTCB・ヒープだけが初期化される | `SelfReboot_via_Event`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-06 | サービスから物理実装への非依存 | HALやドライバが差し替えられる | 同じ公開IFを利用する | サービス仕様の変更なしに実装を差し替えられる | `CleanArchitecture` |
| TEST-SVC-07 | サービス境界の待機 | サービスがIPC応答を待つ | IPC境界で待機する | サービスの状態遷移だけが変化し、HAL内部の待機方式は漏れない | `IPCRouter` |
| TEST-SVC-08 | サービスの公開URI利用 | 有効なサービスURI | `load_service(uri)`相当を呼ぶ | 固定構成のURIだけがロード対象になる | `{META_ConfigurableSystem}` |
| TEST-SVC-09 | ロード後の明示起動 | `Loaded`のサービス | `start_guest(uri)` | `Running`へ遷移する | `system_service.md` 状態遷移図, `service_concept.py` |
| TEST-SVC-10 | サービスメッセージ境界 | - | メッセージを公開境界へ渡す | サービス仕様はHALの内部コマンドIDを定義しない | `IPCRouter` |
| TEST-SVC-11 | サービス障害の自己再起動 | サービスが異常終了 | 障害イベント通知 | TCBスロットがリセットされ、当該サービスのみ再初期化される（他サービス波及なし） | 「自己再起動」`SelfReboot_via_Event`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-12 | 再起動による失敗要求の自動再実行禁止 | 外部副作用を伴う要求の処理中に障害を確認する | 対象を再初期化し、操作回数と応答を観測する | 初期化成功でも元要求の成功を返さず、元操作の回数を増やさない。他サービスの状態は変わらない | `{SelfReboot_via_Event}`, `test_recovery.py`（再実行禁止のみ）、`service_fault_isolation_model.py`（抽象モデル） |


## 3. テスト検証実績と網羅状況

サービスのロード、明示起動、障害隔離、対象サービスの再起動は`service_concept.py`の概念モデルで検査する。`test_ipc_router.py`はIPCのアクセスと所有権を検査する。`test_vsoc.py`はvSoC実行状態を検査する。これらのpysim試験を、実サービスのロードと自己再起動の検証実績へ読み替えない。

監査結果は[`test_reconstruction_review.md`](docs/qa/test_reconstruction_review.md)を参照する。

## 4. 未検証・スコープ外

- TEST-SVC-01/02/04/05/09/11のpysimサービス実装への統合は未検証である。概念モデルの状態を、実ランタイムのTCBとヒープの状態に対応付ける証拠が必要である。
- TEST-SVC-12の実サービス要求・IPC後始末との統合は未実装である。reset callbackの操作回数から実TCB・Runtime・heapの再初期化を主張しない。
- 固定2スロットのheap返却・再貸与は`test_reacquire_released_partition_does_not_overlap_live_task`で他タスクの実体保全と対象領域のゼロ初期化を検査する。これはサービス生命周期の結線全体の証拠ではない。
- `service_load_result_t`のC++列挙型そのもの。
- 物理メモリパーティション分離の実効性（`system_memory.md`/`runtime_memory.md`側）。
