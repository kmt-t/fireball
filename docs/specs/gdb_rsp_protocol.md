# GDB Remote Serial Protocol 物理仕様書 (Supported GDB RSP Protocol) {VERIFY_LLM}

## 1. 概要と基本思想
<!-- traceability: {DebuggerInterpreterComposition} {Debug_Integrated} {WasmCodeSectionPC} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Hypervisor がホスト GDB クライアントへ提供する **GDB Remote Serial Protocol (RSP)** の正本である。UART またはデバッグシリアルで使用するパケット形式とサポートコマンドを定義する。WASM 仮想レジスタ番号のマッピングも定義する。

デバッグ実行用のランタイムは、起動時にインタープリタとデバッガを静的に構成する。デバッグセッションのアタッチ中は常にインタープリタだけを実行し、インタープリタのハンドラテーブルは切り替えない。デバッガはJITキャッシュを管理せず、ゲストメモリを書き換えてもキャッシュ無効化を要求しない。

RSPのPC値と`Z0`/`z0`のアドレスは、WASM Code section payload先頭を0とするモジュール内オフセットであり、命令先頭バイトを指す。payload内の関数数、body size、locals宣言もオフセットに含む。関数内オフセットやWASMファイル全体の位置は渡さない。

---

## 2. パケット構造とチェックサム規約

<!-- traceability: {DebuggerInterpreterComposition} -->

GDB RSP パケットは ASCII 文字列で送受信され、以下のフレーム構造を持つ：

```
$<payload>#<checksum>
```

- **`$` (0x24)**: パケット開始マーカー。
- **`<payload>`**: コマンドまたはレスポンスの ASCII 文字列。
- **`#` (0x23)**: ペイロード終了マーカー。
- **`<checksum>`**: ペイロード全バイトの算術合計（modulo 256）を表す 2 桁の 16 進数（小文字/大文字）。{RSPChecksumVerify} <!-- definition: {RSPChecksumVerify} -->
- **ACK / NAK**: 正常受信時は `+` (0x2B)、再送要求時は `-` (0x2D) を 1 バイト返却。
- チェックサム不一致、16進表記の不正、またはフレーム長の不一致ではコマンドを実行せず `-` を返す。一致を検証した後だけ `+` を返してディスパッチする。

---

## 3. サポートコマンド・マトリクス (GDB RSP Commands)

| コマンド | ペイロード形式 | レスポンス形式 | 物理実装・動作 |
| :--- | :--- | :--- | :--- |
| **停止理由クエリ** | `?` | `S05` または `T05thread:01;` | カレントタスクの停止シグナル（`SIGTRAP` = 5）を返却。 |
| **レジスタ一括読出** | `g` | `<hex_data>` (XX...XX) | WASM 仮想レジスタ群（PC, SP, FP, TOS, Locals）の値を 16 進文字列で返却。 |
| **レジスタ一括書込** | `G <hex_data>` | `OK` または `E01` | 指定された 16 進データで WASM 仮想レジスタ群を一括更新。 |
| **レジスタ個別読出** | `p <reg_hex>` | `<hex_data>` または `E01` | 指定されたレジスタ番号（16進）の値を返却。 |
| **レジスタ個別書込** | `P <reg_hex>=<val_hex>` | `OK` または `E01` | 指定されたレジスタ番号に値を書き込み。 |
| **メモリ読出** | `m <addr_hex>,<length_hex>` | `<hex_data>` または `E01` | ゲストリニアメモリ、または統合スタックの指定範囲を 16 進バイト列で読出。境界外アクセスは `E01`。 |
| **メモリ書込** | `M <addr_hex>,<length_hex>:<hex_data>` | `OK` または `E01` | ゲストリニアメモリ、または統合スタックの指定範囲へバイト列を書き込み。 |
| **継続実行** | `c` [ `<addr_hex>` ] | (停止時に `T05...` を返却) | 実行を再開。ブレークポイント到達または割り込みまでインタープリタ実行。 |
| **単一ステップ実行** | `s` [ `<addr_hex>` ] | `T05thread:01;` | WASM 命令を 1 命令だけ実行して即座に停止。 |
| **ブレークポイント設定**| `Z0,<addr_hex>,<kind>` | `OK` または `E01` | ソフトウェアブレークポイントを登録（`debugger` の `flat_set_view` に PC を挿入）。 |
| **ブレークポイント削除**| `z0,<addr_hex>,<kind>` | `OK` または `E01` | ソフトウェアブレークポイントを削除（`flat_set_view` から PC を削除）。 |
| **機能クエリ** | `qSupported` | `PacketSize=256` | 実装済みのパケットバッファ最大長だけを通知する。 |

---

## 4. WASM 仮想レジスタ番号マッピング
<!-- traceability: {ContextPointerRegister} {DebuggerInterpreterComposition} -->

GDB クライアントが参照するレジスタ番号と、Fireballの実行コンテキスト値の対応：

| GDB レジスタ番号 | レジスタ名 | ビット幅 | 論理ソース（`execution_context` / オペランドスタック） |
| :--- | :--- | :--- | :--- |
| **`0`** | `pc` | 32-bit | Debuggerがアクティブ関数の命令カーソルへ関数命令開始PCを加えて得る、Code section payload相対PC |
| **`1`** | `sp` | 32-bit | `execution_context.sp_offset`（スタックボトムから数えた32-bitスロット数。pysimでは`len(ctx.stack)`に対応する。詳細は[`interpreter.md`](docs/components/tier3_executer/interpreter.md)を参照） |
| **`2`** | `fp` | 32-bit | 固定値 `0`。pysimの公開コンテキストは独立したフレームポインタを持たず、書込みも `0` のみ許可する。 |
| **`3`** | `tos` | 32-bit | オペランドスタック最上位の値。`sp_offset` は次の空きスロットを指すため、スロット添字では `stack[sp_offset - 1]` |
| **`4`** | `local0` | 32-bit | 固定長ローカルスタック上の値 0 |
| **`5`** | `local1` | 32-bit | 固定長ローカルスタック上の値 1 |
| **`6`** | `local2` | 32-bit | 固定長ローカルスタック上の値 2 |
| **`7`** | `local3` | 32-bit | 固定長ローカルスタック上の値 3 |
| **`8..19`**| `local4..15` | 32-bit | 固定長ローカルスタック上の値 4〜15 |

`g` の応答と `G` / `p` / `P` の値は、各32-bitレジスタを4バイトのlittle-endian順で16進表記する。`G` / `P` はPC、SP、TOS、対象ローカルを更新する。SPは固定スタック容量以下でなければならない。空スタックではTOSも0でなければならない。FPは固定値のため0以外の書込みを`E01`で拒否する。

---

## 5. 非サポートコマンド (Explicit Non-Goals)
<!-- traceability: {GLOBAL_StrictMemoryLimit} -->

極小マイコン向けデバッグサーバのため、以下の複雑な GDB 拡張機能は非サポートとし、空パケット（`$#00`）を返却する：
- ハードウェアウォッチポイント (`Z2`, `Z3`, `Z4`)
- マルチプロセスデバッグ (`vAttach`, `vRun`)
- デバッグ対象の終了・再起動 (`k`)
- ターゲット側ブレークポイント評価式（Bytecode Agent）
- 逆方向デバッグ (Reverse Execution: `bs`, `bc`)
