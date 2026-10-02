# Debugger プラグイン テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`debugger.md`](docs/components/tier3_plugins/debugger.md)
関連正本: [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md)

GDB RSP コマンド処理（`?`, `g/G`, `m/M`, `Z0/z0`, `s`, `c`）、ブレークポイント管理（`fireball::flat_set_view`）、インタープリタ専用デバッグ構成（`DebuggerInterpreterComposition`）、および仮想レジスタセットを検証する。

## 2. テストケース一覧

### GDB RSP プロトコル & 仮想レジスタ ({RSPMinimalSet}, gdb_rsp_protocol.md)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-01 | 停止要因問い合わせ (`?`) | デバッガアタッチ・停止状態 | `?` パケット送信 | 直近の停止理由（`S05` = SIGTRAP）を正しく返す |  `{RSPMinimalSet}` |
| TEST-DBG-02 | 仮想レジスタ全読み出し (`g`) | レジスタ値設定済み | `g` パケット送信 | `0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15` の 20 レジスタが 各32-bit値をlittle-endianの4バイト表現（各8文字）にして連結返却する | 「仮想レジスタセット」 `{RSPMinimalSet}` |
| TEST-DBG-03 | 仮想レジスタ全書き込み (`G`) | 有効な20レジスタ分のhex文字列 | `G <hex>`を送信 | PC、SP、TOS、local0..15が更新され`OK`を返す。スタック容量超過、空スタックの非0 TOS、固定値FPへの非0書込みは`E01`となり状態を変えない | 「仮想レジスタセット」 |
| TEST-DBG-03b | 個別レジスタと実装能力の応答 | デバッガ停止中 | `p4`、`P4=2a000000`、`qSupported`を送る | little-endianの4バイト値でlocal0を個別読書きでき、`PacketSize=256`だけを広告する | `gdb_rsp_protocol.md` |
| TEST-DBG-03c | 不正な個別レジスタパケット | 停止中のデバッガ | 不正hex `p` と区切りなし `P` を送る | `E01`を返しPCとレジスタ状態を変更しない | `test_dbg_03c_malformed_register_packets_are_rejected` |
| TEST-DBG-04 | ゲストメモリ読み出し (`m`) | リニアメモリ初期化済み | `m <addr>,<len>` 送信 | 指定範囲のバイト列が hex 文字列として返却される | {RSPMinimalSet} |
| TEST-DBG-05 | ゲストメモリ読み出し境界外エラー | 範囲外 `addr` 指定 | `m <addr>,<len>` 送信 | `E01` エラーパケットが返却される | `{MemoryBoundaryCheck}` |
| TEST-DBG-06 | ゲストメモリ書き込み (`M`) | デバッグ構成でインタープリタとデバッガが有効 | `M <addr>,<len>:<hex>` 送信 | 境界内のメモリが上書きされ `OK` が返却される。デバッガはJITキャッシュを操作しない | `{MemoryBoundaryCheck}` |
| TEST-DBG-07 | ゲストメモリ書き込み境界外エラー | 範囲外 `addr` 指定 | `M <addr>,<len>:<hex>` 送信 | メモリは更新されず `E01` が返却される | `{MemoryBoundaryCheck}` |

### 実行制御 & ブレークポイント

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-08 | ソフトウェアブレークポイント追加・削除 (`Z0`/`z0`) | デバッガアタッチ状態 | `Z0,0x100,0` / `z0,0x100,0` 送信 | ブレークポイント集合への追加・削除が行われ `OK` が返却される | 「ブレークポイントリスト」 `{RSPMinimalSet}` |
| TEST-DBG-08b | ブレークポイント固定容量超過 | ブレークポイント表が満杯 | 追加の`Z0`を送る | `E01`を返し、既存項目を維持して新規項目を追加しない | `test_dbg_08b_breakpoint_capacity_returns_protocol_error` |
| TEST-DBG-08c | PCの32bit幅の超過 | PC=0、7、0xffffffffを登録済み | `Z0,100000000,0`と`z0,100000000,0`を送る | 両方とも`E01`を返し、PCと既存配列を変更しない | `test_dbg_08c_breakpoint_pc_overflow_is_rejected_without_state_change`、`gdb_rsp_protocol.md` |
| TEST-DBG-09 | ブレークポイントヒットによる実行停止 | PC=0x100 にブレークポイント設定 | `c`（継続実行）送信 | PC=0x100 到達時に実行が停止し、`S05`（SIGTRAP）が返却される | 状態遷移図 |
| TEST-DBG-10 | 単一命令ステップ実行 (`s`) | 停止状態 | `s` 送信 | ちょうど 1 命令だけ実行され、PC が進んだ状態で再び `S05` で停止する |  `{RSPMinimalSet}` |
| TEST-DBG-11 | プログラム正常終了 | 終端命令実行 | `c` 送信 | プログラム終了時に `W00`（正常終了）が返却される | 状態遷移図 |

### デバッガ協調

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-12 | デバッグ無効時のゼロオーバーヘッド | デバッガ未接続 | 通常構成を実行 | 通常構成はデバッガを保持せず、デバッグ分岐のない実行経路を維持する | `{DebuggerInterpreterComposition}` |
| TEST-DBG-13 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成でデバッガアタッチ | 実行 | アタッチ中は常にインタープリタが実行され、JITへの動的切替もハンドラテーブル切替も発生しない | `{DebuggerInterpreterComposition}` |
| TEST-DBG-14 | PC頻度サンプリング | デバッガへ実行PCが通知される | 異なるPCと繰り返しPCを通知する | 各PCの頻度が通知回数と一致し、他PCの記録を変えない | `{Debug_Integrated}`、[`requirement_list.md`](docs/requires/requirement_list.md) |
| TEST-DBG-15 | メモリassertの値照合 | ゲストメモリと期待バイト値を登録済み | 実行後のメモリを検査する | 一致する条件は違反なし、不一致のアドレス・期待値・実値を違反として記録する | `{Debug_Integrated}`、[`requirement_list.md`](docs/requires/requirement_list.md) |

### 実ソケット GDB RSP リモート接続・対話セッション
<!-- traceability: {RSP_Transport_Selectable} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-20 | TCP ソケットリッスンとクライアント接続 | GDBServer 起動 | クライアントが TCP 接続し `?` 送信 | `+` ACK と `$S05#b8` が返り、対話デバッグセッションが確立される | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-21 | ソケット経由の仮想レジスタ読み書き | セッション接続中 | `g` および `G` パケット送信 | TCP ストリーム経由で 20 個の仮想レジスタが正しく取得・変更される | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-22 | ソケット経由のメモリ検査・書き換え | セッション接続中 | `m` および `M` パケット送信 | TCP ストリーム経由でメモリが読み書きされる。JITキャッシュ操作は発生しない | `{MemoryBoundaryCheck}` |
| TEST-DBG-23 | ソケット経由のブレークポイント停止とステップ | セッション接続中 | `Z0` 設定後 `c` / `s` 送信 | 指定 PC で正確にトラップ停止し、単歩ステップ実行で 1 命令進む | [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-24 | プログラム完走通知とソケット正常切断 | ブレークポイント解除 | `c` 送信後クローズ | 終了パケット `$W00#b7` を受信し、サーバーソケットがクリーンに終了・デタッチされる | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-25 | デバッガSinkの静的差し替え | `DebuggerSink`互換のテスト用物理Sinkを構成 | TCPを使わず同じRSPバイト列をSinkへ入出力する | GDBServerはRSP解析を維持したまま物理Sinkだけを差し替えられ、デバッガの状態制御と応答が同一になる | `RSP_Transport_Selectable`, [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-26 | チェックサム不一致パケットの破棄 | デバッグセッション接続中 | 不正なチェックサム付き`M`パケットと正しい`?`を順に送る | 不正パケットへ`-`を返し、メモリを変更せず、後続の正しいパケットを処理する | `{RSPChecksumVerify}`, `gdb_rsp_protocol.md` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）
<!-- traceability: {DebuggerInterpreterComposition} {RSPChecksumVerify} {GOTCHA-DBG-04} -->

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-DBG-01 | デバッガとJITの同時構成拒否 | `RuntimeCompositionConfig(execution=JIT, debugger=True)` | ランタイム構成を合成する | 構成時 `assert` で拒否され、デバッガがJITキャッシュを操作する経路は生成されない | `debugger.md` |
| GOTCHA-DBG-02 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成 | デバッガをアタッチして実行する | アタッチ中もインタープリタだけが実行され、JIT実行器およびデバッグ専用ハンドラテーブルへの切替は発生しない | `debugger.md` |
| GOTCHA-DBG-03 | GDB RSP チェックサム照合と再送制御（通信化け耐性） | GDB リモートセッション接続中 | チェックサムが不一致の破損パケットを送信 | サーバーはパケットを破棄し、NAK（`-`）を返信してクライアントに再送を要求する。**実装の勘所**: チェックサム検証を怠って破損パケットを解釈すると、誤ったメモリアドレスや不正レジスタ値が書き込まれてデバッグ対象がクラッシュする | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md) |
| GOTCHA-DBG-04 | 協調スケジューラ下での RSP 応答分割送出と複数 yield 跨ぎ耐性 | COOS 協調スケジューラ上で GDBServer タスクが動作中 | 長い応答パケット（`g` 等）を要求し、クライアント側で完全な RSP フレーム（`$...#xx`）を受信 | ACK（`+`）とペイロード（`$...#xx`）を同じ送信キューへ積む。ノンブロッキング送信と複数 yield に跨るドレイン処理により、応答全体を送受信する。**実装の勘所**: 協調スケジューラ下で `sendall()` を使うと、部分送信が脱落する。`tx_buffer` で `send()` のフラグメント状態を管理し、yield 境界で確実にフラッシュする。ACK とペイロードを別々に即時送信すると、TCP セグメントが分割される。1回の `scheduler.step()` で応答全体を送れず、複数 yield に跨る。テスト側とクライアント側は1回の yield / recv で完了すると仮定しない。フレーム終端（`#` と2桁の16進数）まで受信をバッファする。 | [`debugger.md`](docs/components/tier3_plugins/debugger.md) |

## 3. テスト検証実績と網羅状況

実装テストは [`test_debugger.py`](experiments/pysim/qa/tier3_plugins/debugger/test_debugger.py) と [`test_gdb_remote.py`](experiments/pysim/qa/tier3_plugins/debugger/test_gdb_remote.py) にある。
パケット解析、仮想レジスタ、メモリの読書き、固定容量ブレークポイント、チェックサム拒否、注入Sink、実TCP接続を検査する。
TEST-DBG-14〜15は実DebuggerManagerの記録・値照合を検査する。

実行制御はTier 2の`RuntimeComposer.compose_execution`がデバッグ有効構成へ結線する。
Tier 3の`InterpreterExecutionControl`は、同じNativeInterpreterの`step`とC++ dispatcherを再開する。
命令位置の停止判定は構成時に選択されたC++アスペクトが行う。
デバッガの停止要求はフックの状態であり、通常Interpreterの1命令実行APIや実行時モードへ追加しない。
通常構成の`void`アスペクトでは`if constexpr`が停止フックを除去する。
RSPの`c`はPythonへ命令ごとに復帰しない。
仮想レジスタ、operand stack、メモリは停止した実行状態を借用する。
ブレークポイントの固定長配列はDebuggerが単独所有し、C++フックは同じ配列を借用する。
QAブロックドライバはローダメタデータ取得と既存のブロック試験だけに使用する。
その独自handlerを、命令停止の証拠へ含めない。

`test_dbg_10_23_one_rsp_step_executes_only_one_wasm_instruction`は、RSPの1命令要求を固定した反例である。
`20 00 41 0a 6a 0b`と`local0=5`では、旧QA経路は1回の`s`でPC=6、stack=[15]まで進んでいた。
構成済みフックの経路ではPC=2、stack=[5]で停止する。
次の`s`はPC=4、stack=[5,10]で停止する。
同じ状態から`c`を再開すると結果15で正常終了する。
この反例を通常の回帰テストへ変更し、xfail指定を除去した。
撤回した1命令APIを用いた試行の成功件数は、仕様適合の証拠に採用しない。

| 要求 | 実行関数 | 独立した期待値と観測 |
| :--- | :--- | :--- |
| TEST-DBG-08c | `test_dbg_08c_breakpoint_pc_overflow_is_rejected_without_state_change` | 32bit最大PCを含む固定配列を保存し、幅超過を0へ丸めずE01で拒否する |
| TEST-DBG-10/23 | `test_dbg_10_23_one_rsp_step_executes_only_one_wasm_instruction` | PC=2/4と全stackを照合し、同じ呼出しから結果15へ継続する |
| TEST-DBG-09/10 | `test_dbg_09_interior_breakpoint_stops_before_side_effect_and_resumes` | ブロック内PC=2で止まり、書いたlocal0=7を使用して結果17へ継続する |
| TEST-DBG-10 | `test_dbg_10_call_and_return_stop_at_selected_function` | call/call_indirectでcallee先頭、endでcaller次命令へ止まり、結果12を得る |
| TEST-DBG-10 | `test_dbg_10_branch_stop_retains_control_state` | ifの真偽それぞれの固定PC列と全stackを照合する |
| TEST-DBG-10 | `test_dbg_10_loop_stop_keeps_local_and_control_frame_history` | 2回のloopのPC・値履歴を照合し、結果0へ継続する |
| TEST-DBG-10 | `test_dbg_10_store_does_not_execute_early_or_damage_other_bytes` | 各停止後の全65536byteを照合し、後続storeの先行実行を検出する |
| TEST-DBG-10 | `test_dbg_10_immediate_is_one_instruction` | 多byte LEB128とi64/f32/f64の固定PC・全生ワードを照合する |
| TEST-DBG-10 | `test_dbg_10_host_boundary_does_not_replay_the_import` | host importの引数7を1回だけ記録し、後続命令を先行実行しない |
| TEST-DBG-10/11 | `test_dbg_10_trap_stops_without_normal_exit_or_later_update` | PC=0/3のUNREACHABLEでs/cが停止し、global更新とW00を発生させない |
| TEST-DBG-12 | `test_dbg_12_disabled_composition_has_no_debug_state_or_weave` | 通常native入口と144byteのABIを維持し、構成時weaveを呼ばない |
| TEST-DBG-12/13 | `test_dbg_12_continue_returns_to_python_only_at_actual_stop` | 2002命令のcが既存native stepの1回で完了する |
| GOTCHA-DBG-01 | `test_dbg_13_composition_rejects_jit_before_creating_executor` | JITとDebuggerの同時構成を生成前に拒否する |
| TEST-DBG-12/13 | `test_dbg_13_two_compositions_keep_stop_state_and_storage_independent` | 通常構成と2つのデバッグ構成の状態・ブレークポイントを相互に変更しない |
| TEST-DBG-20〜24 | `test_gdb_remote_socket_session` | 実TCPのsでlocal.getだけを実行し、同じ状態から結果498とW00を得る |

実TCPの同じ結線はScenario 7と8でも検査する。

2026-10-01の局所回帰では、Debugger、GDB remote、Scenario 7と8の38件が成功した。
skipとxfailは0件だった。
通常ランナーは31スイート、672件が成功した。
局所回帰と通常ランナーの件数は重複する。
通常入口だけのリンク結果ではデバッガのアスペクトと入口シンボルが0件だった。
停止要求無視、ブレークポイント無視、trap PC誤記録、命令ごとの復帰の4変異を一時ライブラリへ入れた。
それぞれ対応する試験が失敗し、対象欠陥の反証能力を確認した。
この有限集合をmutation coverageの測定結果へ扱わない。

## 4. 未検証・スコープ外

- VSCode / J-Link 実機ハードウェアを物理接続したエンドツーエンド通信テスト。

- 全opcode、全trap原因、任意アドレス指定、および別関数へのPC書換えの組合せは網羅しない。
- TEST-DBG-12の絶対実行時間と実機の容量適合は未計測である。通常構成のフック除去と命令ごとのPython復帰がないことを、絶対性能の測定へ読み替えない。
