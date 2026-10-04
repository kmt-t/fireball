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
<!-- traceability: {GOTCHA-SYS-01} -->

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
<!-- traceability: {VDMA} {OwnershipTransfer} -->

転送方向、全範囲検査、権限、物理alias、内部の完了方式は、[`runtime_vsoc_test_spec.md`](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md)のvDMA設計へ対応付ける。
本節は専用importの結果契約と、各実行が通る入口を明示する。

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-20 | `fireball:host/vdma.start` 成功 | `source`/`destination`が共に許可アドレス | 実guestの専用importでL/D/S/Pの全16方向へ転送する。内部memory.copyの16方向と独立に実行する | 専用importは`0`を返し、memory.copyはtrapなく完了する。guestの直後loadと実バック領域が転送値を返す。転送範囲外を保存する。既存syscall試験でVDMA制御ページの未登録も確認する | runtime_syscall.md, runtime_vmmio.md, `test_syscall_04_vdma_host_call_transfer`, `test_vdma.py`, TEST-VSOC-67 |
| TEST-SYS-21 | 非所有者によるSHMのvDMA転送拒否 | 所有者Aの実SHMをマップする。TLB未充填とAのアクセス後の2条件を使う | Bの実タスクコンテキストで、guestの専用importから元と先を別々にSHMへ指定する。その後Aから同じ範囲へ転送する。既存の直接ハンドラ試験も保持する | Bの呼出しは非0のWASI errnoを返す。SHM実体、物理バック領域、ゲストRAM、所有者とマッピング範囲は不変である。Aの転送は指定byte列を書き、隣接範囲を保存する。拒否時の個別errno値は本契約で規定しない | runtime_vmmio.md {OwnershipTransfer}, `test_non_owner_rejection_preserves_storage_and_owner_access`, `test_syscall_21_vdma_rejects_non_owner_shm_without_mutation`, TEST-VSOC-68 |
| TEST-SYS-22 | 非同期vDMAの内部完了通知 | 専用host callの転送対象と操作が非同期完了を伴う | 転送の保留中と完了通知後の状態を確認する | 保留中はゲストへ成功復帰せず、既存COOS完了通知で再開した後に完了とCPU可視性を確認する | runtime_vsoc.md、TEST-VSOC-62 |

### 専用 vIRQ host call
<!-- traceability: {GLOBAL_InterruptWakeup} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-30 | 専用import経由のvIRQ登録保留 | 静的ノードと適合する2関数が存在する | `virq_register`を解決する。初回登録と既存登録の置換について境界commit前後を比較する | `SUCCESS`を返し、保留表だけを更新する。commit前は有効表が不変である。commit後は指定関数だけが有効になり、他ノードは不変である | runtime_syscall.md, runtime_vsoc.md, `test_syscall_05_virq_registration_host_calls` |
| TEST-SYS-31 | 専用import経由のvIRQ解除保留 | 有効な登録が存在する | `virq_unregister`を解決して解除する。境界commit前後を比較する | `SUCCESS`を返し、保留表だけから登録を除去する。commit前は有効表が不変である。commit後は対象ノードが無効になり、他ノードは不変である | runtime_syscall.md, runtime_vsoc.md, `test_syscall_31_virq_unregister_is_deferred_until_commit` |
| TEST-SYS-32 | 不正vIRQ要求で有効・保留登録を保存 | 有効登録Aと保留登録Bがある。範囲外ノード、存在しない関数、不適合シグネチャを使う | 専用importから登録または解除を試す。その後commitする | `INVAL`を返し、有効表、保留表、静的原因源表を変更しない。commit後は正常な保留登録Bが有効になる | runtime_syscall.md, runtime_vsoc.md, `test_syscall_32_invalid_virq_registration_preserves_active_and_pending`, `test_syscall_32_invalid_virq_unregister_preserves_active_and_pending` |

### IPC (`0x40`-`0x4F`)
<!-- traceability: {IPC_HandleBased} {GOTCHA-SYS-02} -->

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
| TEST-SYS-50 | `IPC_RECV`出力領域重複 | 送信者が待機済みである。4バイト受信長領域がデータ領域の直前から重なる、先頭で重なる、内部で重なる、末尾1バイトで重なる4条件を使う | 重複領域で`fireball_call(0x41,...)`を試す。その後、非重複領域で受信と応答を行う | 拒否は待受け前に`INVAL`を返す。ゲストRAM、受信側の状態、応答待ちメッセージ、送信側の待機と所有権は不変である。再受信で元の内容を取得でき、応答後にだけ送信側が完了する | runtime_syscall.md (IPC), `test_syscall_50_overlapping_recv_outputs_reject_without_consuming_sender` |
| TEST-SYS-51 | `IPC_RECV`バッファ容量不足 | データ出力容量が最大ペイロード28バイト未満 | 相手を待たせず`fireball_call(0x41, handle_id, buf_offset, buf_len, recv_len_out_ptr,...)` | 待受け前に`MSGSIZE`を返し、受信メッセージを消費しない | runtime_syscall.md (IPC) |

### WASI (`0x80`-`0xBF`)

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-80 | `WASI_FD_WRITE` | fd=1（stdout）、iovecが1件 | `fireball_call(0x80, fd, iovs_ptr, iovs_len, nwritten_ptr,...)` | `fireball://hal/stdout/0`宛の`CMD_STREAM_WRITE_BUFFER`相当が呼ばれ、`nwritten_ptr`に書き込みバイト数が入り、`0`(errno)を返す | interface_wit.md |
| TEST-SYS-81 | `WASI_FD_READ` | stdinの読出し位置が終端で、出力領域には非0の初期値がある | `fireball_call(0x81,...)` | errno `0`を返して受信長に0を書き、データ領域とstdinの内容・位置を保存する | runtime_syscall.md (WASI) |
| TEST-SYS-82 | `WASI_FD_CLOSE` | 開いたfdと、保存する別fdがある | `fireball_call(0x82, fd,...)`で閉じ、同じfdを再度閉じる | 初回は`0`を返して対象だけを表から除去し、再度のcloseは`BADF`を返す。別fdは不変である | runtime_syscall.md (WASI), `test_wasi_03_fd_close` |
| TEST-SYS-83 | `WASI_CLOCK_TIME_GET` | 単調時計の`clock_id=1`を指定する | `fireball_call(0x83, 1, precision, time_ptr,...)` | `time_ptr`に64bitの単調非減少ナノ秒値を書き、指定範囲外を保存する。参照環境では呼出前後の単調時計値の間に入る | `hal_dispatch.md` 階層型 URI 命名規則 & WASI 0.3p IPC コマンド仕様、`test_wasi_04_clock_time_get_monotonic` |
| TEST-SYS-84 | `WASI_PROC_EXIT` | - | `fireball_call(0x84, exit_code,...)` | プロセス終了相当の状態変化（戻り値なし） | runtime_syscall.md (WASI) |
| TEST-SYS-85 | `WASI_RANDOM_GET` | - | `fireball_call(0x85, buf_ptr, buf_len,...)` | `buf_ptr`に`buf_len`バイトのランダムデータが書き込まれ、`0`を返す | runtime_syscall.md (WASI) |

### 共通・エラー処理

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-SYS-90 | 未定義ID | 予約済み範囲・未割当ID | `fireball_call(未定義ID,...)` | 定義されたエラーコード（WASI `errno_t`準拠、実装は`ENOSYS`相当）を返す | `syscall_concept.py` `test_reserved_irq_and_unknown_id_return_nosys` |
| TEST-SYS-91 | 戻り値は常にWASI `errno_t`準拠 | 任意の失敗ケース | 各失敗パスの戻り値を確認 | プロジェクト独自の非標準エラーコードを使わない | 「WASIの`errno_t`に準拠」 |
| TEST-SYS-92 | `fb_offset_t`のゲスト境界チェック | offset引数がゲストメモリ範囲外 | 該当syscallを呼ぶ | 即座に境界外エラーを返す（加算オーバーフローを起こさない減算形式で判定） | `syscall_concept.py` `test_guest_range_validation_precedes_access` |
| TEST-SYS-93 | raw returnにデータ値を混在させない | MMIO/IPC成功データがWASI errnoの有効値と一致する | `fireball_call`を呼び出し、戻り値と出力領域を確認する | raw returnは`SUCCESS`、データ値は指定出力領域から取得する | runtime_syscall.md |
| TEST-SYS-94 | MMIOアクセス幅がSHMのマッピング範囲を越えない | `mapping_size=4`のSHMページと、開始アドレスが範囲内の32bit操作 | 先頭の32bit操作と1バイト進めた32bit書き込みを行う | 先頭は指定バイト列を書き、後者は`FAULT`となる。マッピング外のsentinelを含む全物理バック領域を保存する | `test_syscall.py` `test_syscall_16_mmio_access_width_stays_inside_shm_mapping` |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-SYS-01} {GOTCHA-SYS-02} {GOTCHA-SYS-03} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-SYS-01 | 未定義 Syscall ID の非パニック・ENOSYS 安全復帰 | 存在しないシステムコール ID（例: `0xFF`） | `fireball_call(0xFF)` を実行 | システムが停止・パニックせず、WASI 準拠の `WasiErrno.NOSYS`（52）を返して安全に復帰する。 | [`runtime_syscall.md`](docs/components/tier2_runtime/runtime_syscall.md) |
| GOTCHA-SYS-02 | `fb_offset_t`境界違反の副作用前拒否 | ゲストメモリ終端を超える範囲と32bit加算overflow相当の入力がある | `fd_write`や`mmio_bulk_read`を実行する | `WasiErrno.FAULT`（21）で拒否され、ホスト側の対象メモリアクセスと出力先の変更が発生しない | [`runtime_syscall.md`](docs/components/tier2_runtime/runtime_syscall.md) |
| GOTCHA-SYS-03 | WASI iovec 散在ギャザー（Scatter-Gather）の全要素事前検証 | 一部要素が境界外を指す iovec 配列 | `fd_write` を実行 | 途中の正常要素も含めて 1 バイトも出力ストリームへ書き込まず、即座に `EFAULT` を返却する。 | [`runtime_syscall.md`](docs/components/tier2_runtime/runtime_syscall.md) |

### DYNAMIC実体の境界

`test_vdma_dynamic_bounds_rejection_preserves_real_buffer`は256byteのHAL実バッファを越える3条件を拒否する。
4KBのPTEページ内という条件だけで転送を許可しない。
拒否時はguest memory、HAL buffer、PTEとownerを保存する。

## 3. テスト検証実績と網羅状況
<!-- traceability: {GOTCHA-SYS-03} -->

仕様表の定義数と実行済み件数を区別する。ケースIDの範囲をdocstringに記しただけでは、その範囲を検証済みとしない。

| 対象契約 | 実行ケース | 独立した期待値と観測状態 |
| :--- | :--- | :--- |
| TEST-SYS-10、13、93 | 32bit・8bitのデータ搬送 | write/readの往復比較に加え、既知のlittle-endian物理バイト列と8bit書込み周辺のsentinelを検査する。read8の出力は4byteの値だけを更新する |
| TEST-SYS-11、12、14、94 | 範囲外・権限拒否とSHM全幅境界 | errnoだけでなく、ゲストメモリと全物理バック領域の保存を比較する。SHM境界は範囲外となる5バイト目も保存対象とする |
| TEST-SYS-21 | TLB未充填・所有者アクセス後の2件 | 非所有者は拒否される。転送前の全物理バック領域とゲストRAMを保存比較する。所有者の再転送内容は入力バイト列から導く |
| TEST-SYS-20 | `test_vdma.py`の専用import正常16方向 | 実NativeInterpreterとCOOSから実HAL・SHM・PASSTHROUGH・guest RAMを使う。戻り値0、直後guest load、転送範囲と範囲外を独立snapshotで比較する |
| TEST-SYS-21 | `test_vdma.py`の実SHM非所有者拒否4条件 | 専用importを実guestから呼ぶ。転送元と先、TLB未充填と充填後の全4条件で非0を返し、実SHMを含む全領域とPTEを保存する。所有者からの再転送は成功する |
| TEST-SYS-30、31 | 登録置換・解除の各1件 | 有効表はcommit前に変化しない。指定した関数番号と未登録値をcommit後に確認する |
| TEST-SYS-32 | 不正登録4件・不正解除2件 | 正常な有効登録と保留登録を保存比較する。拒否後のcommitは正常な保留登録を反映する |
| TEST-SYS-50 | 出力領域の重複4件 | 拒否後のメモリとタスク状態を保存比較する。再受信内容は送信元の固定ペイロードから導く |
| TEST-SYS-81、82 | EOFとclose | EOFは非0の受信長を0へ更新し、全メモリとstdin状態を保存する。closeは対象fdの除去と別fdの保存を直接検査する |
| TEST-SYS-83、85 | 単調時計と乱数データの搬送 | 単調時計は呼出前後の参照値で64bit値を囲む。乱数は既存参照バックエンドのentropy入力を既知16バイトに固定し、要求長・全バイト・範囲外の保存を照合する |
| GOTCHA-SYS-03 | 後半iovecの境界違反 | [`runtime_syscall.md`](docs/components/tier2_runtime/runtime_syscall.md)、実stdout HALを結線する。拒否後に全メモリ、出力なし、HAL処理件数0を照合する |

2026-10-02にLinux x86-64・Python 3.14.6で[`test_syscall.py`](experiments/pysim/qa/tier2_runtime/test_syscall.py)を実行した。

```bash
.venv/bin/python -m pytest -q experiments/pysim/qa/tier2_runtime/test_syscall.py
```

結果は成功52件、失敗0件、skip 0件である。所要時間は0.33秒である。
この結果は本書の全契約や実機動作の網羅を意味しない。vDMAのguest結線と外部サービス境界は [`runtime_vsoc_test_spec.md`](docs/qa/tier2_runtime/runtime_vsoc_test_spec.md) で検証する。

## 4. 未検証・スコープ外

- WIT生成バインディングが生成する言語別ラッパーのABI詳細。
- WASI errnoの完全な数値表（`wasi_snapshot_preview1`標準）とのすべての対応関係の網羅。
- TEST-SYS-85は乱数バックエンドからゲストへのデータ搬送を検査する。乱数源の暗号学的品質は本テストで検証しない。
- TEST-SYS-22は外部サービスモックと実guest・Gateway・COOSの境界で検査した。モックが持つ待機アダプタを製品の非同期driver結線の証拠にしない。実装された下位サービスの待機・通知処理も、デバイス境界をモックで置換して検査できる。
- `IPC_LOOKUP`の未登録URIでの出力・ハンドル表不変（TEST-SYS-41）、および相手が未到達の`IPC_RECV`の待機（TEST-SYS-45）。今回の実行対象には独立したケースがない。
- 専用vIRQの登録保留と有効表の公開は直接検査する。実割り込みとCOOS境界の配送結線は `runtime_vsoc_test_spec.md` 側の責務とする。
