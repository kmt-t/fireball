# GDB RSP パーサ コンポーネント設計書

## 1. コンセプト
<!-- traceability: {RSPMinimalSet} {RSP_Transport_Selectable} {Debug_Integrated} -->
GDB RSP Parser は、HAL から受信したデバッグ通信シリアルバイト列（UART等）から GDB Remote Serial Protocol (RSP) パケットのフレーミング、チェックサム検証、およびコマンド/レスポンスのシリアライズ・デシリアライズを担当する。具象的なサポートコマンドセットおよび仮想レジスタマッピングの規格は [GDB RSP 物理仕様書 (`docs/specs/gdb_rsp_protocol.md`)](../../../specs/gdb_rsp_protocol.md) を正本とする。 `{RSPMinimalSet}` `{RSP_Transport_Selectable}` `{Debug_Integrated}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {RSPMinimalSet} -->
本コンポーネントは **Tier 3 (詳細リーフコンポーネント: Leaf Component)** に属し、デバッグマネージャ (`debug_manager.md`) から分解された GDB RSP パケットのフレーミング、構文解析、およびレスポンス生成を担当する。 `{META_3TierSeparation}` `{RSPMinimalSet}`

## 3. 静的モデル

### 3.1 データ構造
- **`RspParser`**: UART バッファからのバイトストリーム受信、`$` から `#` までのパケット抽出、およびチェックサム計算を行うクラス。
- **`RspSerializer`**: レスポンス文字列（`OK`, `S05`, `T05...`, 16進レジスタ/メモリ列）を構築し、チェックサムを付加して送信バッファへ書き出すクラス。

### 3.2 主要なクラス・構造体
| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 受信パケットバッファ | フレーミングされた 1 パケットの ASCII ペイロード | 固定長配列 | 256 Bytes (`FB_CONF_RSP_PACKET_MAX`) |
| パース状態 | 受信ステートマシン（Idle, Body, Checksum1, Checksum2） | 列挙型 | `rsp_parse_state` |
| 送信バッファ | 構築された応答パケット | 固定長配列 | 256 Bytes |

## 4. 動的モデル

### 4.1 アルゴリズム
1. **パケット受信とチェックサム検証**:
   - `$` 文字を受信するとパケット開始とし、後続の ASCII 文字を受信バッファへ蓄積しつつ算術合計（modulo 256）を計算する。
   - `#` 文字を受信後、続く 2 桁の 16 進数をチェックサム値として検証する。
   - 一致した場合は ACK (`+`) を送信し、不一致の場合は NAK (`-`) を送信して再送を促す。
2. **コマンドディスパッチ**:
   - パースしたコマンド種別（`?`, `g/G`, `m/M`, `c`, `s`, `Z0/z0`, `k`, `qSupported` 等）を `debug_command` 構造体へ変換し、[`debug_manager`](debug_manager.md) のコマンドキューへ投函する。
3. **レスポンス生成**:
   - `debug_manager` からの実行結果を受け取り、16進エンコードを行って `$<payload>#<checksum>` 形式でシリアライズして HAL 送信バッファへ出力する。

サポートコマンド仕様・パケット形式・仮想レジスタ番号の詳細は [GDB RSP 物理仕様書 (`docs/specs/gdb_rsp_protocol.md`)](../../../specs/gdb_rsp_protocol.md) を参照。
