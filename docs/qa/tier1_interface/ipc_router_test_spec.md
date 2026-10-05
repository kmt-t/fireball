# IPCルータ テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md)
参考実装: [`ipc_router_concept.py`](docs/components/tier1_interface/concepts/ipc_router_concept.py)（`flat_view_concept.py`のFlatMapViewを利用）
実行可能テスト: [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py)。

URIベースのサービス検索（3段パイプライン）、デバイス種別だけを表すロールとURIインスタンスの分離、ロールベースアクセス制御、ゼロコピー要求・応答（Request Revoke→Rendezvous→Grant→Response、バッファなし同期CSPハンドオフ）、Schedulerによる送信元TCB IDの認証、および受信側のガード付き外部選択（select、複数の許可された送信元エッジを同時に待ち受ける）を検証する。本APIはCOOSのCSPチャネル（`{ADR_RendezvousChannel}`）そのものであり、有界キューやDrop Handlerは存在しない。

## 2. テストケース一覧
<!-- traceability: {IPCRouter} {IPCRegistry} {RoleBasedAccessControl} -->

### 2.1 検証因子と独立した期待値

| 検証対象 | 因子と水準 | 組み合わせと判定根拠 |
| :--- | :--- | :--- |
| URI検索とRBAC | 送信元9ロール、登録済み8 URI | 72組を全数検査する。許可・拒否の期待値は設計正本の通信許可表から導く。製品のマトリックスは期待値に使用しない。 |
| 送信前拒否 | 他ロール用の登録チャネル、9要素のメッセージ | `send`を実行する。所有者、ページ世代、内容、タスク状態、チャネル待機状態の保全を確認する。拒否後はrollbackなしで同じメッセージを送信する。 |
| 要求・応答移譲 | 要求の送信側先行／受信側先行、応答の送信側先行／受信側先行、エコー／追加KV付き応答 | 8組を全数検査する。各ランデブーの成立前に送信側がサスペンドし、成立後だけ復帰することを確認する。各段階でメッセージの同一性、全KV、埋込みリソースの内容、共有ページの所有者を確認する。 |
| 単一要求制約 | 要求受信前、要求受信後の応答待ち | 2状態で二重送信を拒否する。最初の要求が保全され、応答完了後に同じチャネルを再利用できることを確認する。 |
| キーのビット配置 | 3スコープ、4型コード、24bit識別子の最小／最大 | 24組を全数検査する。期待ビット列は正本の3/5/24bit配置から導く。packとunpackの往復だけで判定しない。 |

送信先RUNTIMEはURIレジストリへ登録されていない。72組の分母には含めない。同種デバイスの複数URI構成は、参考実装の専用ケースで検証する。

### 2.2 検証する契約

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-IPCR-01 | レジストリは実際にFlatMapView（O(log N)二分探索） | - | `_REGISTRY`の型を確認 | `dict`ではなく`FlatMapView`のインスタンスである | [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md), `{IPCRegistry}`, ipc_router_concept.py `test_registry_is_a_real_flat_map_view_not_a_dict` |
| TEST-IPCR-02 | URI Lookup成功 | 登録済みURI（例: `fireball://hal/gpio/0`） | `router.lookup(uri)` | `(COMPLETED, channel)` オブジェクトを返す | Stage 1, Stage 2, `{IPCRouter}`, `{IPCRegistry}` |
| TEST-IPCR-03 | URI Lookup失敗 | 未登録URI | `router.lookup(uri)` を呼ぶ | `(ERR_NOT_FOUND, None)` を返し、メッセージ所有権は送信側のまま(`SENDER_OWNS`) | Error1, `{IPCRouter}`, `{IPCRegistry}`, ipc_router_concept.py `test_unregistered_uri_is_rejected` |
| TEST-IPCR-04 | ロールベースアクセス制御・許可 | `RUNTIME`→`HAL_GPIO`（許可） | `lookup(uri)` でチャネル取得後 `send(channel, msg)` | `COMPLETED`を返す | [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md), {RoleBasedAccessControl} |
| TEST-IPCR-05 | ロールベースアクセス制御・拒否 & 偽装防止 | `RUNTIME`→`DEBUGGER`（拒否） | `lookup(uri)` を呼ぶ。また他ロールのチャネルを直接指定して `send(channel, msg)` を試行 | `ERR_PERMISSION_DENIED`を返し、TCB ロール検査により偽装送信も拒否され、所有権が送信側のまま維持される | Error2, [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md), {RoleBasedAccessControl}, `ipc_router_concept.py` `test_permission_denied` |
| TEST-IPCR-06 | 全DENY行・列の意味の確認 | `HAL_*`（6ロールいずれか）を送信元にする | 任意の宛先へ`lookup` | 常に拒否される（HALは通信グラフの葉） | ipc_router.md「全DENY行・列の意味」 |
| TEST-IPCR-07 | ゼロコピー要求・応答所有権移譲 | 許可されたURIへの送信 | `lookup`→`send(channel, msg)`→`receive()`→`reply(msg, response_code)` | Schedulerのsender/reply stamperが共有メモリのRevokeを行い、Schedulerが対象TCBの実行コンテキストでGrant/Claimする。要求で`SENDER_OWNS`→`IN_FLIGHT`→`RECEIVER_OWNS`、応答で`RECEIVER_OWNS`→`IN_FLIGHT`→`SENDER_OWNS`と遷移し、送信側は応答取得までブロックする | 「所有権移譲」, `{OwnershipTransfer}` |
| TEST-IPCR-08 | 単一待機者制約（キュー化されないことの確認） | 同一エッジへ1件送信済み（未受信） | 同一エッジへさらに1件`send` | `ERR_QUEUE_FULL`のような差し戻しではなく、`AssertionError`（プログラミングエラー）となる——2件目を保持する「キュー」がそもそも存在しない | 「Revoke」, ipc_router_concept.py `test_no_queue_full_state_exists` |
| TEST-IPCR-09 | Drop Handlerが存在しないことの確認 | 要求または応答の相手タスクが到達しない | （該当する強制回収APIは存在しない） | 送信側は要求受信または応答返却までブロックし続ける。キュー内メッセージの強制回収という概念自体が発生しない | 「キューが存在しないことの帰結」 |
| TEST-IPCR-10 | 要求・応答の直列性 | 同一チャネル（エッジ）は応答完了まで1トランザクションだけ保持する | 1件目を受信させ、応答前に2件目を送信 | 2件目は同一チャネル上の応答完了前に受理されず、応答後にだけ次の要求が送信できる | 「単一待機者制約」 |
| TEST-IPCR-11 | 二重所有不在（形式検証と整合） | 任意の移譲シーケンス | 各段階での所有権フィールドを確認 | `sender_ownership != OWNED または receiver_ownership != OWNED` が常に成立 | [`csp_handoff_model.py`](docs/components/tier1_interface/formal/csp_handoff_model.py) |
| TEST-IPCR-12 | 要求・応答待機 | メッセージがRevokeされIN_FLIGHTになり、相手タスクを到達させない、または受信側が応答しない | `csp_handoff_model.py`とチャネル状態を確認 | 送信側は要求または応答のIN_FLIGHTで待機し続けてもよい。CSP単独の全タスク公平性や有界応答時間を要件にしない | `ipc_router.md`「要求・応答待機の安全性」 |
| TEST-IPCR-13 | kv_pair型スコープのビット構成 | メッセージペイロードを構築 | 型スコープ上位3bit（Functional/Dictionary/Resource）と下位5bit（型）を設定 | 正しくエンコード・デコードされる | kv_pair |
| TEST-IPCR-14 | メッセージの8要素固定長制限 | 送信側が9要素のメッセージを所有する | `send`で拒否を確認し、8要素に修正して同じメッセージを再送する | 9要素では`ERR_MSG_TOO_LARGE`を返す。Revoke、状態変更、rollbackは発生しない。8要素では要求待機へ進む。 | `route_message`、`test_send_preflight_rejection_preserves_shared_memory` |
| TEST-IPCR-15 | DENYエッジへの送信 | RBACマトリックス上でDENYの`(sender_role, target_role)`エッジ | `lookup(uri)` または非認可チャネルへの `send(channel, msg)` | 対応するCSPチャネルが存在しない（`None`）またはTCBロール不一致のため`ERR_PERMISSION_DENIED`を返す | route_message |
| TEST-IPCR-16 | CSPチャネルとの同一性 | - | ドキュメント上の記述を確認 | 本APIは`{ADR_RendezvousChannel}`が定めるバッファなし同期ランデブーそのものであり、`{CSP_Handoff}`を主張することを実装が正しく反映している（キューを介した別機構ではない） | 「COOSのCSPチャネルと同一の機構」 |
| TEST-IPCR-17 | 受信側のガード付き外部選択（select）: 複数エッジからの受信 | `CORE_SERVICE`は`RUNTIME`と`DEBUGGER`の双方からALLOW（RBACマトリックス） | 受信側を先にブロックさせた後、`DEBUGGER`から送信 | `receive()` を呼ぶだけで `DEBUGGER` からのメッセージを受信できる（送信元ロールやURIの事前指定不要） | 「Rendezvous」, 「receive_message」, ipc_router_concept.py `test_receive_selects_whichever_allowed_sender_is_ready`, [`scheduler.py`](experiments/pysim/tier1_core/scheduler.py) `channel_select_recv` |
| TEST-IPCR-18 | select解決後の敗退エッジの解除（1チャネル1待機者の維持） | TEST-IPCR-17の状態で`DEBUGGER`エッジが成立した直後 | 成立しなかった`RUNTIME`→`CORE_SERVICE`エッジの状態を確認し、続けて新規の受信側・送信側でそのエッジを使用する | 敗退エッジの待機者登録が解除されており（`waiter_dir == NONE`）、後続の`RUNTIME`→`CORE_SERVICE`ランデブーが独立して正常に成立する（stale waiterとして残らない） | [`scheduler.py`](experiments/pysim/tier1_core/scheduler.py) `channel_send`のSelectGroup解除処理, [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py) `test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group` |
| TEST-IPCR-19 | メッセージ配列データ所有権とビュー提供 | エントリ配列構築 | メッセージ構築と所有権状態別のアクセスを実行する | IPCメッセージはキー・バリュー対のソート配列を所有し、非所有ビューでペイロードを提供する。`IN_FLIGHT`中はエントリへアクセスできない。 | 「IPCメッセージ」、`test_ipc_05_message_storage_ownership_and_access_check`。直接借用と失効は第3.3節の実行ケースで検査する。 |
| TEST-IPCR-20 | デバイス種別RoleとURIインスタンスの分離 | 同じデバイス種別のURIを複数登録する | 各URIを検索し、Role・サービスハンドル・CSPチャネルを比較する | Roleは同じデバイス種別値を返し、インスタンスIDはRoleへ含まれない。サービスハンドルとチャネルはURIインスタンスごとに分離され、片方のI/Oが他方のチャネル状態を変更しない。`ipc_router_concept.py` の `test_same_role_uri_instances_use_distinct_channels` が交差受信を検出する | {RoleBasedAccessControl}, {IPCRegistry} |
| TEST-IPCR-21 | 送り元IDのScheduler認証 | 送信元タスクと受信側タスクを異なるTCBで起動する | 送信要求を受信し、`sender_id`を確認する | `sender_id`はSchedulerの送信元TCB `task_id`と一致し、メッセージ入力やルータ引数から変更できない | `{OwnershipTransfer}` |
| TEST-IPCR-22 | 応答コードのpending境界 | 受信直後のメッセージ | `response_code`を確認し、受信側がコードを設定して`reply`する | 受信直後は`0xffffffff`、reply後は設定値となり、pendingのままreplyできない | `{OwnershipTransfer}` |
| TEST-IPCR-23 | エコー応答 | 受信側がKVを変更しない | `reply(message, code)`を実行する | 送信側は同じメッセージオブジェクトを受け取り、要求KVと応答コードを取得する | `{IPC_ZeroCopy}` |
| TEST-IPCR-24 | 追加データ付き応答 | 受信側が`RECEIVER_OWNS` | 受信側がKVを追加して`reply`する | 送信側は追加KVを含む同じメッセージを取得する。物理コピーは発生しない | `{IPC_ZeroCopy}` |
| TEST-IPCR-25 | 応答前の送信側ブロック | 要求のRendezvousが成立し、受信側が処理中 | 受信側がreplyする前のScheduler状態を確認する | 送信元TCBは応答待ちで起床せず、reply後にだけREADYへ戻る | `{ADR_RendezvousChannel}` |
| TEST-IPCR-26 | 内部KVバッチの構築契約 | 一意なu32 KV、最大8要素、実体容量内 | 正常生成例と重複・容量外・u32外のバッチを書き込む | 正常例はソート済み実体から検索できる。契約違反は書込み前にassertし、全共有バイトを保存する | `test_ipc_batch_sorted_unique_map_roundtrip`、`test_ipc_invalid_internal_batch_asserts_before_write` |
| TEST-IPCR-27 | KVエントリのu64 wire配置 | keyとvalueに異なる32bitパターンを使う | SharedBlockのwriterとreaderを別々に実行する | u64上位32bitはkey、下位32bitはvalueである。writerの全byte列を独立した既知値と比較する。readerには規定byte列を直接入力する。隣接領域を保存する | `ipc_router.md`「IPCメッセージ」、`test_mem_10_entry_writer_matches_wire_layout`、`test_mem_10_entry_reader_decodes_independent_wire_layout` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-IPCR-01} {GOTCHA-IPCR-02} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-IPCR-01 | 単一要求・応答トランザクションとキュー完全不在 | 同一 CSP エッジへ要求を送信し、応答待ち状態にする | 同一エッジへさらに `send` を試行 | `ERR_QUEUE_FULL` のような差し戻しエラーではなく、即座にアサーション違反（プログラミングエラー）で停止する。 | [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md), `{ADR_RendezvousChannel}` |
| GOTCHA-IPCR-02 | Preflight Rejection による所有権保全 | RBAC 拒否エッジまたは未登録 URI 宛のメッセージ送信 | `send` を実行 | 権限・URI・サイズ検証がメッセージ Revoke（所有権剥奪）の前に先行して行われ、エラー時は所有権が `SENDER_OWNS` のまま1ミリも動かない。 | [`ipc_router.md`](docs/components/tier1_interface/ipc_router.md), `{OwnershipTransfer}` |


## 3. テスト検証実績と網羅状況

### 3.1 契約と実行ケースの対応
<!-- traceability: {GOTCHA-IPCR-01} {GOTCHA-IPCR-02} -->

要求と応答の双方へ同期ランデブーを適用する。現行の`channel_reply`は元の送信側が応答受信を待機する前に値を保存して復帰するため、応答側先行時のサスペンド検査は未完了である。既存ケースの成功をこの契約の検証済み証拠として扱わない。ランデブー成立後はCOOSの実行順序に従い、クライアントが先にCPUを使用することは期待条件に含めない。

| テストケースID | 実行可能テスト | 実際に確認する範囲 |
| :--- | :--- | :--- |
| TEST-IPCR-02/04/05/06/15 | `test_lookup_matches_specification_permission_matrix` | 登録URIのロール、72組の許可・拒否、許可チャネルの同一性、拒否エッジの不在 |
| TEST-IPCR-02/05 | `test_ipc_01_uri_lookup_and_permission_matrix`、`test_ipc_06_router_lookup_authorization` | 登録URIの検索、実際の偽装send拒否、lookupのRBAC拒否。拒否sendがyieldした場合も失敗する。 |
| TEST-IPCR-03 | `test_unknown_uri_rejection_preserves_message` | 未登録URIのERR_NOT_FOUND、内容・所有者・ページ世代・タスク状態の保全 |
| TEST-IPCR-05/14/15、GOTCHA-IPCR-02 | `test_send_preflight_rejection_preserves_shared_memory` | 実際のsend拒否、メッセージとリソースの所有者・内容・ページ世代、チャネル待機状態の保全、拒否後の再利用 |
| TEST-IPCR-07/11/12/21/22/23/24/25 | `test_request_reply_preserves_contents_and_transfers_exclusive_ownership` | 到達順と応答形式の4組、認証済み送信元ID、全KV、物理アドレス、リソース内容、所有者推移、失効ハンドル拒否、pending応答拒否、送信元の応答待機 |
| TEST-IPCR-07/21/22/24 | `test_ipc_02_e2e_shared_block_transfer` | 実コルーチンのスケジューリング、同じメッセージへの追加応答、送信元への返却 |
| TEST-IPCR-07/19 | `test_ipc_07_message_in_shm_and_payload_shm_transfer` | メッセージ本体と埋込みリソースの要求・応答移譲 |
| TEST-IPCR-08/10、GOTCHA-IPCR-01 | `test_second_send_cannot_overwrite_active_transaction` | 要求待機と応答待機の二重送信拒否、最初の要求の保全、応答後のチャネル再利用 |
| TEST-IPCR-13 | `test_key_scope_type_and_identifier_have_independent_bit_positions` | 24組の独立したビット列とpack/unpack双方の一致 |
| TEST-IPCR-17/18 | `test_ipc_04_select_recv_picks_first_ready_sender_and_clears_group` | 複数エッジの外部選択、敗退エッジ解除、後続の独立ランデブー |
| TEST-IPCR-19 | `test_ipc_05_message_storage_ownership_and_access_check` | ソート済みKVの検索、所有権状態によるアクセス拒否。 |

TEST-IPCR-01/20は参考実装の`test_registry_is_a_real_flat_map_view_not_a_dict`と`test_same_role_uri_instances_use_distinct_channels`に対応する。TEST-IPCR-16は設計記述とSchedulerのチャネル契約を照合するレビュー項目である。TEST-IPCR-09/12の無期限待機は有限操作列だけでは証明しない。

### 3.2 局所実行結果

- 実行日: 2026-10-01。
- 対象ソース: [`ipc_router.py`](experiments/pysim/tier1_interface/ipc_router.py)、[`scheduler.py`](experiments/pysim/tier1_core/scheduler.py)、[`manager.py`](experiments/pysim/tier2_runtime/memory/manager.py)。
- テストスイート: [`test_ipc_router.py`](experiments/pysim/qa/tier1_interface/test_ipc_router.py)。
- 環境: Linux、プロジェクトのPython 3.14環境、実共有メモリアダプタとScheduler。
- 成功121件、失敗0件、skip 0件。
- 従来111件に、KV生成例、内部バッチ違反6条件、直接借用、実移譲後の旧ビュー失効、不正countの即時検出を追加した。
- 参考実装、形式モデル、ベンチマークはこの局所実行に含めない。

実行コマンド:

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --offline --no-sync python -m pytest -q experiments/pysim/qa/tier1_interface/test_ipc_router.py
```

### 3.3 直接借用と所有権の証拠

`test_ipc_borrow_and_narrowed_entries_read_live_shared_bytes`はビュー生成時のKV読出しが0回であることを検査する。
8要素の探索はKV読出し5回以下である。
狭めた区間のentriesも、同じ所有者が変更した共有実体を直接参照する。
`test_ipc_old_borrows_fail_after_actual_handoff_and_reply`は、Revoke前の借用がGrant後も失効することを検査する。
新しい所有者は再借用する。
ビュー世代や構造変更追跡のメタデータは追加しない。

## 4. 未検証・スコープ外

- `{LowLatencyLookup}`の実測ベンチマーク自体（[`low_latency_lookup_bench.py`](docs/components/tier1_interface/benchmarks/low_latency_lookup_bench.py)が正本）。
- C++実装での`fireball::flat_map_view<std::string_view, registry_entry>`のROM配置詳細。
- 同種デバイスの複数URI構成をpysimで実行する条件。既定レジストリは種別ごとに1インスタンスを登録する。参考実装のTEST-IPCR-20とは検証範囲を分ける。
- 全操作列における二重所有不在と無期限待機の安全性。有限操作列の状態検査と形式モデルの証拠を分ける。
- 実機MPU/PTE/TLBによる隔離。ここで検証するのはpysimのページ所有者とSharedBlockアクセス契約である。
