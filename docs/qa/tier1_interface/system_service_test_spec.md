# サービス テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`system_service.md`](docs/components/tier1_interface/system_service.md)
参考実装: [`service_concept.py`](docs/components/tier1_interface/concepts/service_concept.py)

サービス分離・障害隔離・自己再起動、およびサービス境界の振る舞いを検証する。WASI Preview1 からHAL IFへの変換はTier 3ゲストアダプタのテスト仕様の責務とする。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SVC-01 | サービスロード成功 | 有効なURI | `load_service(uri)` | `SUCCESS`を返し、Ready状態になる | load_service |
| TEST-SVC-02 | サービスロード失敗時のリカバリー戦略 | 依存関係未解決等 | 同上 | `RETRY`/`RESTART`/`PANIC`のいずれかを返す（`IGNORE`は非適用） | 「IGNOREは非適用」 |
| TEST-SVC-03 | サービスから公開IFへのアクセス境界 | サービスが稼働中 | サービスが公開インターフェースを呼び出す | サービスはゲスト側アダプタや物理ドライバの内部構造を保持しない | `{META_FaultIsolation}` |
| TEST-SVC-04 | サービス障害の局所化 | サービスが異常終了 | 他サービスの状態を確認する | 失敗したサービス以外の状態は変更されない | `{META_FaultIsolation}`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-05 | サービス再起動後の隔離 | サービスが再起動する | 対象サービスを再ロードする | 対象のTCB・ヒープだけが初期化される | `{SelfReboot_via_Event}`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-06 | サービスから物理実装への非依存 | HALやドライバが差し替えられる | 同じ公開IFを利用する | サービス仕様の変更なしに実装を差し替えられる | `{CleanArchitecture}` |
| TEST-SVC-07 | サービス境界の待機 | サービスがIPC応答を待つ | IPC境界で待機する | サービスの状態遷移だけが変化し、HAL内部の待機方式は漏れない | `{IPCRouter}` |
| TEST-SVC-08 | サービスの公開URI利用 | 有効なサービスURI | `load_service(uri)`相当を呼ぶ | 固定構成のURIだけがロード対象になる | `{META_ConfigurableSystem}` |
| TEST-SVC-09 | サービス障害の自己再起動 | サービスが異常終了 | 障害イベント通知 | TCBスロットがリセットされ、当該サービスのみ再初期化される（他サービス波及なし） | 「自己再起動」`{SelfReboot_via_Event}`, `service_concept.py` `test_fault_isolation_and_targeted_restart` |
| TEST-SVC-10 | サービスメッセージ境界 | - | メッセージを公開境界へ渡す | サービス仕様はHALの内部コマンドIDを定義しない | `{IPCRouter}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `service_load_result_t`のC++列挙型そのもの。
- 物理メモリパーティション分離の実効性（`system_memory.md`/`runtime_memory.md`側）。
