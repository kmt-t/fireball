# システムコール (`fireball_call`) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/system_syscall.md`
関連正本: `docs/components/tier2_runtime/runtime_vmmio.md`（vMMIOアドレス空間・SYSCTL/VDMAレジスタ）、`docs/components/tier1_core/system_config.md`（アドレス定数）
参考実装: `docs/components/tier2_runtime/concepts/vmmio_concept.py`
現行実装: `experiments/pysim/system.py`（`FbSyscallId`, `WasiErrno`）

`fireball_call(id, arg0..arg5) -> u32` の実ID空間（System/vMMIO Generic/VDMA/IRQ/IPC/WASI）と、WASI `errno_t` 準拠の戻り値規約を検証する。

## 2. テストケース一覧

### System (`0x00`-`0x0F`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-01 | `SYS_YIELD`(0x01) | - | `fireball_call(0x01, ...)` | `0`を返す（`{CooperativeMultitasking}`要求の協調的yield） | §5.2 |
| SYS-02 | `SYS_HALT`(0x02) | - | `fireball_call(0x02, ...)` | システム停止状態になる（戻り値は規定なし） | §5.2 |
| SYS-03 | `SYS_RESET`(0x03) | - | `fireball_call(0x03, ...)` | `0`を返し、ゲストリセット相当の状態変化が起こる | §5.2 |

### vMMIO Generic (`0x10`-`0x1F`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-10 | `MMIO_READ32`成功 | 許可された物理アドレスに値が存在 | `fireball_call(0x10, addr, ...)` | `value`(u32)を返す | §5.3 |
| SYS-11 | `MMIO_READ32`境界外 | `addr`が`FB_CONF_VMMIO_ALLOWED_ADDRS`外 | 同上 | `ERR_OUT_OF_BOUNDS`相当のエラーコードを返す | §5.3, `{META_RestrictedPhysicalAccess}` |
| SYS-12 | `MMIO_WRITE32`成功/権限拒否 | 書き込み許可/不許可の2ケース | `fireball_call(0x11, addr, value,...)` | 許可時`0`、不許可時`ERR_ACCESS_DENIED`相当 | §5.3 |
| SYS-13 | `MMIO_READ8`/`MMIO_WRITE8` | 同上をバイト単位で | 同様の手順 | 同様の結果（幅8bit） | §5.3 |
| SYS-14 | `MMIO_BULK_READ`/`WRITE`のサイズ不正 | `byte_count`が不正（範囲外・0等） | 呼び出す | `ERR_INVALID_SIZE`相当を返す | §5.3 |
| SYS-15 | `MMIO_BULK_READ`のゲスト書き込み先境界チェック | `dest_offset`がゲストメモリ範囲外 | 呼び出す | `ERR_OUT_OF_BOUNDS`相当を返し、ゲストメモリ外への書き込みが発生しない | §5.3, §4.1 fb_offset_t |

### VDMA (`0x20`-`0x2F`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-20 | `VDMA_START`成功 | `src`/`dst`が共に許可アドレス | `fireball_call(0x20, src, dst, byte_count,...)` | `0`を返し、`byte_count`バイトが転送される | §5.4, runtime_vmmio.md §4.2 |
| SYS-21 | VDMA転送先がSHM(FC=14)の場合の所有権チェック | `dst`がSHMアドレスで、呼び出し元が非所有者 | `VDMA_START`を呼ぶ | `dispatch_access`と同一の権限チェックにより拒否される | runtime_vmmio.md §4.5 |
| SYS-22 | VDMA完了時の仮想割り込み通知（該当する場合） | 完了通知が要求されている | 転送完了後の状態を確認 | `IRQ_VDMA_DONE`相当が立つ | runtime_vmmio.md §4.2 手順4 |

### IRQ (`0x30`-`0x3F`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-30 | `IRQ_READ_FLAGS` | 事前にフラグを立てておく | `fireball_call(0x30,...)` | 立っているフラグをそのまま返す | §5.5 |
| SYS-31 | `IRQ_CLEAR`(mask) | フラグが立っている | `fireball_call(0x31, mask,...)` | 指定ビットのみクリアされ、`0`を返す | §5.5 |

### IPC (`0x40`-`0x4F`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-40 | `IPC_LOOKUP`成功 | URIが登録済み | `fireball_call(0x42, uri_offset, uri_len,...)` | `handle_id`(u32)を返す | §5.6, `{IPC_HandleBased}` |
| SYS-41 | `IPC_LOOKUP`未登録URI | URI未登録 | 同上 | errno相当を返す | §5.6 |
| SYS-42 | `IPC_SEND`成功 | 有効なhandle_id、キューに空きあり | `fireball_call(0x40, handle_id, msg_offset, msg_len,...)` | `0`を返す | §5.6, ipc_router.md |
| SYS-43 | `IPC_SEND`キュー満杯 | キューが`max_queue`に到達 | 同上 | errno相当（Rollback、ipc_router.md `ERR_QUEUE_FULL`対応）を返す | ipc_router.md 4.1 |
| SYS-44 | `IPC_RECV`成功 | キューにメッセージあり | `fireball_call(0x41, handle_id, buf_offset, buf_len,...)` | `recv_len`(u32)を返し、`buf_offset`にメッセージがコピーされる | §5.6 |
| SYS-45 | `IPC_RECV`空バッファ | キューが空 | 同上 | 本来はコルーチンサスペンドが要求されるが、`fireball_call`は同期呼び出しであるため即時に何らかのerrnoを返す（実装依存点。§3参照） | §5.6「バッファが空の場合はコルーチンがサスペンドされる」 |

### WASI (`0x80`-`0xBF`)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-80 | `WASI_FD_WRITE` | fd=1（stdout）、iovecが1件 | `fireball_call(0x80, fd, iovs_ptr, iovs_len, nwritten_ptr,...)` | `console-output.write`相当が呼ばれ、`nwritten_ptr`に書き込みバイト数が入り、`0`(errno)を返す | §5.7, interface_wit.md §5.5 |
| SYS-81 | `WASI_FD_READ` | 実stdin相当のデータなし | `fireball_call(0x81,...)` | 0バイト読み取り(EOF)としてerrno `0`を返す | §5.7 |
| SYS-82 | `WASI_FD_CLOSE` | 任意のfd | `fireball_call(0x82, fd,...)` | `0`を返す | §5.7 |
| SYS-83 | `WASI_CLOCK_TIME_GET` | - | `fireball_call(0x83, clock_id, precision, time_ptr,...)` | `time_ptr`に単調増加するナノ秒値が書き込まれる | §5.7, interface_wit.md §5.1/5.6 |
| SYS-84 | `WASI_PROC_EXIT` | - | `fireball_call(0x84, exit_code,...)` | プロセス終了相当の状態変化（戻り値なし） | §5.7 |
| SYS-85 | `WASI_RANDOM_GET` | - | `fireball_call(0x85, buf_ptr, buf_len,...)` | `buf_ptr`に`buf_len`バイトのランダムデータが書き込まれ、`0`を返す | §5.7 |

### 共通・エラー処理

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-90 | 未定義ID | 予約済み範囲・未割当ID | `fireball_call(未定義ID,...)` | 定義されたエラーコード（WASI `errno_t`準拠、実装は`ENOSYS`相当）を返す | §4.2 |
| SYS-91 | 戻り値は常にWASI `errno_t`準拠 | 任意の失敗ケース | 各失敗パスの戻り値を確認 | プロジェクト独自の非標準エラーコードを使わない | §4.2「WASIの`errno_t`に準拠」 |
| SYS-92 | `fb_offset_t`のゲスト境界チェック | offset引数がゲストメモリ範囲外 | 該当syscallを呼ぶ | 即座に境界外エラーを返す（ゲスト境界チェックで「瞬時に」判定、§4.1） | §4.1 |

## 3. 現状のギャップ（pysim実装との差分）

- SYS-45（`IPC_RECV`空バッファ時の「コルーチンサスペンド」）は同期的な`fireball_call`呼び出し境界を跨いだ本物のサスペンドをモデル化できないため、pysimは`WasiErrno.AGAIN`を返す設計を採用している。これは仕様の要求する挙動そのものではなく、代替である旨をコードとこの仕様書双方に明記する必要がある（README「既知の制限」参照）。
- SYS-21（VDMA→SHM所有権チェック）・SYS-14/15（MMIO_BULK系の境界チェック）はpysimで未検証（テスト未実装）。
- SYS-40〜44のIPC系はpysimで実装・テスト済み（`experiments/pysim/tests.py`）。ただしSYS-43の`max_queue`到達ケースは`ipc_router_concept.py`固定レジストリの`fireball://hal/gpio/0`（`max_queue=2`）に限定されて検証されている。

## 4. 未検証・スコープ外

- `fireball-call0`〜`fireball-call6`の各アリティ別バリアント自体の呼び出し規約差異（pysimは6引数版のみ実装、§3参照）。
- WASI errnoの完全な数値表（`wasi_snapshot_preview1`標準）とのすべての対応関係の網羅（pysimは使用する範囲のみ実装）。
