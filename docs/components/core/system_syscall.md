# システムコール仕様 コンポーネント設計書

## 1. 目的
<!-- traceability: {NativeAPI_Export} -->
本ドキュメントは、WebAssemblyゲスト環境からホストの提供するサービスを呼び出すための汎用システムコール `fireball_call` のインターフェイス仕様を定義する。特に、WASI (WebAssembly System Interface) 呼び出しを `fireball_call` にマッピングするための規約、および関連するShimライブラリとWASIホスト側実装の役割に焦点を当てる。 `{NativeAPI_Export}`

## 2. 背景
<!-- traceability: {UnifiedAccessModel} -->
`fireball_call` は、vMMIO機能全体の**代理実行ラッパー**である。直接vMMIOアドレスにアクセスできないゲスト言語のために、シングル・トラップ命令経由でホストがvMMIO操作を代行する。

```
アクセスパスA: guest load/store(vMMIO_addr) → 許可テーブル → 直接物理アクセス
アクセスパスB: guest fireball_call(id, args) → host代理 → vMMIO → 許可テーブル → 直接物理アクセス
```

どちらのパスも最終的にvMMIO許可テーブルを通る。セキュリティゲートは1箇所。 `{UnifiedAccessModel}`

## 3. `fireball_call` WIT定義
<!-- traceability: {WIT_Interface_Spec} -->
`fireball_call`のWIT (WebAssembly Interface Type) 定義は以下の通りである。詳細は `docs/components/interface/interface_wit.md` を参照のこと。 `{WIT_Interface_Spec}`


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
  // 他の高レベル・インターフェイス（timer, bus, streams等）は、[interface_wit.md](../interface/interface_wit.md) においてリソース型として定義され、WASIバインディング経由で接続される。
}
```

##### トラップ高速パスとレジスタ直接マッピング
<!-- traceability: {Trap_Interface} -->
`fireball_call` は、実行環境のJIT/Interpreterが提供するインポート関数呼び出しをインターセプトし、ホスト側の仮想レジスタ `REG_SYSCALL_*` に引数を直接複写（レジスタマッピング）することで、トラップ（`ecall` / `svc` 等）の処理オーバヘッドを極限まで削減する高速パスを提供する。 `{Trap_Interface}`

## 4. `fireball_call` 呼び出し規約

### 4.1. 引数のパッキング
<!-- traceability: {Type_Vocabulary} -->

`fireball_call` は、後述の「型のエイリアス定義」で定義された型エイリアス（`fb_id_t`, `fb_val_t`, `fb_offset_t`）および規定の語彙セットに従って引数をパッキングする。物理的にはシステムコールID（`id`）と、6つの汎用引数（`arg0`〜`arg5`）の合計7つの `u32` 表現で構成され、インターフェイスの型安全性を担保する。WASI関数がこれらの引数よりも多くのパラメータを持つ場合、ゲストメモリの物理ベースアドレスからの相対オフセット（`fb_offset_t`）を渡す。絶対アドレスではなく相対オフセットに制限することで、ゲスト境界チェックを瞬時に行う。

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

#### 4.1.1. ゲストメモリ内構造体のレイアウト規則
`arg0`〜`arg5` にゲストメモリ上のポインタ（`iovs_ptr` 等）を渡す場合、データ構造は以下の制約に従って配置されなければならない。

* **アライメント**: すべての構造体およびそのメンバは **4バイトアライメント** に配置されなければならない。
* **パッキング**: 暗黙のパディングが発生しないよう、メンバはサイズ順に並べるか、パッキングを明示する。
* **WasiIov (`wasi_ciovec_t`) 構造体のレイアウト**:
  * `buf`: データの開始アドレスを示すポインタ（`uint32_t` / 4バイト）
  * `buf_len`: データのバイト長（`uint32_t` / 4バイト）

### 4.2. 戻り値
<!-- traceability: {Syscall_Return_Value} {Errorcode_To_Strategy} -->
`fireball_call`は `u32` 型の値を返す。成功時は `0` を返し、失敗時は非0の定義されたエラーコード（WASIの `errno_t` に準拠）を返す。エラーコードの詳細は 5.7節 および別紙参照。Shim層ではこのエラーコードがWITの `recovery-strategy` に変換されて上位に伝播する。 `{Syscall_Return_Value}` `{Errorcode_To_Strategy}`

## 5. システムコールID
システムコールIDは、`fireball_call`が実行する特定の操作を識別し、vMMIOの全機能をカバーする。カテゴリ別に管理される。

### 5.1. カテゴリ一覧

<!-- traceability: {Type_Vocabulary} {IPC_HandleBased} {CSPCommunication} -->

| カテゴリ | ID範囲 | 説明 |
| :--- | :--- | :--- |
| System | `0x00`-`0x0F` | 実行制御 |
| vMMIO Generic | `0x10`-`0x1F` | vMMIOレジスタの汎用読み書き |
| VDMA | `0x20`-`0x2F` | 仮想DMA操作 |
| IRQ | `0x30`-`0x3F` | 仮想割り込み管理 |
| IPC | `0x40`-`0x4F` | ハンドル解決およびCSPメッセージ通信 `{IPC_HandleBased}` `{CSPCommunication}` |
| WASI | `0x80`-`0xBF` | WASI互換レイヤー |

### 5.2. System (`0x00`-`0x0F`)
<!-- traceability: {CooperativeMultitasking} -->

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | `RESERVED` | — | — | 予約済み |
| `0x01` | `SYS_YIELD` | — | `0` | 協調的イールド要求 `{CooperativeMultitasking}` |
| `0x02` | `SYS_HALT` | — | — | システム停止 |
| `0x03` | `SYS_RESET` | — | `0` | ゲストリセット |

### 5.3. vMMIO Generic (`0x10`-`0x1F`)
vMMIOアドレス空間全体への汎用アクセス。SYSCTL/IPCR/VDMA/SHM/DYNAMIC/PASSTHROUGHすべての領域に対応。アクセス可否は、コンフィグで定義された静的なデバイス割り当て許可テーブル（vMMIOアクセス許可テーブル）に基づいて、タスクIDと対象物理アドレス範囲が合致するか検証される。 `{RoleBasedAccessControl}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x10` | `MMIO_READ32` | `addr` (`fb_val_t`: 物理アドレス) | `value` (`fb_val_t`: 32bit値、エラー時は `ERR_OUT_OF_BOUNDS`) | 32bit読み出し |
| `0x11` | `MMIO_WRITE32` | `addr` (`fb_val_t`: 物理アドレス), `value` (`fb_val_t`: 32bit値) | `0` (エラー時は `ERR_OUT_OF_BOUNDS` または `ERR_ACCESS_DENIED`) | 32bit書き込み |
| `0x12` | `MMIO_READ8` | `addr` (`fb_val_t`: 物理アドレス) | `value` (`fb_val_t`: 8bit値、エラー時は `ERR_OUT_OF_BOUNDS`) | 8bit読み出し |
| `0x13` | `MMIO_WRITE8` | `addr` (`fb_val_t`: 物理アドレス), `value` (`fb_val_t`: 8bit値) | `0` (エラー時は `ERR_OUT_OF_BOUNDS` または `ERR_ACCESS_DENIED`) | 8bit書き込み |
| `0x14` | `MMIO_BULK_READ` | `addr` (`fb_val_t`: 物理アドレス), `dest_offset` (`fb_offset_t`: ゲスト物理ベース相対), `byte_count` (`fb_val_t`: 転送バイト数) | `0` (エラー時は `ERR_OUT_OF_BOUNDS`, `ERR_ACCESS_DENIED` または `ERR_INVALID_SIZE`) | バルク読み出し（ゲストメモリへコピー） `{META_RestrictedPhysicalAccess}` |
| `0x15` | `MMIO_BULK_WRITE` | `addr` (`fb_val_t`: 物理アドレス), `src_offset` (`fb_offset_t`: ゲスト物理ベース相対), `byte_count` (`fb_val_t`: 転送バイト数) | `0` (エラー時は `ERR_OUT_OF_BOUNDS`, `ERR_ACCESS_DENIED` または `ERR_INVALID_SIZE`) | バルク書き込み（ゲストメモリから書込） `{META_RestrictedPhysicalAccess}` |

### 5.4. VDMA (`0x20`-`0x2F`)
<!-- traceability: {VDMA} -->
仮想DMA操作のセマンティックラッパー。内部的にvMMIO VDMAレジスタへの書き込みに変換される。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x20` | `VDMA_START` | `src`, `dst`, `byte_count` | `0` | DMA転送開始 `{VDMA}` |

### 5.5. IRQ (`0x30`-`0x3F`)
<!-- traceability: {CooperativeMultitasking} {RoleBasedAccessControl} {META_RestrictedPhysicalAccess} {VDMA} -->
仮想割り込みフラグの管理。`REG_IRQ_FLAGS` のラッパー。
割り込み処理とコルーチンベースの協調型マルチタスク（`{CooperativeMultitasking}`）が連動し、ISRによるフラグ操作時にREADYキューへの投入が行われる。これらのID呼び出しはロールマトリックス（`{RoleBasedAccessControl}`）および `{META_RestrictedPhysicalAccess}` に基づき、権限のないゲストからのアクセスは遮断される。また、仮想DMA（`{VDMA}`）完了時の割り込みクリアなどにも使用される。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x30` | `IRQ_READ_FLAGS` | — | `flags` | 割り込みフラグ読み出し |
| `0x31` | `IRQ_CLEAR` | `mask` | `0` | 指定ビットのフラグクリア |
| `0x32`〜`0x3F` | `IRQ_RESERVED` | — | — | 将来の割り込みベクタ拡張用（予約スロット） |

### 5.6. IPC (`0x40`-`0x4F`)
<!-- traceability: {CSPCommunication} {IPC_HandleBased} -->
CSPチャネルおよびハンドルベースのプロセス間通信。
URIによる名前解決後の接続確立（`lookup`）によって取得した `handle_id` を用いて、以降は直接メッセージパッシングを行う（`{IPC_HandleBased}`）。メッセージの送受信は、ホーアのCSPモデルに基づくゼロコピー所有権移譲を伴う同期通信として処理される（`{CSPCommunication}`）。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x40` | `IPC_SEND` | `handle_id`, `msg_offset`, `msg_len` | `0` / errno | メッセージ送信（msg_offset: 送信メッセージ構造体の相対オフセット）。指定したハンドルを介してムーブセマンティクスによる送信を行う。 |
| `0x41` | `IPC_RECV` | `handle_id`, `buf_offset`, `buf_len` | `recv_len` / errno | メッセージ受信（buf_offset: 受信バッファの相対オフセット）。指定したハンドルからメッセージを受け取る（バッファが空の場合はコルーチンがサスペンドされる）。 |
| `0x42` | `IPC_LOOKUP` | `uri_offset`, `uri_len` | `handle_id` / errno | 名前解決とハンドル取得（uri_offset: URI文字列の相対オフセット）。URI文字列の相対オフセットから通信ハンドルを返却する。 `{IPC_HandleBased}` |

### 5.7. WASI (`0x80`-`0xBF`)
<!-- traceability: {WASI_Implementation} -->
WASI互換レイヤー。Shimライブラリが `wasi-libc` の呼び出しをこれらのIDに変換する。本ドキュメントは物理的なシステムコールのマッピング仕様に特化し、高レベルのWITインターフェース定義（ファイル構成や型バインディングポリシー等）については [interface_wit.md](../interface/interface_wit.md) にて分離して定義されている。
WASI 0.2標準仕様に適合するように、各システムコールはShimによって `wasi_ciovec_t` レイアウトへ自動パッキングされ、ホスト側で `wasi:clocks` や `wasi:io` のリソース操作へと同期マッピングされる。 `{WASI_Implementation}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x80` | `WASI_FD_WRITE` | `fd`, `iovs_ptr`, `iovs_len`, `nwritten_ptr` | errno | ファイル書き込み |
| `0x81` | `WASI_FD_READ` | `fd`, `iovs_ptr`, `iovs_len`, `nread_ptr` | errno | ファイル読み出し |
| `0x82` | `WASI_FD_CLOSE` | `fd` | errno | ファイルクローズ |
| `0x83` | `WASI_CLOCK_TIME_GET` | `clock_id`, `precision`, `time_ptr` | errno | 時刻取得 |
| `0x84` | `WASI_PROC_EXIT` | `exit_code` | — | プロセス終了 |
| `0x85` | `WASI_RANDOM_GET` | `buf_ptr`, `buf_len` | errno | 乱数取得 |

本カテゴリのIDはすべてWASI 0.2標準仕様に適合するように Shim 側で適切に仲介・処理される。 `{WASI_Implementation}`

> [!NOTE]
> GPIOアクセスはMMIO Generic (`MMIO_READ32`/`MMIO_WRITE32`) でPASSTHROUGH領域経由。専用syscallは不要。

```cpp
// inc/fireball_syscalls.hxx
namespace fireball {
    enum class fb_syscall_id : uint32_t {
        reserved          = 0x00,

        // System
        sys_yield         = 0x01,
        sys_halt          = 0x02,
        sys_reset         = 0x03,

        // vMMIO Generic
        mmio_read32       = 0x10,
        mmio_write32      = 0x11,
        mmio_read8        = 0x12,
        mmio_write8       = 0x13,
        mmio_bulk_read    = 0x14,
        mmio_bulk_write   = 0x15,

        // VDMA
        vdma_start        = 0x20,

        // IRQ
        irq_read_flags    = 0x30,
        irq_clear         = 0x31,

        // IPC
        ipc_send          = 0x40,
        ipc_recv          = 0x41,
        ipc_lookup        = 0x42,

        // WASI
        wasi_fd_write     = 0x80,
        wasi_fd_read      = 0x81,
        wasi_fd_close     = 0x82,
        wasi_clock_time_get = 0x83,
        wasi_proc_exit    = 0x84,
        wasi_random_get   = 0x85,
    };
}
```

## 6. Fireball Shim (`libfireball_shim`)

### 6.1. 役割

<!-- traceability: {WIT_Interface_Purpose} -->
ゲストのWASI互換ライブラリ（`wasi-libc`など）からの呼び出しを傍受し、`fireball_call`呼び出し規約に従ってホストの`fireball_call`へ変換する。

### 6.2. 高応答 Trigger のマッピング例
<!-- traceability: {Fast_Path_GPIO} -->

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 最小レイテンシ確保のため `fireball_call` を直接使用してピン出力を設定する。 |
| シグネチャ | `fireball_trigger_set_pin(pin: u32, value: bool) -> void` |
| マッピング | `id`: `FB_SYSCALL_TRIGGER_SET_PIN`<br>`arg0`: `pin`<br>`arg1`: `value` (0/1) |

```python
# ゲスト側での trigger.set_pin の実装例 (Shim) `{Fast_Path_GPIO}`
def fireball_trigger_set_pin(pin: int, value: bool):
    __fireball_call(
        fb_syscall_id.FB_SYSCALL_TRIGGER_SET_PIN,
        pin,
        int(value),
        0, 0, 0, 0
    )
```
> [!IMPORTANT]
> WASI 0.2 標準のリソース（`output-stream` 等）は、対応する WIT インターフェイスの実装関数を通じて呼び出される。`fireball_call`はvMMIO機能全体の代理実行ラッパーであり、GPIOのような物理アクセスもMMIO Generic経由で行える。

## 7. WASIホスト側実装

### 7.1. 役割
<!-- traceability: {Challenge_WasiFdWriteLoop} {WASI_Async_Bridge} -->
`fireball_call` を捕捉し、`id` に基づいて適切なハンドラにディスパッチする。WASI関連の呼び出しに対しては、対応するサービスや下位レイヤーのハードウェアHAL（Zephyr/SoC SDKなど）の操作を実行する。

- **WASI `fd_write` の処理例 (Scatter/Gather)**: `{Challenge_WasiFdWriteLoop}`
    - WASI の `fd_write` は `ciovec` 配列による一括書き込みを要求する。
    - **Shim側ループ設計**: ホストを極小に保つため、Shim（ゲスト側ライブラリ）でベクタをループし、1ベクタごとに `fireball_call` を発行する設計を基本方針とする。ホスト側はシンプルなディスパッチに徹し、無駄な状態を持たない。
- **同期WASI と 非同期IPC のブリッジ**: `{WASI_Async_Bridge}`
    - 同期的な WASI 呼び出しを Fireball の非同期 IPC へマッピングする際、ラッパー内の `wait_for_ipc_response` が内部で `co_yield()` を発行する。
    - この `co_yield` を VSoC / COOS が適切にハンドリングし、I/O 完了までタスクをサスペンド状態にする密結合な連携が必要。


## 8. ホストからゲストへの非同期通知メカニズム
<!-- traceability: {Asynchronous_Notification} -->

ホスト側で非同期に発生したイベント（例: ハードウェア割り込みの完了、タイマーイベント、非同期I/Oの完了など）をゲストに通知するために、`fireball_call`とは独立したメカニズムを定義する。 `{Asynchronous_Notification}`

### 8.1. 仮想割り込み
<!-- traceability: {Asynchronous_Notification} -->
ホストは、ゲストに対して**仮想割り込み**をトリガーすることで、イベントの発生を通知する。これはvSoCの`notify_virtual_interrupt`機能を利用する。

#### 8.1.1. 仮想割り込みID

<!-- traceability: {Asynchronous_Notification} -->
これらのIDは、WASI 0.2 の `pollable` リソースをホスト側で ready 状態にするためのトリガーとして使用される。

例:
```cpp
// inc/fireball_virtual_interrupts.hxx
namespace fireball {
    enum class fb_virtual_interrupt_id : uint32_t {
        reserved          = 0,
        trigger_event     = 1, // 高速トリガーイベント
        timer_expired     = 2, // WASI Clocks 用
        stream_ready      = 3,  // WASI I/O 用
    };
}
```

#### 8.1.2. 仮想割り込みペイロード
<!-- traceability: {Asynchronous_Notification} -->
仮想割り込みに関する詳細な情報（例えば、UARTから受信したデータ、タイマーID、非同期操作の結果コードなど）は、vMMIOレジスタや共有メモリ上の事前に定義された領域を介してゲストに伝達される。ゲストは割り込みハンドラ内でこれらの情報を読み取り、適切な非同期イベント処理を行う。

## 9. メモリ安全性
<!-- traceability: {Challenge_SyscallMemorySafety} -->
`fireball_call`を介してゲストメモリへのポインタが渡される場合、統一vMMIOモデルの許可テーブルがセキュリティゲートとして機能する。別途の `vsoc_validate_ptr` は不要。 `{Challenge_SyscallMemorySafety}`

## 10. トラップ状態プロトコル
<!-- traceability: {Trap_Interface} -->

`fireball_call` は、トラップ命令（RISC-Vの `ecall` や ARMの `svc` 等）をベースにした同期通信インターフェースである。ゲストWASM実行環境においてインポート関数呼び出し（`call`）が行われると、実行エンジン（Interpreter/JIT）がこれをトラップし、ホスト側の対応するC++ハンドラに制御を同期的に移譲する（トラップ状態プロトコル）。

##### トラップ実行の制御フロー
トラップ命令ベースの同期通信インターフェース（`{Trap_Interface}`）における、具体的な実行制御フローは以下の通りである。

1. **ゲスト実行**: ゲストが `fireball_call(id, a0, ...)` を呼び出す。
2. **トラップ検知**: 実行エンジンがインポート関数のトラップ（トラップ命令に相当）を検知。
3. **レジスタマッピング**: 引数 `id` および `a0`〜`a5` が仮想レジスタ `REG_SYSCALL_*` にコピーされる。
4. **ホストディスパッチ**: ホスト側ハンドラが呼び出され、処理が同期的に実行される。この間、ゲストタスクのPC（Program Counter）はトラップ命令位置で静止し、スタックおよびローカル変数は自動的に保存される。
5. **完了と復帰**: ホストが `REG_SYSCALL_RET` に戻り値を設定すると、実行エンジンがゲストタスクのPCを次の命令に進め、実行を自動的に復元・再開する。
