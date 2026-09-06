# IPCルータ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md)
参考実装: [`ipc_router_concept.py`](docs/components/tier1_interface/concepts/ipc_router_concept.py)（`flat_view_concept.py`のFlatMapViewを利用）

URIベースのサービス検索（3段パイプライン）、ロールベースアクセス制御、ゼロコピー所有権移譲（Revoke→Rendezvous→Grant、バッファなし同期CSPハンドオフ）、および受信側のガード付き外部選択（select、複数の許可された送信元エッジを同時に待ち受ける）を検証する。本APIはCOOSのCSPチャネル（`{ADR_RendezvousChannel}`）そのものであり、有界キューやDrop Handlerは存在しない。

## 2. テストケース一覧

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| IPCR-01 | レジストリは実際にFlatMapView（O(log N)二分探索） | - | `_REGISTRY`の型を確認 | `dict`ではなく`FlatMapView`のインスタンスである | ipc_router_concept.py `test_registry_is_a_real_flat_map_view_not_a_dict` |
| IPCR-02 | URI Lookup成功 | 登録済みURI（例: `fireball://hal/gpio/0`） | `router.lookup(uri)` | `(COMPLETED, channel)` オブジェクトを返す | Stage 1, Stage 2 |
| IPCR-03 | URI Lookup失敗 | 未登録URI | `router.lookup(uri)` を呼ぶ | `(ERR_NOT_FOUND, None)` を返し、メッセージ所有権は送信側のまま(`SENDER_OWNS`) | Error1, ipc_router_concept.py `test_unregistered_uri_is_rejected` |
| IPCR-04 | ロールベースアクセス制御・許可 | `RUNTIME`→`PLATFORM_HAL`（許可） | `lookup(uri)` でチャネル取得後 `send(channel, msg)` | `COMPLETED`を返す | 表, `{RoleBasedAccessControl}` |
| IPCR-05 | ロールベースアクセス制御・拒否 & 偽装防止 | `RUNTIME`→`DEBUGGER`（拒否） | `lookup(uri)` を呼ぶ。また他ロールのチャネルを直接指定して `send(channel, msg)` を試行 | `ERR_PERMISSION_DENIED`を返し、TCB ロール検査により偽装送信も拒否され、所有権が送信側のまま維持される | Error2, ipc_router_concept.py `test_permission_denied` |
| IPCR-06 | 全DENY行・列の意味の確認 | `PLATFORM_HAL`を送信元にする | 任意の宛先へ`lookup` | 常に拒否される（HALは通信グラフの葉） | ipc_router.md「全DENY行・列の意味」 |
| IPCR-07 | ゼロコピー所有権移譲の3段階 | 許可されたURIへの送信 | `lookup`→`send(channel, msg)`→`receive()` | `SENDER_OWNS`→`IN_FLIGHT`（Revoke直後、`send`が返った時点）→`RECEIVER_OWNS`（`receive`でGrant成立時）と遷移する | 「所有権移譲」, `{OwnershipTransfer}` |
| IPCR-08 | 単一待機者制約（キュー化されないことの確認） | 同一エッジへ1件送信済み（未受信） | 同一エッジへさらに1件`send` | `ERR_QUEUE_FULL`のような差し戻しではなく、`AssertionError`（プログラミングエラー）となる——2件目を保持する「キュー」がそもそも存在しない | 「Revoke」, ipc_router_concept.py `test_no_queue_full_state_exists` |
| IPCR-09 | Drop Handlerが存在しないことの確認 | メッセージがIN_FLIGHT中に受信側タスクが到達しない | （該当する強制回収APIは存在しない） | 送信側タスクがブロックし続けるのみで、キュー内メッセージの強制回収という概念自体が発生しない（回収すべきキューがないため） | 「キューが存在しないことの帰結」 |
| IPCR-10 | 単一メッセージ順序の保証 | 同一チャネル（エッジ）は同時に高々1件のみIN_FLIGHTになれる | 1件目のRendezvous成立後に2件目を送信 | 常に送信された順に1件ずつランデブーが成立する（複数件が同時にバッファされて順序が入れ替わる余地がない） | 「単一待機者制約」 |
| IPCR-11 | 二重所有不在（形式検証と整合） | 任意の移譲シーケンス | 各段階での所有権フィールドを確認 | `sender_ownership != OWNED または receiver_ownership != OWNED` が常に成立 | `../formal/csp_handoff_model.py` |
| IPCR-12 | In-flight有限解決性 | メッセージがRevokeされIN_FLIGHTになる | Grantに到達するまで追跡（相手タスクが有限時間内に到達するという公正性仮定の下で） | 有限ステップで必ずGrantに解決する（無限にIN_FLIGHTのままになるのは相手タスクが永久に到達しない場合のみで、これは本コンポーネントの検証範囲外） | 「In-flight 有限解決性」 |
| IPCR-13 | kv_pair型スコープのビット構成 | メッセージペイロードを構築 | 型スコープ上位3bit（Functional/Dictionary/Resource）と下位5bit（型）を設定 | 正しくエンコード・デコードされる | kv_pair |
| IPCR-14 | メッセージの8要素固定長制限 | 9個以上のkv_pairを構築しようとする | メッセージ構築 | 拒否される、または`ERR_MSG_TOO_LARGE`（route_message仕様） | route_message |
| IPCR-15 | DENYエッジへの送信 | RBACマトリックス上でDENYの`(sender_role, target_role)`エッジ | `lookup(uri)` または非認可チャネルへの `send(channel, msg)` | 対応するCSPチャネルが存在しない（`None`）またはTCBロール不一致のため`ERR_PERMISSION_DENIED`を返す | route_message |
| IPCR-16 | CSPチャネルとの同一性 | - | ドキュメント上の記述を確認 | 本APIは`{ADR_RendezvousChannel}`が定めるバッファなし同期ランデブーそのものであり、`{CSP_Handoff}`を主張することを実装が正しく反映している（キューを介した別機構ではない） | 「COOSのCSPチャネルと同一の機構」 |
| IPCR-17 | 受信側のガード付き外部選択（select）: 複数エッジからの受信 | `CORE_SERVICE`は`RUNTIME`と`DEBUGGER`の双方からALLOW（RBACマトリックス） | 受信側を先にブロックさせた後、`DEBUGGER`から送信 | `receive()` を呼ぶだけで `DEBUGGER` からのメッセージを受信できる（送信元ロールやURIの事前指定不要） | 「Rendezvous」, 「receive_message」, ipc_router_concept.py `test_receive_selects_whichever_allowed_sender_is_ready`, [`scheduler.py`](experiments/pysim/core/scheduler.py) `channel_select_recv` |
| IPCR-18 | select解決後の敗退エッジの解除（1チャネル1待機者の維持） | IPCR-17の状態で`DEBUGGER`エッジが成立した直後 | 成立しなかった`RUNTIME`→`CORE_SERVICE`エッジの状態を確認し、続けて新規の受信側・送信側でそのエッジを使用する | 敗退エッジの待機者登録が解除されており（`waiter_dir == NONE`）、後続の`RUNTIME`→`CORE_SERVICE`ランデブーが独立して正常に成立する（stale waiterとして残らない） | [`scheduler.py`](experiments/pysim/core/scheduler.py) `channel_send`のSelectGroup解除処理, [`test_ipc_router.py`](experiments/pysim/tests/tier1_interface/test_ipc_router.py) `test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group` |
| IPCR-19 | メッセージ配列データ所有権とビュー提供 | エントリ配列構築 | メッセージ構築 | IPCメッセージはキー・バリュー対のソート配列を所有し、非所有ビューでペイロードを提供する（バルク転送時は共有メモリブロックのRAII所有権をカプセル化） | 「IPCメッセージ」, `test_ipc_05_message_storage_ownership_separation` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| IPCR-GOTCHA-01 | 単一待機者制約とキュー完全不在 | 同一 CSP エッジへ1件送信中（受信待ち状態） | 同一エッジへさらに `send` を試行 | `ERR_QUEUE_FULL` のような差し戻しエラーではなく、即座にアサーション違反（プログラミングエラー）で停止する。**実装の勘所**: CSP ランデブーチャネルにはバッファもキューも存在しないため、「キュー溢れ」というエラー状態を設けてはならず、2重送信は呼び出し元の論理破綻として検出する | `ipc_router.md`, `{ADR_RendezvousChannel}` |
| IPCR-GOTCHA-02 | Preflight Rejection による所有権保全 | RBAC 拒否エッジまたは未登録 URI 宛のメッセージ送信 | `send` を実行 | 権限・URI・サイズ検証がメッセージ Revoke（所有権剥奪）の前に先行して行われ、エラー時は所有権が `SENDER_OWNS` のまま1ミリも動かない。**実装の勘所**: 先にリソースを Revoke してから送信先を検証すると、エラー時にリソースが孤立（in-flight リーク）する | `ipc_router.md`, `{OwnershipTransfer}` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `{LowLatencyLookup}`の実測ベンチマーク自体（`../benchmarks/low_latency_lookup_bench.py`が正本）。
- C++実装での`fireball::flat_map_view<std::string_view, registry_entry>`のROM配置詳細。
