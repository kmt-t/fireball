# システムコール (`fireball_call`) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier1_core/system_syscall.md`
関連正本: `docs/components/tier2_runtime/runtime_vmmio.md`（vMMIOアドレス空間・SYSCTL/VDMAレジスタ）、`docs/components/tier1_core/system_config.md`（アドレス定数）
参考実装: `docs/components/tier2_runtime/concepts/vmmio_concept.py`

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
| SYS-42 | `IPC_SEND`成功 | 有効なhandle_id | `fireball_call(0x40, handle_id, msg_offset, msg_len,...)` | 受信側が既に待機していれば即座に、まだ到達していなければ呼び出し元タスクのコルーチンが協調スケジューラ上でブロックし、受信側到達後に`0`を返す（キューは存在しないため、待機はブロックのみで失敗経路はない） | §5.6, ipc_router.md |
| SYS-43 | `IPC_SEND`宛先未登録／RBAC拒否／サイズ超過 | 未登録URIから得たhandle_id、または許可されないロール、または9個以上のkv_pair | 同上 | errno相当（`ERR_NOT_FOUND`/`ERR_PERMISSION_DENIED`/`ERR_MSG_TOO_LARGE`のいずれかに対応）を即座に返す。所有権は最初から送信側のまま動いていない | ipc_router.md §4.1, §5.1 |
| SYS-44 | `IPC_RECV`成功 | 送信側が既に到達している、または有効なhandle_id | `fireball_call(0x41, handle_id, buf_offset, buf_len,...)` | 送信側が既に待機していれば即座に、まだ到達していなければブロックして待ち、到達後に`recv_len`(u32)を返し、`buf_offset`にメッセージがコピーされる | §5.6 |
| SYS-45 | `IPC_RECV`相手未到達 | 送信側がまだ到達していない | 同上 | `fireball_call`の呼び出し元タスクのコルーチンが協調スケジューラ上でブロックし、送信側が到達するまで再開しない（EAGAINのような即時errnoは返さない。ブロックがCSPランデブーの本来の意味論であり、実装依存の妥協ではない） | §5.6「バッファが空の場合はコルーチンがサスペンドされる」, `experiments/pysim/system.py` `_ipc_recv` |

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

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SYS-GOTCHA-01 | 未定義 Syscall ID の非パニック・ENOSYS 安全復帰 | 存在しないシステムコール ID（例: `0xFF`） | `fireball_call(0xFF)` を実行 | システムが停止・パニックせず、WASI 準拠の `WasiErrno.NOSYS`（52）を返して安全に復帰する。**実装の勘所**: 未定義システムコールでホスト側が例外やアボートを発生させると、未サポート機能への問い合わせを行うゲストランタイムがクラッシュする | `system_syscall.md` §4.2 |
| SYS-GOTCHA-02 | `fb_offset_t` 境界チェックの完全先行（ホスト SEGV 防止） | ゲストメモリ終端を超えるオフセット | `fd_write` や `mmio_bulk_read` を実行 | ホスト側でのメモリアクセス前に `offset + len > mem_size` が評価され、`WasiErrno.FAULT`（21）で即座に拒絶される。**実装の勘所**: 整数オーバーフロー（`offset + len` が 32bit を超えて 0 付近にラップ）を考慮した境界判定式 `offset > mem_size or len > mem_size - offset` を使用しなければならない | `system_syscall.md` §4.1 |
| SYS-GOTCHA-03 | WASI iovec 散在ギャザー（Scatter-Gather）の全要素事前検証 | 一部要素が境界外を指す iovec 配列 | `fd_write` を実行 | 途中の正常要素も含めて 1 バイトも出力ストリームへ書き込まず、即座に `EFAULT` を返却する。**実装の勘所**: 検証しながら逐次出力すると、異常要素に到達した時点で途中までの中途半端なデータが出力先に漏洩・残存する | `system_syscall.md` §5.7 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- `fireball-call0`〜`fireball-call6`の各アリティ別バリアント自体の呼び出し規約差異。
- WASI errnoの完全な数値表（`wasi_snapshot_preview1`標準）とのすべての対応関係の網羅。
