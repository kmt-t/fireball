# WASI Preview 1 ABI 物理仕様書 (Supported WASI Preview 1 ABI) {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/wasi_lifecycle_model.py
-->

## 1. 概要と基本思想
<!-- traceability: {Type_Vocabulary} {TypeSafeMessaging} {Challenge_IpcQueueStarvation} {META_ZeroCostAbstraction} -->
本仕様書は、Fireball Hypervisor が WASM ゲストアプリケーションに対して提供する **WASI Preview 1 (`wasi_snapshot_preview1`)** インターフェイスの物理 ABI マッピング、サポート API セット、およびエラーコード規約を定義する正本である。

組込みマイコン環境（Cortex-M33, RAM 32KB〜64KB）における極小フットプリントを維持するため、ファイルシステムやネットワーク等の重厚な OS 機能は排除または制限し、文字入出力（UART）、ハードウェア時刻（SysTick/Timer）、乱数（TRNG）、および COOS スケジューラ連携に特化したスリムなサブセットを提供する。 `{Type_Vocabulary}` `{TypeSafeMessaging}` `{META_ZeroCostAbstraction}`

---

## 2. WASI 型定義と物理レイアウト (WASI Type Vocabulary)
<!-- traceability: {Type_Vocabulary} {Wasm32Only} -->

WASI 32-bit (wasm32) における物理データ型およびメモリレイアウト：

| WASI 型名 | C/C++ 物理型 | バイト幅 | 説明 |
| :--- | :--- | :--- | :--- |
| `__wasi_size_t` | `uint32_t` | 4 bytes | メモリサイズ・バイト長 |
| `__wasi_errno_t` | `uint16_t` | 2 bytes | 戻り値エラーコード (0 = `SUCCESS`) |
| `__wasi_fd_t` | `int32_t` | 4 bytes | ファイル記述子 (0=stdin, 1=stdout, 2=stderr) |
| `__wasi_clockid_t` | `uint32_t` | 4 bytes | クロック種別 (0=REALTIME, 1=MONOTONIC) |
| `__wasi_timestamp_t` | `uint64_t` | 8 bytes | ナノ秒単位のタイムスタンプ |
| `__wasi_ciovec_t` | `struct { uint32_t buf; uint32_t buf_len; }` | 8 bytes | 出力用バッファ記述子（Scatter/Gather I/O） |
| `__wasi_iovec_t` | `struct { uint32_t buf; uint32_t buf_len; }` | 8 bytes | 入力用バッファ記述子 |
| `__wasi_exitcode_t` | `uint32_t` | 4 bytes | プロセス終了コード |

---

## 3. WASI Preview 1 サポート API マトリクス

### 3.1 文字入出力 & ストリーム API (I/O & Streams)
<!-- traceability: {TypeSafeMessaging} {MemoryBoundaryCheck} -->

| API 名 | シグネチャ | 物理実装・ルーティング | 戻り値 / エラー |
| :--- | :--- | :--- | :--- |
| **`fd_write`** | `(fd: fd_t, iovs: ptr, iovs_len: size_t, nwritten: ptr) -> errno_t` | `fd=1/2`: UART HAL (`platform_hal`) または デバッグリングバッファへ文字出力。<br>`fd>=3`: IPC チャネル (`ipc_router`) へパケット送信。 | `SUCCESS` (0)<br>`EBADF` (不正なFD)<br>`EFAULT` (メモリ境界外) |
| **`fd_read`** | `(fd: fd_t, iovs: ptr, iovs_len: size_t, nread: ptr) -> errno_t` | `fd=0`: UART RX バッファから文字読み出し。<br>`fd>=3`: IPC チャネルからメッセージ受信。 | `SUCCESS` (0)<br>`EAGAIN` (データ未着)<br>`EBADF` |
| **`fd_close`** | `(fd: fd_t) -> errno_t` | `fd>=3` の IPC 接続チャネルをクローズ。`fd=0..2` のクローズは無視して成功。 | `SUCCESS` (0)<br>`EBADF` |
| **`fd_seek`** | `(fd: fd_t, offset: int64, whence: uint8, newoffset: ptr) -> errno_t` | ストリーム型デバイスのため非サポート。 | `ESPIPE` (パイプ/ストリームのためシーク不可) |
| **`fd_fdstat_get`**| `(fd: fd_t, stat: ptr) -> errno_t` | `fd=0..2` に対し `FILETYPE_CHARACTER_DEVICE` と `RIGHTS_FD_READ/WRITE` を返却。 | `SUCCESS` (0)<br>`EBADF` |
| **`fd_fdstat_set_flags`**| `(fd: fd_t, flags: uint16) -> errno_t` | ノンブロッキングフラグ（`FDFLAG_NONBLOCK`）の設定。 | `SUCCESS` (0) |

---

### 3.2 システム時刻 & クロック API (Clocks & Timers)
<!-- traceability: {GLOBAL_PeriodicTask} -->

| API 名 | シグネチャ | 物理実装・ルーティング | 戻り値 / エラー |
| :--- | :--- | :--- | :--- |
| **`clock_time_get`** | `(id: clockid_t, precision: timestamp_t, time: ptr) -> errno_t` | `CLOCKID_MONOTONIC`: SysTick / Hardware Timer HAL から起動後ナノ秒を取得。<br>`CLOCKID_REALTIME`: RTC HAL（またはエポック時刻）を取得。 | `SUCCESS` (0)<br>`EINVAL` (未定義クロックID) |
| **`clock_res_get`** | `(id: clockid_t, resolution: ptr) -> errno_t` | ハードウェアタイマーの分解能（例: 1000ns = 1µs）を返却。 | `SUCCESS` (0) |

---

### 3.3 プロセス制御 & 乱数 & スケジューラ API (Process, Random & Scheduler)
<!-- traceability: {ADR_RendezvousChannel} {CSP_Handoff} -->

| API 名 | シグネチャ | 物理実装・ルーティング | 戻り値 / エラー |
| :--- | :--- | :--- | :--- |
| **`proc_exit`** | `(rval: exitcode_t) -> void` | カレントタスクを `TERMINATED` 状態へ遷移させ、COOS スケジューラへ通知。タスクスタックを回収。 | 復帰しない（`noreturn`） |
| **`random_get`** | `(buf: ptr, buf_len: size_t) -> errno_t` | マイコン内蔵の TRNG (True Random Number Generator) HAL から物理乱数バイト列を充填。 | `SUCCESS` (0)<br>`EFAULT` |
| **`sched_yield`** | `() -> errno_t` | カレントタスクを一時中断し、READY キュー内の同優先度タスクへ制御を譲渡（COOS `yield` 呼出）。 | `SUCCESS` (0) |
| **`environ_get`** / **`environ_sizes_get`** | `(environ: ptr, environ_buf: ptr) -> errno_t` | 静的コンフィグ（`system_config_details`）で定義された環境変数テーブルを返却。 | `SUCCESS` (0) |
| **`args_get`** / **`args_sizes_get`** | `(argv: ptr, argv_buf: ptr) -> errno_t` | タスク起動時に渡された引数文字列テーブルを返却。 | `SUCCESS` (0) |

---

## 4. 非サポート API 一覧 (Explicit Non-Goals)
<!-- traceability: {GLOBAL_StrictMemoryLimit} -->

組込み Hypervisor のリソース制約により、以下のファイルシステム・ソケット関連 WASI API は**静的に除外**され、呼び出し時は即座に `__WASI_ERRNO_NOTSUP` (58) または `__WASI_ERRNO_BADF` を返す：
- **ファイルシステム操作**: `path_open`, `path_create_directory`, `path_unlink_file`, `path_readlink`, `path_rename`, `path_filestat_get`, `fd_readdir`, `fd_allocate`, `fd_datasync`, `fd_sync`
- **ソケット操作**: `sock_recv`, `sock_send`, `sock_shutdown`（※ タスク間通信はすべて Fireball IPC Router チャネルを使用する）
