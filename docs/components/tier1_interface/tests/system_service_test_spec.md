# サービス テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_interface/system_service.md`
参考実装: なし

サービス分離・障害隔離・自己再起動、およびWASI呼び出しをHAL/IPCコマンドへ変換するラッパー（§4.4擬似コード）の振る舞いを検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SVC-01 | サービスロード成功 | 有効なURI | `load_service(uri)` | `SUCCESS`を返し、Ready状態になる | §5.2 load_service |
| SVC-02 | サービスロード失敗時のリカバリー戦略 | 依存関係未解決等 | 同上 | `RETRY`/`RESTART`/`PANIC`のいずれかを返す（`IGNORE`は非適用） | §5.2「IGNOREは非適用」 |
| SVC-03 | WASI fd_write→IPCメッセージ変換 | fdが有効なチャネルに解決可能 | `wasi_fd_write(fd, iovs, iovs_len, nwritten_ptr)`相当を呼ぶ | 各iovecごとに`CMD_HAL_WRITE`のKVメッセージが構築され、`ipc_router.route_message`経由で送信される | §4.4 擬似コード |
| SVC-04 | 無効なfdは`WASI_ERRNO_BADF` | `resolve_wasi_fd_to_channel`が`INVALID_CHANNEL`を返す | 同上 | `WASI_ERRNO_BADF`を返す | §4.4 手順2 |
| SVC-05 | ゲストメモリ境界チェック（iovs自体） | iovsポインタが範囲外 | 同上 | `WASI_ERRNO_FAULT` | §4.4 手順3 |
| SVC-06 | ゲストメモリ境界チェック（各iovの`buf`） | 個々のbufが範囲外 | 同上 | `WASI_ERRNO_FAULT` | §4.4 手順4 |
| SVC-07 | キュー満杯/アクセス拒否時の中断 | `route_message`が`ERROR_QUEUE_FULL`または`ERR_ACCESS_DENIED` | 同上 | `WASI_ERRNO_IO`を返し中断する | §4.4 手順5 |
| SVC-08 | 完了待機（co_yield相当のサスペンド） | 正常送信後 | `wait_for_ipc_response`相当 | HALからの完了通知までタスクがサスペンドする（同期I/Oの模倣） | §4.4 手順6 |
| SVC-09 | サービス障害の自己再起動 | サービスが異常終了 | 障害イベント通知 | TCBスロットがリセットされ、当該サービスのみ再初期化される（他サービス波及なし） | §4.1「自己再起動」`{SelfReboot_via_Event}` |
| SVC-10 | メッセージ形式のヘッダ/ペイロード分離 | - | メッセージ構築 | `arg0`=コマンドID, `arg1`=リカバリー戦略カテゴリ, `arg2`〜`arg5`=固有引数 | §5.3 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `service_load_result_t`のC++列挙型そのもの。
- 物理メモリパーティション分離の実効性（`platform_memory.md`側）。
