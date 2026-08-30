# IPCルータ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_interface/ipc_router.md`
参考実装: `docs/components/tier1_interface/concepts/ipc_router_concept.py`（`flat_view_concept.py`のFlatMapViewを利用）

URIベースのサービス検索（3段パイプライン）、ロールベースアクセス制御、ゼロコピー所有権移譲（Revoke→Enqueue→Grant）、キュー満杯時のRollback、Drop Handlerによる異常時回収を検証する。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| IPCR-01 | レジストリは実際にFlatMapView（O(log N)二分探索） | - | `router.registry`の型を確認 | `dict`ではなく`FlatMapView`のインスタンスである | §4.3.1, ipc_router_concept.py `test_registry_is_a_real_flat_map_view_not_a_dict` |
| IPCR-02 | URI Lookup成功 | 登録済みURI（例: `fireball://hal/gpio/0`） | `registry.find(uri)`または`route_message` | 対応する`registry_entry`（role/channel_id/max_queue）を返す | §4.1 Stage 1 |
| IPCR-03 | URI Lookup失敗 | 未登録URI | `route_message`を呼ぶ | `ERR_NOT_FOUND`を返し、メッセージ所有権は送信側のまま(`SENDER_OWNS`) | §4.1.1 Error1, ipc_router_concept.py `test_unregistered_uri_is_rejected` |
| IPCR-04 | ロールベースアクセス制御・許可 | `CLIENT_APP`→`PLATFORM_HAL`（許可） | `route_message("CLIENT_APP", uri, msg)` | `OK_ENQUEUED`を返す | §4.1.1 表, `{RoleBasedAccessControl}` |
| IPCR-05 | ロールベースアクセス制御・拒否 | `CLIENT_APP`→`DEBUGGER`（拒否） | 同上 | `ERR_PERMISSION_DENIED`を返し、所有権が送信側のまま維持される | §4.1.1 Error2, ipc_router_concept.py `test_permission_denied` |
| IPCR-06 | 全DENY行・列の意味の確認 | `PLATFORM_HAL`を送信元にする | 任意の宛先へroute_message | 常に拒否される（HALは通信グラフの葉） | ipc_router.md「全DENY行・列の意味」 |
| IPCR-07 | ゼロコピー所有権移譲の3段階 | 許可されたURIへの送信 | `route_message`→`receive_message` | `SENDER_OWNS`→`IN_FLIGHT`（Enqueue直後）→`RECEIVER_OWNS`（Dequeue時）と遷移する | §4.1「所有権移譲」, `{OwnershipTransfer}` |
| IPCR-08 | キュー満杯時のRollback | 対象チャネルの`max_queue`まで送信済み | さらに1件送信 | `ERR_QUEUE_FULL`を返し、当該メッセージの所有権は即座に送信側へ復元される（IN_FLIGHTを経由しない） | §4.1「Rollback」, ipc_router_concept.py `test_queue_full_rollback` |
| IPCR-09 | Drop Handlerによる強制回収 | メッセージがキュー内でIN_FLIGHT中 | `trigger_drop_handler(channel_id)`を呼ぶ | キュー内の全メッセージが`RECLAIMED_BY_DROP`になり、リークしない | §4.1「異常時リカバリ」, `{IPC_DropHandler}` |
| IPCR-10 | FIFO順序の保証 | 同一チャネルに複数メッセージ送信 | 順に`receive_message`を呼ぶ | 送信順(FIFO)でデキューされる | §6.1「メッセージ順序」 |
| IPCR-11 | 二重所有不在（形式検証と整合） | 任意の移譲シーケンス | 各段階での所有権フィールドを確認 | `sender_ownership != OWNED または receiver_ownership != OWNED` が常に成立 | `../formal/csp_handoff_model.py`, §6.3 |
| IPCR-12 | In-flight有限解決性 | メッセージがEnqueueされる | Grant/Drop/Rollbackのいずれかに到達するまで追跡 | 有限ステップで必ずいずれかに解決する（無限にIN_FLIGHTのままにならない） | §6.1「In-flight 有限解決性」 |
| IPCR-13 | kv_pair型スコープのビット構成 | メッセージペイロードを構築 | 型スコープ上位3bit（Functional/Dictionary/Resource）と下位5bit（型）を設定 | 正しくエンコード・デコードされる | §3.3 kv_pair |
| IPCR-14 | メッセージの8要素固定長制限 | 9個以上のkv_pairを構築しようとする | メッセージ構築 | 拒否される、または`ERR_MSG_TOO_LARGE`（route_message仕様） | §3.3, §5.1 route_message |
| IPCR-15 | 存在しないチャネルへのroute_message | 無効な`channel`値 | `route_message(channel, msg)` | `ERR_INVALID_CHANNEL`を返す | §5.1 route_message |
| IPCR-16 | CSPチャネルとの区別 | - | ドキュメント上の記述を確認 | 本APIは`{ADR_RendezvousChannel}`が定めるバッファなし同期ランデブーとは別機構であり、`{CSP_Handoff}`を主張しないことを実装が混同していない | §5.1「COOSのCSPチャネルとの区別」 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `{LowLatencyLookup}`の実測ベンチマーク自体（`../benchmarks/low_latency_lookup_bench.py`が正本）。
- C++実装での`fireball::flat_map_view<std::string_view, registry_entry>`のROM配置詳細。
