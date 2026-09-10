# システムコール仕様 コンポーネント設計書 {VERIFY_FORMAL}
<!-- evidence:
     formal: formal/syscall_trap_model.py
     test: tests/runtime_syscall_test_spec.md
-->

## 1. 目的
<!-- traceability: {NativeAPI_Export} -->
本ドキュメントは、WebAssemblyゲスト環境からホストの提供する機能を呼び出すための汎用システムコール `fireball_call` のホスト側インターフェース仕様を定義する。WASI呼び出しをこのABIへ接続するゲスト側アダプタは、Tier 3 の `libfireball` が担う。 `{NativeAPI_Export}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} -->
本コンポーネントは **Tier 2 (分解されたサブコンポーネント: Decomposed Subcomponent)** に属する。`fireball_call` はゲスト（WASM）からのみ呼び出される、ゲストをホストする vSoC ランタイムの機能であり、Tier 1 のコアコンポーネント（COOS、IPC ルータ等）が依存する汎用プリミティブではない。 `{META_3TierSeparation}`

## 3. 背景
<!-- traceability: {UnifiedAccessModel} -->
`fireball_call` は、vMMIOアドレス空間（[`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) の Stage 2/3、Bit 31 == 1）に対する**代理実行ラッパー**である。直接vMMIOアドレスにアクセスできないゲスト言語のために、シングル・トラップ命令経由でホストがvMMIO操作を代行する。ゲスト専用RAM（Stage 1, Bit 31 == 0）はこの対象外であり、`FastAddressCheck` による別経路の境界チェックのみで完結する。

```
アクセスパスA: guest load/store(vMMIO_addr) → PTEマッピング解決 (未登録時 TRAP) → 直接物理アクセス
アクセスパスB: guest fireball_call(id, args) → host代理 → vMMIO → PTEマッピング解決 → 直接物理アクセス
```

vMMIOアドレス空間（Stage 2/3）に対しては、どちらのパスも最終的に統一された vMMIO ページマッピング機構（PTE / TLB）を通る。アクセス権限のない領域（他タスク所有の共有メモリや未割当領域）は仮想アドレス空間から物理的に **unmap（マッピング解除）** されており、PTE 不在として未登録ページトラップ（`TRAP_UNREGISTERED_PAGE`）により即座に遮断される。セキュリティ境界は vMMIO のマッピング存在性により 1 箇所に統一される（ゲストRAMのFastAddressCheckとは独立した別ゲート）。 `{UnifiedAccessModel}`

## 4. `fireball_call` WIT定義
<!-- traceability: {WIT_Interface_Spec} -->
`fireball_call`のWIT (WebAssembly Interface Type) 定義は以下の通りである。詳細は [`interface_wit.md`](docs/components/tier1_interface/interface_wit.md) を参照のこと。 `{WIT_Interface_Spec}`

```wit
package fireball:host;

interface trap {
  /// Performs a low-level host call with raw arguments.
  /// Variants for optimization based on argument count.
  fireball-call0: func(id: u32) -> u32;
  fireball-call1: func(id: u32, a0: u32) -> u32;
  fireball-call2: func(id: u32, a0: u32, a1: u32) -> u32;
  fireball-call3: func(id: u32, a0: u32, a1: u32, a2: u32) -> u32;
  fireball-call4: func(id: u32, a0: u32, a1: u32, a2: u32, a3: u32) -> u32;
  fireball-call5: func(id: u32, a0: u32, a1: u32, a2: u32, a3: u32, a4: u32) -> u32;
  fireball-call6: func(id: u32, a0: u32, a1: u32, a2: u32, a3: u32, a4: u32, a5: u32) -> u32;
}

world fireball {
  import trap;
  // 高レベルのWASI/HAL操作は、Tier 2 HALの公開抽象IFへ接続される。
}
```

##### トラップ高速パスとレジスタ直接マッピング
<!-- traceability: {Trap_Interface} -->
`fireball_call` は、実行環境のJIT/Interpreterが提供するインポート関数呼び出しをインターセプトし、ホスト側の仮想レジスタ `REG_SYSCALL_*` に引数を直接複写（レジスタマッピング）することで、トラップ（`ecall` / `svc` 等）の処理オーバーヘッドを極限まで削減する高速パスを提供する。 `{Trap_Interface}`

## 5. `fireball_call` 呼び出し規約

### 5.1. 引数のパッキング
<!-- traceability: {Type_Vocabulary} -->

`fireball_call` は、後述の「型のエイリアス定義」で定義された型エイリアス（`fb_id_t`, `fb_val_t`, `fb_offset_t`）および規定の語彙セットに従って引数をパッキングする。物理的にはシステムコールID（`id`）と、6つの汎用引数（`arg0`〜`arg5`）の合計7つの `u32` 表現で構成され、インターフェースの型安全性を担保する。WASI関数がこれらの引数よりも多くのパラメータを持つ場合、ゲストメモリの物理ベースアドレスからの相対オフセット（`fb_offset_t`）を渡す。絶対アドレスではなく相対オフセットに制限することで、ゲスト境界チェックを瞬時に行う。

##### 型のエイリアス定義 (Type Vocabulary) `{Type_Vocabulary}`
本インターフェースで受け渡される引数はすべて物理的には `u32` であるが、その意味論的な解釈を定義するため、以下の型語彙（エイリアス）を使用する。
* **`fb_id_t`**: システムコールIDまたはリソースIDを表す `u32`。
* **`fb_val_t`**: 即値のレジスタ値または即値パラメータを表す `u32`。
* **`fb_offset_t`**: ゲストメモリの物理ベースアドレスからの相対オフセット（バイト単位）を表す `u32`。

| 引数名 | 型   | 説明                                            |
| :----- | :--- | :---------------------------------------------- |
| `id`   | `fb_id_t` | システムコールID (`FB_SYSCALL_*` で定義)       |
| `arg0` | `fb_offset_t \| fb_val_t` | 汎用引数0、またはゲストメモリ内構造体の相対オフセット |
| `arg1` | `fb_offset_t \| fb_val_t` | 汎用引数1、またはゲストメモリ内構造体の相対オフセット |
| `arg2` | `fb_offset_t \| fb_val_t` | 汎用引数2、またはゲストメモリ内構造体の相対オフセット |
| `arg3` | `fb_offset_t \| fb_val_t` | 汎用引数3、またはゲストメモリ内構造体の相対オフセット |
| `arg4` | `fb_offset_t \| fb_val_t` | 汎用引数4、またはゲストメモリ内構造体の相対オフセット |
| `arg5` | `fb_offset_t \| fb_val_t` | 汎用引数5、またはゲストメモリ内構造体の相対オフセット |

#### 5.1.1. ゲストメモリ内構造体のレイアウト規則
`arg0`〜`arg5` にゲストメモリ上のポインタ（`iovs_ptr` 等）を渡す場合、データ構造は以下の制約に従って配置されなければならない。

* **アライメント**: すべての構造体およびそのメンバは **4バイトアライメント** に配置されなければならない。
* **パッキング**: 暗黙のパディングが発生しないよう、メンバはサイズ順に並べるか、パッキングを明示する。
* **WasiIov (`wasi_ciovec_t`) 構造体のレイアウト**:
  * `buf`: データの開始アドレスを示すポインタ（`uint32_t` / 4バイト）
  * `buf_len`: データのバイト長（`uint32_t` / 4バイト）

**境界検査先行と整数オーバーフロー防止 (`SYS-GOTCHA-02`)**:
ゲスト空間のアドレス `fb_offset_t` は、カーネル側で物理アドレスに解決される前に必ず境界チェックを行う。整数オーバーフロー攻撃（`offset + size` の加算結果が 32bit を超えて小さな値にラップし、境界チェックをすり抜ける脆弱性）を完全に防ぐため、境界検査式は必ず `offset > guest_memory_size or size > guest_memory_size - offset` の減算形式で先行評価し、違反時はメモリアクセス前に即座に WASI `errno_t` の `EFAULT` で拒絶する（具体的な数値は `{Syscall_Mapping}` の WASI 準拠定義を正本とする）。

**WASI iovec 散在ギャザーの全要素事前検証 (`SYS-GOTCHA-03`)**:
散在ギャザー（iovec 配列）の各バッファ要素（`buf + len`）は、実際の出力ストリームへの書き込みを開始する前に全数事前検証される。途中の要素に境界外アドレスが含まれている場合、先行する正常要素であっても 1 バイトも出力ストリームへ書き込まず即座に `EFAULT` を返却する。これにより、異常終了時に中途半端なデータが出力先に漏洩・残存することを防止する。

### 5.2. 戻り値
<!-- traceability: {Syscall_Return_Value} {Errorcode_To_Strategy} -->
`fireball_call`は `u32` 型の値を返す。成功時は `0` を返し、失敗時は非0の定義されたエラーコード（WASIの `errno_t` に準拠）を返す。エラーコードの詳細は `{Syscall_Mapping}` の各定義および別紙参照。ゲスト側の `libfireball` は必要に応じてこの値をWASIの戻り値へ変換する。 `{Syscall_Return_Value}` `{Errorcode_To_Strategy}`

**未定義 Syscall ID の非パニック安全復帰 (`SYS-GOTCHA-01`)**:
未定義または予約済みのシステムコール ID が呼び出された場合、ホスト側はアボートやカーネルパニックを発生させず、WASI 準拠の `WasiErrno.NOSYS`（52）を返却して安全に復帰する。これにより、新機能の有無を動的に問い合わせるゲストランタイムや標準ライブラリ（WASI libc 等）がフォールバック機構を安全に機能させることができる。

## 6. システムコールID
システムコールIDは、`fireball_call`が実行する特定の操作を識別し、vMMIOの全機能をカバーする。カテゴリ別に管理される。

### 6.1. カテゴリ一覧

<!-- traceability: {Type_Vocabulary} {IPC_HandleBased} {CSPCommunication} -->

| カテゴリ | ID範囲 | 説明 |
| :--- | :--- | :--- |
| System | `0x00`-`0x0F` | 実行制御 |
| vMMIO Generic | `0x10`-`0x1F` | vMMIOレジスタの汎用読み書き |
| VDMA | `0x20`-`0x2F` | 仮想DMA操作 |
| IRQ | `0x30`-`0x3F` | 仮想割り込み管理 |
| IPC | `0x40`-`0x4F` | ハンドル解決およびCSPメッセージ通信 `{IPC_HandleBased}` `{CSPCommunication}` |
| WASI | `0x80`-`0xBF` | WASI互換レイヤー |

### 6.2. System (`0x00`-`0x0F`)
<!-- traceability: {CooperativeMultitasking} -->

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | `RESERVED` | — | — | 予約済み |
| `0x01` | `SYS_YIELD` | — | `0` | 協調的イールド要求 `{CooperativeMultitasking}` |
| `0x02` | `SYS_HALT` | — | — | システム停止 |
| `0x03` | `SYS_RESET` | — | `0` | ゲストリセット |

### 6.3. vMMIO Generic (`0x10`-`0x1F`)
vMMIOアドレス空間全体への汎用アクセス。SYSCTL/IPCR/VDMA/SHM/DYNAMIC/PASSTHROUGHすべての領域に対応。アクセス可否は、対象物理アドレスが `FB_CONF_VMMIO_ALLOWED_ADDRS`（[`system_config.md`](docs/components/tier1_core/system_config.md)）の許可範囲に属するかで判定される。この許可判定はタスク単位ではなく物理アドレス単位のグローバルなゲートであり、PTEに埋め込まれた権限フィールドが唯一の検証点となる（`runtime_vmmio.md` を正本とする）。SHM領域（FC=14）等、タスク間で所有権が移動するリソースの排他制御は `{RoleBasedAccessControl}` と IPCルータの所有権移譲によって別途行われ、vMMIOの物理アクセス許可判定とは独立している。 `{META_RestrictedPhysicalAccess}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x10` | `MMIO_READ32` | `addr` (`fb_val_t`: 物理アドレス) | `value` (`fb_val_t`: 32bit値、エラー時は `ERR_OUT_OF_BOUNDS`) | 32bit読み出し |
| `0x11` | `MMIO_WRITE32` | `addr` (`fb_val_t`: 物理アドレス), `value` (`fb_val_t`: 32bit値) | `0` (エラー時は `ERR_OUT_OF_BOUNDS` または `ERR_ACCESS_DENIED`) | 32bit書き込み |
| `0x12` | `MMIO_READ8` | `addr` (`fb_val_t`: 物理アドレス) | `value` (`fb_val_t`: 8bit値、エラー時は `ERR_OUT_OF_BOUNDS`) | 8bit読み出し |
| `0x13` | `MMIO_WRITE8` | `addr` (`fb_val_t`: 物理アドレス), `value` (`fb_val_t`: 8bit値) | `0` (エラー時は `ERR_OUT_OF_BOUNDS` または `ERR_ACCESS_DENIED`) | 8bit書き込み |
| `0x14` | `MMIO_BULK_READ` | `addr` (`fb_val_t`: 物理アドレス), `dest_offset` (`fb_offset_t`: ゲスト物理ベース相対), `byte_count` (`fb_val_t`: 転送バイト数) | `0` (エラー時は `ERR_OUT_OF_BOUNDS`, `ERR_ACCESS_DENIED` または `ERR_INVALID_SIZE`) | バルク読み出し（ゲストメモリへコピー） `{META_RestrictedPhysicalAccess}` |
| `0x15` | `MMIO_BULK_WRITE` | `addr` (`fb_val_t`: 物理アドレス), `src_offset` (`fb_offset_t`: ゲスト物理ベース相対), `byte_count` (`fb_val_t`: 転送バイト数) | `0` (エラー時は `ERR_OUT_OF_BOUNDS`, `ERR_ACCESS_DENIED` または `ERR_INVALID_SIZE`) | バルク書き込み（ゲストメモリから書込） `{META_RestrictedPhysicalAccess}` |
| `0x16` | `TRIGGER_SET_PIN` | `pin` (`fb_val_t`), `value` (`fb_val_t`: 0/1) | `0` (エラー時は `ERR_OUT_OF_BOUNDS` または `ERR_ACCESS_DENIED`) | GPIOピン出力設定（`{Fast_Path_GPIO}` vMMIO直接ストアのゲストアダプタ経路、`FB_SYSCALL_TRIGGER_SET_PIN`）。**pysim実験実装での状態**: 専用のGPIO vMMIOレジスタ配線が未実装のため、`fireball_call` ディスパッチテーブルには未登録であり、呼び出すと `SYS-GOTCHA-01` の規定通り安全に `WasiErrno.NOSYS` を返す（GPIOはこの実験では IPC 経由の `fireball://device/gpio/0` デバイスとして到達可能）。実機ターゲットでの本ID実装は別途 vMMIO GPIO レジスタ配線を前提とする。 |

### 6.4. VDMA (`0x20`-`0x2F`)
<!-- traceability: {VDMA} -->
仮想DMA操作のセマンティックラッパー。内部的にvMMIO VDMAレジスタへの書き込みに変換される。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x20` | `VDMA_START` | `src`, `dst`, `byte_count` | `0` | DMA転送開始 `{VDMA}` |

### 6.5. IRQ (`0x30`-`0x3F`)
<!-- traceability: {CooperativeMultitasking} {META_RestrictedPhysicalAccess} {VDMA} -->
vIRQ は vMMIO の専用ページと COOS の汎用 `interrupt-event` を通じて配送する。システムコールから `REG_IRQ_FLAGS` を読み書きする旧方式は採用せず、WASIのpoll APIにも接続しない。 `{CooperativeMultitasking}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x30`〜`0x3F` | `IRQ_RESERVED` | — | — | 旧フラグ操作を再導入せず、原因付きvIRQ配送はvMMIO vIRQページで行うため予約 |

### 6.6. IPC (`0x40`-`0x4F`)
<!-- traceability: {CSPCommunication} {IPC_HandleBased} -->
CSPチャネルおよびハンドルベースのプロセス間通信。
URIによる名前解決後の接続確立（`lookup`）によって取得した `handle_id` を用いて、以降は直接メッセージパッシングを行う（`{IPC_HandleBased}`）。メッセージの送受信は、ホーアのCSPモデルに基づくゼロコピー所有権移譲を伴う同期通信として処理される（`{CSPCommunication}`）。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x40` | `IPC_SEND` | `handle_id`, `msg_offset`, `msg_len` | `0` / errno | メッセージ送信（msg_offset: 送信メッセージ構造体の相対オフセット）。指定したハンドルを介してムーブセマンティクスによる送信を行う。 |
| `0x41` | `IPC_RECV` | `handle_id`, `buf_offset`, `buf_len` | `recv_len` / errno | メッセージ受信（buf_offset: 受信バッファの相対オフセット）。指定したハンドルからメッセージを受け取る（バッファが空の場合はコルーチンがサスペンドされる）。 |
| `0x42` | `IPC_LOOKUP` | `uri_offset`, `uri_len` | `handle_id` / errno | 名前解決とハンドル取得（uri_offset: URI文字列の相対オフセット）。URI文字列の相対オフセットから通信ハンドルを返却する。 `{IPC_HandleBased}` |

### 6.7. WASI (`0x80`-`0xBF`)
<!-- traceability: {WASI_Implementation} -->
WASI互換レイヤー。Tier 3 の `libfireball` が `wasi-libc` 等のゲスト側呼び出しをこれらのIDに変換する。本ドキュメントはホスト側のシステムコールIDとディスパッチ仕様に限定し、高レベルのゲストバインディングは Tier 3 の `libfireball` 仕様を正本とする。 `{WASI_Implementation}`
WASIの引数レイアウトとエラー変換は、`libfireball` が `runtime_syscall.md` のABI契約に従って実施する。ホスト側ディスパッチはゲストラッパーの呼び出し順序を知らない。 `{WASI_Implementation}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x80` | `WASI_FD_WRITE` | `fd`, `iovs_ptr`, `iovs_len`, `nwritten_ptr` | errno | ファイル書き込み |
| `0x81` | `WASI_FD_READ` | `fd`, `iovs_ptr`, `iovs_len`, `nread_ptr` | errno | ファイル読み出し |
| `0x82` | `WASI_FD_CLOSE` | `fd` | errno | ファイルクローズ |
| `0x83` | `WASI_CLOCK_TIME_GET` | `clock_id`, `precision`, `time_ptr` | errno | 時刻取得 |
| `0x84` | `WASI_PROC_EXIT` | `exit_code` | — | プロセス終了 |
| `0x85` | `WASI_RANDOM_GET` | `buf_ptr`, `buf_len` | errno | 乱数取得 |

本カテゴリのIDは、Tier 3 の `libfireball` がWASI互換呼び出しから適切に発行する。ホスト側では本書のディスパッチ契約に従って処理する。 `{WASI_Implementation}`

> [!NOTE]
> 最速のGPIOアクセスは `{Fast_Path_GPIO}` に従い vMMIO 空間への直接ストア（PASSTHROUGH領域経由、トラップ不要）を用いる。専用syscallは原則不要であるが、WASI互換用途では `libfireball` が MMIO Generic または `FB_SYSCALL_TRIGGER_SET_PIN` を介した呼び出しを提供できる。

##### システムコール ID 定義一覧表 (`fb_syscall_id`)
<!-- traceability: {Syscall_Mapping} -->

| グループ | 識別子名 | 値 (ID) | 説明 |
| :--- | :--- | :--- | :--- |
| **Reserved** | `reserved` | `0x00` | 予約済み |
| **System** | `sys_yield` | `0x01` | スケジューラへ協調的制御譲渡 |
| | `sys_halt` | `0x02` | システム停止 |
| | `sys_reset` | `0x03` | システムリセット |
| **vMMIO Generic** | `mmio_read32` | `0x10` | 32-bit MMIO 読み出し |
| | `mmio_write32` | `0x11` | 32-bit MMIO 書き込み |
| | `mmio_read8` | `0x12` | 8-bit MMIO 読み出し |
| | `mmio_write8` | `0x13` | 8-bit MMIO 書き込み |
| | `mmio_bulk_read` | `0x14` | 一括 MMIO 読み出し |
| | `mmio_bulk_write`| `0x15` | 一括 MMIO 書き込み |
| | `trigger_set_pin` (`FB_SYSCALL_TRIGGER_SET_PIN`) | `0x16` | GPIOピン出力設定（ゲストアダプタ経路） |
| **VDMA** | `vdma_start` | `0x20` | 仮想 DMA 転送開始 |
| **IRQ** | `IRQ_RESERVED` | `0x30`〜`0x3F` | 旧フラグ操作を再導入しないため予約。原因付きvIRQ配送はvMMIO vIRQページで行う |
| **IPC** | `ipc_send` | `0x40` | IPC メッセージ送信 |
| | `ipc_recv` | `0x41` | IPC メッセージ受信 |
| | `ipc_lookup` | `0x42` | サービス/デバイス URI 検索 |
| **WASI** | `wasi_fd_write` | `0x80` | ファイルディスクリプタ書き込み |
| | `wasi_fd_read` | `0x81` | ファイルディスクリプタ読み込み |
| | `wasi_fd_close` | `0x82` | ファイルディスクリプタ破棄 |
| | `wasi_clock_time_get` | `0x83` | 単調時刻取得 |
| | `wasi_proc_exit` | `0x84` | プロセス終了 |
| | `wasi_random_get` | `0x85` | 乱数取得 |

## 7. ゲスト向けバインディングの境界

### 7.1. 役割

<!-- traceability: {WIT_Interface_Purpose} -->
ゲストのWASI互換呼び出しを `fireball_call` ABIへ変換する責務は、本コンポーネントには含めない。ゲスト側の静的ライブラリ `libfireball` が、Preview1 APIの引数整理、HALハンドル操作、戻り値変換を担当する。本節は両コンポーネントの境界を宣言する。

### 7.2. 高レベルバインディングの参照先
<!-- traceability: {Trap_Interface} {Syscall_Mapping} -->

`fireball_call` の raw trap ABI、システムコールID、引数境界検証は本書で定義する。WASI Preview1 の `fd_write` 等をこのABIへ変換するコード例やゲストライブラリのビルド形態は Tier 3 の `libfireball` 仕様に置く。HALのデバイス固有処理は [`hal_dispatch.md`](docs/components/tier2_runtime/hal_dispatch.md) の公開抽象IFを経由し、物理ドライバ詳細はその下位仕様へ委譲する。

## 8. WASIホスト側実装

### 8.1. 役割
<!-- traceability: {Challenge_WasiFdWriteLoop} {WASI_Async_Bridge} -->
`fireball_call` を捕捉し、`id` に基づいて適切なハンドラにディスパッチする。WASI関連の呼び出しに対しては、対応するサービスや下位レイヤーのハードウェアHAL（Zephyr/SoC SDKなど）の操作を実行する。

| 機構名 | 課題と背景 | 解決方針・設計構造 | 関連キーワード |
| :--- | :--- | :--- | :--- |
| **Scatter/Gather 分割処理** | WASI `fd_write` は `ciovec` 配列による一括書き込みを要求するが、ホスト側でベクタ解析ループを抱えるとホスト実装が肥大化する | **`libfireball` 側ループ設計**: ゲスト側ライブラリがベクタを反復し、1 ベクタごとに `fireball_call` を発行する。ホスト側はステートレスな単一ブロックディスパッチに専念する | `{Challenge_WasiFdWriteLoop}` |
| **同期WASI・非同期IPC ブリッジ** | ゲスト側の同期 WASI 呼び出しと Fireball の非同期 CSP IPC の実行モデル不一致 | **コルーチン Yield 連動**: ラッパー内の `wait_for_ipc_response` が内部で `co_yield()` を発行し、VSoC/COOS が I/O 完了までタスクを安全にサスペンドする | `{WASI_Async_Bridge}` |

## 9. ホストからゲストへの非同期通知メカニズム
<!-- traceability: {Asynchronous_Notification} -->

ホスト側で非同期に発生したイベント（例: ハードウェア割り込みの完了、タイマーイベント、非同期I/Oの完了など）をゲストに通知するために、`fireball_call`とは独立したメカニズムを定義する。 `{Asynchronous_Notification}`

### 9.1. 仮想割り込み
<!-- traceability: {Asynchronous_Notification} -->
ホストは、ゲストに対して**仮想割り込み**をトリガーすることで、イベントの発生を通知する。これはvSoCの`notify_virtual_interrupt`機能を利用する。

#### 9.1.1. 仮想割り込みID 一覧表
<!-- traceability: {Asynchronous_Notification} -->
これらのIDは、WASI 0.3p のポーリング相当操作を `libfireball` が利用する際の通知トリガーとして使用される。

| 仮想割り込み識別子名 | 値 (ID) | 説明 | 主な用途 |
| :--- | :--- | :--- | :--- |
| `reserved` | `0x00` | 予約済み | システム内部 |
| `trigger_event` | `0x01` | 高速トリガーイベント | GPIO / エッジ通知 |
| `timer_expired` | `0x02` | タイマー満了 | WASI Clocks 用 |
| `stream_ready` | `0x03` | ストリーム準備完了 | WASI I/O 用 |

#### 9.1.2. 仮想割り込みペイロード
<!-- traceability: {Asynchronous_Notification} -->
仮想割り込みに関する詳細な情報（例えば、UARTから受信したデータ、タイマーID、非同期操作の結果コードなど）は、vMMIOレジスタや共有メモリ上の事前に定義された領域を介してゲストに伝達される。ゲストは割り込みハンドラ内でこれらの情報を読み取り、適切な非同期イベント処理を行う。

## 10. メモリ安全性
<!-- traceability: {Challenge_SyscallMemorySafety} {OwnershipTransfer} {FastAddressCheck} -->
`fireball_call` を介してゲストメモリへのポインタが渡される場合でも、アクセスしてはならない領域は仮想アドレス空間から物理的に **unmap（マッピング解除）** されている。他タスク所有の SHM 領域や転送中（`IN_FLIGHT`）のページ、未割当領域へのアクセスは、ソフトウェア的な許可チェックを待つまでもなく、PTE / TLB 不在による未登録ページトラップ（`TRAP_UNREGISTERED_PAGE`）としてハードウェア・仮想化境界で即座に遮断される。ゲストRAM（リニアメモリ）も単一の境界比較（`FastAddressCheck`）で保護されるため、ホスト側での二重のポインタ検証（`vsoc_validate_ptr` 等）は完全に不要であり、ゼロオーバーヘッドのメモリ安全性が保証される。 `{Challenge_SyscallMemorySafety}`

## 11. トラップ状態プロトコル
<!-- traceability: {Trap_Interface} -->

`fireball_call` は、トラップ命令（RISC-Vの `ecall` や ARMの `svc` 等）をベースにした同期通信インターフェースである。ゲストWASM実行環境においてインポート関数呼び出し（`call`）が行われると、実行エンジン（Interpreter/JIT）がこれをトラップし、ホスト側の対応するC++ハンドラに制御を同期的に移譲する（トラップ状態プロトコル）。

##### トラップ実行の制御フロー
トラップ命令ベースの同期通信インターフェース（`{Trap_Interface}`）における、具体的な実行制御フローは以下の通りである。

1. **ゲスト実行**: ゲストが `fireball_call(id, a0, ...)` を呼び出す。
2. **トラップ検知**: 実行エンジンがインポート関数のトラップ（トラップ命令に相当）を検知。
3. **レジスタマッピング**: 引数 `id` および `a0`〜`a5` が仮想レジスタ `REG_SYSCALL_*` にコピーされる。
4. **ホストディスパッチ**: ホスト側ハンドラが呼び出され、処理が同期的に実行される。この間、ゲストタスクのPC（Program Counter）はトラップ命令位置で静止し、スタックおよびローカル変数は自動的に保存される。
5. **完了と復帰**: ホストが `REG_SYSCALL_RET` に戻り値を設定すると、実行エンジンがゲストタスクのPCを次の命令に進め、実行を自動的に復元・再開する。
