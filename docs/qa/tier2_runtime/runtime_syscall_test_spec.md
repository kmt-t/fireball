# システムコール (`fireball_call`) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_syscall.md`](docs/components/tier2_runtime/runtime_syscall.md)
関連正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)（vMMIOアドレス空間と対象アドレスの保護）、[`system_config.md`](docs/components/tier1_core/system_config.md)（アドレス定数）
参考実装: [`syscall_concept.py`](docs/components/tier2_runtime/concepts/syscall_concept.py)

`fireball_call(id, arg0..arg5) -> u32` の host-call ID 空間（System/vMMIO Generic/IPC/WASI）と、戻り値は常にWASI `errno_t`、データは出力ポインタまたはデータ領域で渡すABI規約を検証する。vIRQ/vDMAは専用host callとして別契約で検証する。host call の搬送に SYSCTL／VDMA の vMMIO レジスタを使用しないことも検証する。

## 2. テストケース一覧

### System (`0x00`-`0x0F`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-01 | `SYS_YIELD`(0x01) | ゲスト関数が後続処理を持ち、別タスクがREADYである | ゲストから `fireball_call(0x01, ...)` を呼ぶ | `0`を返し、現在のRuntime境界でゲストをREADYへ戻して別タスクを実行し、再開後に後続処理を完了する | `runtime_syscall.md` §5.3.2, `test_syscall_04_guest_yield_hands_off_to_ready_task` |
| TEST-SYS-02 | `SYS_HALT`(0x02) | - | `fireball_call(0x02, ...)` | システム停止状態になる（戻り値は規定なし） | runtime_syscall.md (Lifecycle) |
| TEST-SYS-03 | `SYS_RESET`(0x03) | - | `fireball_call(0x03, ...)` | `0`を返し、ゲストリセット相当の状態変化が起こる | runtime_syscall.md (Lifecycle) |

### vMMIO Generic (`0x10`-`0x1F`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-10 | `MMIO_READ32`成功とerrno値との衝突回避 | 許可アドレスに値`WasiErrno.FAULT`が存在し、有効な`value_out_ptr`がある | `fireball_call(0x10, addr, value_out_ptr, ...)` | raw returnは`SUCCESS`で、`value_out_ptr`から値`21`を読める | runtime_syscall.md (MMIO), `test_syscall_03_mmio_read_write` |
| TEST-SYS-11 | `MMIO_READ32`境界外または出力範囲外 | MMIOアドレスまたは4バイトの`value_out_ptr`が範囲外 | 同上 | `FAULT`相当を返し、出力範囲外には書き込まない | `{META_RestrictedPhysicalAccess}`, `test_syscall_11_mmio_read32_out_of_bounds`, `test_syscall_03_mmio_read_write` |
| TEST-SYS-12 | `MMIO_WRITE32`成功/権限拒否 | 書き込み許可/不許可の2ケース | `fireball_call(0x11, addr, value,...)` | 許可時`0`、不許可時`ERR_ACCESS_DENIED`相当 | runtime_syscall.md (MMIO), `test_syscall_03_mmio_read_write`(成功), `test_syscall_12_mmio_write32_permission_denied`(拒否) |
| TEST-SYS-13 | `MMIO_READ8`/`MMIO_WRITE8` | 同上をバイト単位で | 同様の手順 | 同様の結果（幅8bit） | runtime_syscall.md (MMIO), `test_syscall_13_mmio_read8_write8` |
| TEST-SYS-14 | `MMIO_BULK_READ`/`WRITE`のサイズ不正 | `byte_count`が不正（範囲外・0等） | 呼び出す | `ERR_INVALID_SIZE`相当を返す | runtime_syscall.md (MMIO), `test_syscall_14_mmio_bulk_read_write_invalid_size` |
| TEST-SYS-15 | `MMIO_BULK_READ`のゲスト書き込み先境界チェック | `dest_offset`がゲストメモリ範囲外 | 呼び出す | `ERR_OUT_OF_BOUNDS`相当を返し、ゲストメモリ外への書き込みが発生しない | fb_offset_t, `test_syscall_15_mmio_bulk_read_dest_offset_out_of_bounds` |
| TEST-SYS-16 | `TRIGGER_SET_PIN`(0x16)のpysim実験実装での安全なNOSYS復帰 | - | `fireball_call(0x16, pin, value, ...)` | 専用GPIOレジスタ配線が未実装のためディスパッチテーブル未登録であり、`GOTCHA-SYS-01`の規定通り`WasiErrno.NOSYS`(52)を安全に返す（クラッシュ・パニックしない） | runtime_syscall.md (MMIO), GOTCHA-SYS-01 |

### 専用 vDMA host call

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-20 | `fireball:host/vdma.start` 成功 | `source`/`destination`が共に許可アドレス | `fireball_vdma_start(source, destination, byte_count)` | `0`を返し、`byte_count`バイトが転送される。VDMAレジスタへのアクセスは発生しない | runtime_syscall.md, runtime_vmmio.md |
| TEST-SYS-21 | vDMA専用host callの転送先がSHM(FC=14)の場合の所有権チェック | `destination`がSHMアドレスで、呼び出し元が非所有者 | `fireball:host/vdma.start`を呼ぶ | 転送要求は専用host callで受け、転送先の共通vMMIO権限ゲートにより拒否される | runtime_vmmio.md |
| TEST-SYS-22 | vDMA完了時の仮想割り込み通知（該当する場合） | 完了通知が要求されている | 転送完了後の状態を確認 | `IRQ_VDMA_DONE`相当が立つ | runtime_vmmio.md  |

### 専用 vIRQ host call

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-30 | `fireball:host/virq.register` host call | 静的ノードと期待シグネチャの関数が存在 | `fireball_virq_register(node_id, function_index)` | `0`を返して登録を保留し、ゲストからvMMIO固定スロットへ直接書き込まない | runtime_syscall.md, runtime_vsoc.md |
| TEST-SYS-31 | `fireball:host/virq.unregister` host call | 有効または保留中の登録が存在 | `fireball_virq_unregister(node_id)` | `0`を返して解除を保留し、次のCOOS協調境界で無効化する | runtime_syscall.md, runtime_vsoc.md |
| TEST-SYS-32 | 不正vIRQ登録要求 | 範囲外ノード、無効関数、または専用host callの不正引数 | 各専用host callを呼び出す | `WasiErrno`相当のエラーを返し、有効登録・`REG_IRQ_FLAGS`を変更しない | runtime_syscall.md, GOTCHA-SYS-01 |

### IPC (`0x40`-`0x4F`)
<!-- traceability: {IPC_HandleBased} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-40 | `IPC_LOOKUP`成功 | URIが登録済み、有効な4バイト`handle_out_ptr`がある | `fireball_call(0x42, uri_offset, uri_len, handle_out_ptr,...)` | raw returnは`SUCCESS`で、出力領域から正の`handle_id`を読める | `IPC_HandleBased` |
| TEST-SYS-41 | `IPC_LOOKUP`未登録URI | URI未登録 | 同上 | `NOENT`を返し、出力値やハンドル表を変更しない | runtime_syscall.md (IPC) |
| TEST-SYS-42 | `IPC_SEND`成功 | 有効なhandle_id | `fireball_call(0x40, handle_id, msg_offset, msg_len,...)` | 受信側が既に待機していれば即座に、まだ到達していなければ呼び出し元タスクのコルーチンが協調スケジューラ上でブロックし、受信側到達後に`0`を返す（キューは存在しないため、待機はブロックのみで失敗経路はない） | ipc_router.md |
| TEST-SYS-43 | `IPC_SEND`宛先未登録／RBAC拒否／サイズ超過 | 未登録URIから得たhandle_id、許可されないロール、9個以上のkv_pair、または29バイト以上のゲストペイロード | 同上 | 未登録・権限拒否・KV上限超過を対応するerrnoへ変換する。29バイト以上は共有メッセージ構築前に `EMSGSIZE` を返し、所有権移譲やチャネル状態変更を起こさない | ipc_router.md, runtime_syscall.md (IPC) |
| TEST-SYS-44 | `IPC_RECV`成功 | 呼び出し元ロールに受信許可された送信側が到達し、出力範囲が重ならない | `fireball_call(0x41, handle_id, buf_offset, buf_len, recv_len_out_ptr,...)` (`handle_id`は未使用) | raw returnは`SUCCESS`で、データが`buf_offset`に、`recv_len`が出力ポインタに書かれる | runtime_syscall.md (IPC) |
| TEST-SYS-45 | `IPC_RECV`相手未到達 | 送信側がまだ到達していない | 同上 | `fireball_call`の呼び出し元タスクのコルーチンが協調スケジューラ上でブロックし、送信側が到達するまで再開しない（EAGAINのような即時errnoは返さない。ブロックがCSPランデブーの本来の意味論であり、実装依存の妥協ではない） | 「バッファが空の場合はコルーチンがサスペンドされる」, [`system.py`](experiments/pysim/system.py) `_ipc_recv` |
| TEST-SYS-46 | `IPC_RECV`出力範囲不正 | データ出力または4バイト受信長出力が現在のゲストメモリ範囲を超える | 相手を待機させずに`fireball_call(0x41, handle_id, buf_offset, buf_len, recv_len_out_ptr,...)` | 待受け開始前に`EFAULT`を返し、タスクをブロックせず送信メッセージも消費しない | runtime_syscall.md (IPC), GOTCHA-SYS-02 |
| TEST-SYS-47 | `IPC_LOOKUP` RBAC拒否 | 登録URIへの送信が呼び出し元ロールで許可されない | `fireball_call(0x42, uri_offset, uri_len, handle_out_ptr,...)` | `PERM`を返す。URI未登録の`NOENT`へ誤変換しない | runtime_syscall.md (IPC), ipc_router.md |
| TEST-SYS-48 | `IPC_RECV` RBAC拒否 | 受信可能な送信元エッジを持たないロール | 有効なデータ出力領域、受信長出力領域を指定して `fireball_call(0x41,...)` | ブロックせず `PERM` を返し、`NOENT` へ誤変換しない | runtime_syscall.md (IPC), ipc_router.md |
| TEST-SYS-49 | `IPC_SEND`応答コード出力範囲の事前検査 | `response_code_ptr` の4バイト範囲がゲストメモリ外 | 相手を待たせず `fireball_call(0x40,...)` | CSP送信を開始する前に `EFAULT` を返し、相手側の処理・メッセージ所有権に影響しない | runtime_syscall.md (IPC), GOTCHA-SYS-02 |
| TEST-SYS-50 | `IPC_RECV`出力領域重複 | データ領域と4バイト受信長領域が重なる | 相手を待たせず`fireball_call(0x41, handle_id, buf_offset, buf_len, recv_len_out_ptr,...)` | 待受け前に`INVAL`を返し、送信メッセージを消費しない | runtime_syscall.md (IPC) |
| TEST-SYS-51 | `IPC_RECV`バッファ容量不足 | データ出力容量が最大ペイロード28バイト未満 | 相手を待たせず`fireball_call(0x41, handle_id, buf_offset, buf_len, recv_len_out_ptr,...)` | 待受け前に`MSGSIZE`を返し、受信メッセージを消費しない | runtime_syscall.md (IPC) |

### WASI (`0x80`-`0xBF`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-80 | `WASI_FD_WRITE` | fd=1（stdout）、iovecが1件 | `fireball_call(0x80, fd, iovs_ptr, iovs_len, nwritten_ptr,...)` | `fireball://hal/stdout/0`宛の`CMD_STREAM_WRITE_BUFFER`相当が呼ばれ、`nwritten_ptr`に書き込みバイト数が入り、`0`(errno)を返す | interface_wit.md |
| TEST-SYS-81 | `WASI_FD_READ` | 実stdin相当のデータなし | `fireball_call(0x81,...)` | 0バイト読み取り(EOF)としてerrno `0`を返す | runtime_syscall.md (WASI) |
| TEST-SYS-82 | `WASI_FD_CLOSE` | 任意のfd | `fireball_call(0x82, fd,...)` | `0`を返す | runtime_syscall.md (WASI) |
| TEST-SYS-83 | `WASI_CLOCK_TIME_GET` | - | `fireball_call(0x83, clock_id, precision, time_ptr,...)` | `time_ptr`に単調増加するナノ秒値が書き込まれる | `hal_dispatch.md` 階層型 URI 命名規則 & WASI 0.3p IPC コマンド仕様 |
| TEST-SYS-84 | `WASI_PROC_EXIT` | - | `fireball_call(0x84, exit_code,...)` | プロセス終了相当の状態変化（戻り値なし） | runtime_syscall.md (WASI) |
| TEST-SYS-85 | `WASI_RANDOM_GET` | - | `fireball_call(0x85, buf_ptr, buf_len,...)` | `buf_ptr`に`buf_len`バイトのランダムデータが書き込まれ、`0`を返す | runtime_syscall.md (WASI) |

### 共通・エラー処理

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-90 | 未定義ID | 予約済み範囲・未割当ID | `fireball_call(未定義ID,...)` | 定義されたエラーコード（WASI `errno_t`準拠、実装は`ENOSYS`相当）を返す | `syscall_concept.py` `test_reserved_irq_and_unknown_id_return_nosys` |
| TEST-SYS-91 | 戻り値は常にWASI `errno_t`準拠 | 任意の失敗ケース | 各失敗パスの戻り値を確認 | プロジェクト独自の非標準エラーコードを使わない | 「WASIの`errno_t`に準拠」 |
| TEST-SYS-92 | `fb_offset_t`のゲスト境界チェック | offset引数がゲストメモリ範囲外 | 該当syscallを呼ぶ | 即座に境界外エラーを返す（加算オーバーフローを起こさない減算形式で判定） | `syscall_concept.py` `test_guest_range_validation_precedes_access` |
| TEST-SYS-93 | raw returnにデータ値を混在させない | MMIO/IPC成功データがWASI errnoの有効値と一致する | `fireball_call`を呼び出し、戻り値と出力領域を確認する | raw returnは`SUCCESS`、データ値は指定出力領域から取得する | runtime_syscall.md |
| TEST-SYS-94 | MMIOアクセス幅がSHMのマッピング範囲を越えない | `mapping_size=4`のSHMページと、開始アドレスが範囲内の32bit操作 | 先頭の32bit操作と1バイト進めた32bit書き込みを行う | 先頭は成功し、後者は`FAULT`となりSHM backingを変更しない | `test_syscall.py` `test_syscall_16_mmio_access_width_stays_inside_shm_mapping` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-SYS-01 | 未定義 Syscall ID の非パニック・ENOSYS 安全復帰 | 存在しないシステムコール ID（例: `0xFF`） | `fireball_call(0xFF)` を実行 | システムが停止・パニックせず、WASI 準拠の `WasiErrno.NOSYS`（52）を返して安全に復帰する。**実装の勘所**: 未定義システムコールでホスト側が例外やアボートを発生させると、未サポート機能への問い合わせを行うゲストランタイムがクラッシュする | `runtime_syscall.md` |
| GOTCHA-SYS-02 | `fb_offset_t` 境界チェックの完全先行（ホスト SEGV 防止） | ゲストメモリ終端を超えるオフセット | `fd_write` や `mmio_bulk_read` を実行 | ホスト側でのメモリアクセス前に `offset + len > mem_size` が評価され、`WasiErrno.FAULT`（21）で即座に拒絶される。**実装の勘所**: 整数オーバーフロー（`offset + len` が 32bit を超えて 0 付近にラップ）を考慮した境界判定式 `offset > mem_size or len > mem_size - offset` を使用しなければならない | `runtime_syscall.md` |
| GOTCHA-SYS-03 | WASI iovec 散在ギャザー（Scatter-Gather）の全要素事前検証 | 一部要素が境界外を指す iovec 配列 | `fd_write` を実行 | 途中の正常要素も含めて 1 バイトも出力ストリームへ書き込まず、即座に `EFAULT` を返却する。**実装の勘所**: 検証しながら逐次出力すると、異常要素に到達した時点で途中までの中途半端なデータが出力先に漏洩・残存する | `runtime_syscall.md` |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- WIT生成バインディングが生成する言語別ラッパーのABI詳細。
- WASI errnoの完全な数値表（`wasi_snapshot_preview1`標準）とのすべての対応関係の網羅。
