# GDB Remote Serial Protocol 物理仕様書 (Supported GDB RSP Protocol) {VERIFY_LLM}

## 1. 概要と基本思想
<!-- traceability: {DebuggerLabelTableSwitch} {Debug_Integrated} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Hypervisor が UART / デバッグシリアル経由でホスト GDB クライアントに提供する **GDB Remote Serial Protocol (RSP)** のパケットフォーマット、サポートコマンドセット、および WASM 仮想レジスタ番号マッピングを定義する正本である。

デバッグセッション確立時、Hypervisor は JIT 実行を無効化し、インタープリタのラベルテーブル切り替え（`{DebuggerLabelTableSwitch}`）により全命令境界でブレークポイント判定（`flat_set_view` 参照）とステップ実行を実現する。 `{DebuggerLabelTableSwitch}` `{Debug_Integrated}` `{META_ZeroCostAbstraction}`

---

## 2. パケット構造とチェックサム規約
<!-- traceability: {DebuggerLabelTableSwitch} -->

GDB RSP パケットは ASCII 文字列で送受信され、以下のフレーム構造を持つ：

```
$<payload>#<checksum>
```

- **`$` (0x24)**: パケット開始マーカー。
- **`<payload>`**: コマンドまたはレスポンスの ASCII 文字列。
- **`#` (0x23)**: ペイロード終了マーカー。
- **`<checksum>`**: ペイロード全バイトの算術合計（modulo 256）を表す 2 桁の 16 進数（小文字/大文字）。
- **ACK / NAK**: 正常受信時は `+` (0x2B)、再送要求時は `-` (0x2D) を 1 バイト返却。

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
| **ブレークポイント設定**| `Z0,<addr_hex>,<kind>` | `OK` または `E01` | ソフトウェアブレークポイントを登録（`debug_manager` の `flat_set_view` に PC を挿入）。 |
| **ブレークポイント削除**| `z0,<addr_hex>,<kind>` | `OK` または `E01` | ソフトウェアブレークポイントを削除（`flat_set_view` から PC を削除）。 |
| **機能クエリ** | `qSupported` | `PacketSize=256;qXfer:features:read+` | パケットバッファ最大長（256 Bytes）および XML ターゲット記述サポートを通知。 |
| **プロセス終了** | `k` | (接続切断) | デバッグ対象タスクを終了し、初期状態へリセット。 |

---

## 4. WASM 仮想レジスタ番号マッピング (GDB Target XML Map)
<!-- traceability: {ContextPointerRegister} {DebuggerLabelTableSwitch} -->

GDB クライアントが参照するレジスタ番号（Target Description XML）と、Fireball 統合スタック上の物理オフセットの対応：

| GDB レジスタ番号 | レジスタ名 | ビット幅 | 物理ソース（スタックボトム `execution_context` / 統合スタック） |
| :--- | :--- | :--- | :--- |
| **`0`** | `pc` | 32-bit | `R0 (ip)` (現在実行中の WASM バイトコードオフセット / PC) |
| **`1`** | `sp` | 32-bit | `execution_context.sp_offset` (スタックボトムから見た、現在積まれているオペランドスタックの成長長——次の空きスロットへのオフセット。`runtime_interpreter.md` §3.1「スタックの成長した長さ」と同一の量) |
| **`2`** | `fp` | 32-bit | `execution_context.frame_offset` (カレントコールフレームの開始オフセット) |
| **`3`** | `tos` | 32-bit | オペランドスタック最上位の値（`sp_offset` は次の空きスロットを指すため、最上位要素はその1つ手前: `[stack_bot + sp_offset - 4]`） |
| **`4`** | `local0` | 32-bit | カレント関数のローカル変数 0 (`[stack_bot + frame.local_offset + 0]`) |
| **`5`** | `local1` | 32-bit | カレント関数のローカル変数 1 (`[stack_bot + frame.local_offset + 4]`) |
| **`6`** | `local2` | 32-bit | カレント関数のローカル変数 2 (`[stack_bot + frame.local_offset + 8]`) |
| **`7`** | `local3` | 32-bit | カレント関数のローカル変数 3 (`[stack_bot + frame.local_offset + 12]`) |
| **`8..19`**| `local4..15` | 32-bit | カレント関数のローカル変数 4〜15 |

---

## 5. 非サポートコマンド (Explicit Non-Goals)
<!-- traceability: {GLOBAL_StrictMemoryLimit} -->

極小マイコン向けデバッグサーバのため、以下の複雑な GDB 拡張機能は非サポートとし、空パケット（`$#00`）を返却する：
- ハードウェアウォッチポイント (`Z2`, `Z3`, `Z4`)
- マルチプロセスデバッグ (`vAttach`, `vRun`)
- ターゲット側ブレークポイント評価式（Bytecode Agent）
- 逆方向デバッグ (Reverse Execution: `bs`, `bc`)
