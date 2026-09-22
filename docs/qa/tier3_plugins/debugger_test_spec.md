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
| TEST-DBG-02 | 仮想レジスタ全読み出し (`g`) | レジスタ値設定済み | `g` パケット送信 | `0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15` の 20 レジスタが 32-bit リトルエンディアン hex（各8文字）で連結返却される | 「仮想レジスタセット」 `{RSPMinimalSet}` |
| TEST-DBG-03 | 仮想レジスタ全書き込み (`G`) | 20レジスタ分の hex 文字列 | `G <hex>` 送信 | 各レジスタ（pc, sp, fp, tos, local0..15）が正確に上書き更新され `OK` を返す | 「仮想レジスタセット」 |
| TEST-DBG-04 | ゲストメモリ読み出し (`m`) | リニアメモリ初期化済み | `m <addr>,<len>` 送信 | 指定範囲のバイト列が hex 文字列として返却される | {RSPMinimalSet} |
| TEST-DBG-05 | ゲストメモリ読み出し境界外エラー | 範囲外 `addr` 指定 | `m <addr>,<len>` 送信 | `E01` エラーパケットが返却される | `{MemoryBoundaryCheck}` |
| TEST-DBG-06 | ゲストメモリ書き込み (`M`) | デバッグ構成でインタープリタとデバッガが有効 | `M <addr>,<len>:<hex>` 送信 | 境界内のメモリが上書きされ `OK` が返却される。デバッガはJITキャッシュを操作しない | `{MemoryBoundaryCheck}` |
| TEST-DBG-07 | ゲストメモリ書き込み境界外エラー | 範囲外 `addr` 指定 | `M <addr>,<len>:<hex>` 送信 | メモリは更新されず `E01` が返却される | `{MemoryBoundaryCheck}` |

### 実行制御 & ブレークポイント

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-08 | ソフトウェアブレークポイント追加・削除 (`Z0`/`z0`) | デバッガアタッチ状態 | `Z0,0x100,0` / `z0,0x100,0` 送信 | ブレークポイント集合への追加・削除が行われ `OK` が返却される | 「ブレークポイントリスト」 `{RSPMinimalSet}` |
| TEST-DBG-09 | ブレークポイントヒットによる実行停止 | PC=0x100 にブレークポイント設定 | `c`（継続実行）送信 | PC=0x100 到達時に実行が停止し、`S05`（SIGTRAP）が返却される | 状態遷移図 |
| TEST-DBG-10 | 単一命令ステップ実行 (`s`) | 停止状態 | `s` 送信 | ちょうど 1 命令だけ実行され、PC が進んだ状態で再び `S05` で停止する |  `{RSPMinimalSet}` |
| TEST-DBG-11 | プログラム正常終了 | 終端命令実行 | `c` 送信 | プログラム終了時に `W00`（正常終了）が返却される | 状態遷移図 |

### デバッガ協調

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-12 | デバッグ無効時のゼロオーバーヘッド | デバッガ未接続 | 通常構成を実行 | 通常構成はデバッガを保持せず、デバッグ分岐のない実行経路を維持する | `{DebuggerInterpreterComposition}` |
| TEST-DBG-13 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成でデバッガアタッチ | 実行 | アタッチ中は常にインタープリタが実行され、JITへの動的切替もハンドラテーブル切替も発生しない | `{DebuggerInterpreterComposition}` |
### 実ソケット GDB RSP リモート接続・対話セッション

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-DBG-20 | TCP ソケットリッスンとクライアント接続 | GDBServer 起動 | クライアントが TCP 接続し `?` 送信 | `+` ACK と `$S05#b8` が返り、対話デバッグセッションが確立される | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-21 | ソケット経由の仮想レジスタ読み書き | セッション接続中 | `g` および `G` パケット送信 | TCP ストリーム経由で 20 個の仮想レジスタが正しく取得・変更される | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-22 | ソケット経由のメモリ検査・書き換え | セッション接続中 | `m` および `M` パケット送信 | TCP ストリーム経由でメモリが読み書きされる。JITキャッシュ操作は発生しない | `{MemoryBoundaryCheck}` |
| TEST-DBG-23 | ソケット経由のブレークポイント停止とステップ | セッション接続中 | `Z0` 設定後 `c` / `s` 送信 | 指定 PC で正確にトラップ停止し、単歩ステップ実行で 1 命令進む | [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-24 | プログラム完走通知とソケット正常切断 | ブレークポイント解除 | `c` 送信後クローズ | 終了パケット `$W00#b7` を受信し、サーバーソケットがクリーンに終了・デタッチされる | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md), [`debugger.md`](docs/components/tier3_plugins/debugger.md) |
| TEST-DBG-25 | デバッガSinkの静的差し替え | `DebuggerSink`互換のテスト用物理Sinkを構成 | TCPを使わず同じRSPバイト列をSinkへ入出力する | GDBServerはRSP解析を維持したまま物理Sinkだけを差し替えられ、デバッガの状態制御と応答が同一になる | `{RSP_Transport_Selectable}`, [`debugger.md`](docs/components/tier3_plugins/debugger.md) |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-DBG-01 | デバッガとJITの同時構成拒否 | `RuntimeCompositionConfig(execution=JIT, debugger=True)` | ランタイム構成を合成する | 構成時 `assert` で拒否され、デバッガがJITキャッシュを操作する経路は生成されない | `debugger.md`, `{DebuggerInterpreterComposition}` |
| GOTCHA-DBG-02 | アタッチ中のインタープリタ専用実行 | `Interpreter + Debugger` 構成 | デバッガをアタッチして実行する | アタッチ中もインタープリタだけが実行され、JIT実行器およびデバッグ専用ハンドラテーブルへの切替は発生しない | `debugger.md`, `{DebuggerInterpreterComposition}` |
| GOTCHA-DBG-03 | GDB RSP チェックサム照合と再送制御（通信化け耐性） | GDB リモートセッション接続中 | チェックサムが不一致の破損パケットを送信 | サーバーはパケットを破棄し、NAK（`-`）を返信してクライアントに再送を要求する。**実装の勘所**: チェックサム検証を怠って破損パケットを解釈すると、誤ったメモリアドレスや不正レジスタ値が書き込まれてデバッグ対象がクラッシュする | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md) |
| GOTCHA-DBG-04 | 協調スケジューラ下での RSP 応答分割送出と複数 yield 跨ぎ耐性 | COOS 協調スケジューラ上で GDBServer タスクが動作中 | 長い応答パケット（`g` 等）を要求し、クライアント側で完全な RSP フレーム（`$...#xx`）を受信 | ACK（`+`）とペイロード（`$...#xx`）を同じ送信キューへ積む。ノンブロッキング送信と複数 yield に跨るドレイン処理により、応答全体を送受信する。**実装の勘所**: 協調スケジューラ下で `sendall()` を使うと、部分送信が脱落する。`tx_buffer` で `send()` のフラグメント状態を管理し、yield 境界で確実にフラッシュする。ACK とペイロードを別々に即時送信すると、TCP セグメントが分割される。1回の `scheduler.step()` で応答全体を送れず、複数 yield に跨る。テスト側とクライアント側は1回の yield / recv で完了すると仮定しない。フレーム終端（`#` と2桁の16進数）まで受信をバッファする。 | [`gdb_rsp_protocol.md`](docs/specs/gdb_rsp_protocol.md) `{GOTCHA-DBG-04}` |

## 3. テスト検証実績と網羅状況

- **GDB RSP 通信 & 仮想レジスタ (TEST-DBG-01〜07)**: `?`, `g`, `G`, `m`, `M`, `Z0`, `z0` のパケット解析・応答およびレジスタ/メモリ操作を検証。
- **実行制御 (TEST-DBG-08〜11)**: ブレークポイント停止、ステップ実行、正常終了通知を検証。
- **デバッグ構成 (TEST-DBG-12〜13)**: 通常構成のデバッグ分岐なしと、アタッチ中のインタープリタ専用実行を検証する。
- **実ソケット GDB リモート対話セッション (TEST-DBG-20〜24)**: GDB RSP の TCP 接続、パケット交換、実行制御、終了通知を検証する。

## 4. 未検証・スコープ外

- VSCode / J-Link 実機ハードウェアを物理接続したエンドツーエンド通信テスト。
