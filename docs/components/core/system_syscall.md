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
  // 他の高レベル・インターフェイス（timer, bus, streams等）は別途定義
}
```

## 4. `fireball_call` 呼び出し規約

### 4.1. 引数のパッキング

<!-- traceability: {Trap_Interface} -->

TODO(Phase 1): ATC抽出 - fireball_callの各引数に渡されるポインタが、ゲストメモリの正当な境界内にあることの事前条件（および違反時のパニック/エラーモデル）を厳密化すること。

`fireball_call`は `id` と5つの汎用 `u32` 引数、**合計6つの `u32` 引数**を持つ。WASI関数がこれらの引数よりも多くのパラメータを持つ場合、ゲストメモリ内の構造体へのポインタを `u32` 引数として渡す。

| 引数名 | 型   | 説明                                            |
| :----- | :--- | :---------------------------------------------- |
| `id`   | `u32` | システムコールID (`FB_SYSCALL_*` で定義)       |
| `arg0` | `u32` | 汎用引数0、またはゲストメモリ内の構造体/バッファへのポインタ |
| `arg1` | `u32` | 汎用引数1、またはゲストメモリ内の構造体/バッファへのポインタ |
| `arg2` | `u32` | 汎用引数2、またはゲストメモリ内の構造体/バッファへのポインタ |
| `arg3` | `u32` | 汎用引数3、またはゲストメモリ内の構造体/バッファへのポインタ |
| `arg4` | `u32` | 汎用引数4、またはゲストメモリ内の構造体/バッファへのポインタ |
| `arg5` | `u32` | 汎用引数5、またはゲストメモリ内の構造体/バッファへのポインタ |

### 4.2. 戻り値
<!-- traceability: {Syscall_Return_Value} {Errorcode_To_Strategy} -->
`fireball_call`は `u32` 型の値を返す。これは通常、0が成功を示し、非0の値はWASIの`errno`に準拠したエラーコードを示す。Shim層ではこのエラーコードがWITの `recovery-strategy` に変換される。 `{Syscall_Return_Value}` `{Errorcode_To_Strategy}`

## 5. システムコールID
システムコールIDは、`fireball_call`が実行する特定の操作を識別し、vMMIOの全機能をカバーする。カテゴリ別に管理される。

### 5.1. カテゴリ一覧

<!-- traceability: {Type_Vocabulary} -->

| カテゴリ | ID範囲 | 説明 |
| :--- | :--- | :--- |
| System | `0x00`-`0x0F` | 実行制御 |
| vMMIO Generic | `0x10`-`0x1F` | vMMIOレジスタの汎用読み書き |
| VDMA | `0x20`-`0x2F` | 仮想DMA操作 |
| IRQ | `0x30`-`0x3F` | 仮想割り込み管理 |
| IPC | `0x40`-`0x4F` | プロセス間通信 |
| WASI | `0x80`-`0xBF` | WASI互換レイヤー |

### 5.2. System (`0x00`-`0x0F`)
<!-- traceability: {CooperativeMultitasking} -->

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x00` | `RESERVED` | — | — | 予約済み |
| `0x03` | `SYS_RESET` | — | `0` | ゲストリセット |
| `0x01` | `SYS_YIELD` | — | `0` | 協調的イールド要求 `{CooperativeMultitasking}` |

### 5.3. vMMIO Generic (`0x10`-`0x1F`)
<!-- traceability: {RoleBasedAccessControl} {RestrictedPhysicalAccess} -->
vMMIOアドレス空間全体への汎用アクセス。SYSCTL/IPCR/VDMA/SHM/DYNAMIC/PASSTHROUGHすべての領域に対応。許可テーブルでアクセス制御される。 `{RoleBasedAccessControl}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x10` | `MMIO_READ32` | `addr` | `value` | 32bit読み出し |
| `0x11` | `MMIO_WRITE32` | `addr`, `value` | `0` | 32bit書き込み |
| `0x12` | `MMIO_READ8` | `addr` | `value` | 8bit読み出し |
| `0x13` | `MMIO_WRITE8` | `addr`, `value` | `0` | 8bit書き込み |
| `0x14` | `MMIO_BULK_READ` | `addr`, `dest_ptr`, `byte_count` | `0` | バルク読み出し `{RestrictedPhysicalAccess}` |
| `0x15` | `MMIO_BULK_WRITE` | `addr`, `src_ptr`, `byte_count` | `0` | バルク書き込み `{RestrictedPhysicalAccess}` |

### 5.4. VDMA (`0x20`-`0x2F`)
<!-- traceability: {VDMA} -->
仮想DMA操作のセマンティックラッパー。内部的にvMMIO VDMAレジスタへの書き込みに変換される。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x20` | `VDMA_START` | `src`, `dst`, `byte_count` | `0` | DMA転送開始 `{VDMA}` |

### 5.5. IRQ (`0x30`-`0x3F`)
<!-- traceability: {CooperativeMultitasking} {RoleBasedAccessControl} {RestrictedPhysicalAccess} {VDMA} -->
仮想割り込みフラグの管理。`REG_IRQ_FLAGS` のラッパー。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x30` | `IRQ_READ_FLAGS` | — | `flags` | 割り込みフラグ読み出し |
| `0x31` | `IRQ_CLEAR` | `mask` | `0` | 指定ビットのフラグクリア |

### 5.6. IPC (`0x40`-`0x4F`)
<!-- traceability: {CSPCommunication} {IPC_HandleBased} -->
CSPチャネル経由のプロセス間通信。

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x40` | `IPC_SEND` | `channel_id`, `msg_ptr`, `msg_len` | `0` / errno | メッセージ送信 `{CSPCommunication}` `{IPC_HandleBased}` |
| `0x41` | `IPC_RECV` | `channel_id`, `buf_ptr`, `buf_len` | `recv_len` / errno | メッセージ受信 `{CSPCommunication}` `{IPC_HandleBased}` |

### 5.7. WASI (`0x80`-`0xBF`)
<!-- traceability: {WASI_Implementation} -->
WASI互換レイヤー。Shimライブラリが `wasi-libc` の呼び出しをこれらのIDに変換する。詳細は `docs/components/interface_wit.md` を参照のこと。 `{WASI_Implementation}`

| ID | 名前 | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| `0x80` | `WASI_FD_WRITE` | `fd`, `iovs_ptr`, `iovs_len`, `nwritten_ptr` | errno | ファイル書き込み |
| `0x81` | `WASI_FD_READ` | `fd`, `iovs_ptr`, `iovs_len`, `nread_ptr` | errno | ファイル読み出し |
| `0x82` | `WASI_FD_CLOSE` | `fd` | errno | ファイルクローズ |
| `0x83` | `WASI_CLOCK_TIME_GET` | `clock_id`, `precision`, `time_ptr` | errno | 時刻取得 |
| `0x84` | `WASI_PROC_EXIT` | `exit_code` | — | プロセス終了 |
| `0x85` | `WASI_RANDOM_GET` | `buf_ptr`, `buf_len` | errno | 乱数取得 |

> [!NOTE]
> GPIOアクセスはMMIO Generic (`MMIO_READ32`/`MMIO_WRITE32`) でPASSTHROUGH領域経由。専用syscallは不要。

```text
// inc/fireball_syscalls.hxx
enum class fb_syscall_id : uint32_t {
    RESERVED          = 0x00,

    // System
    SYS_YIELD         = 0x01,
    SYS_HALT          = 0x02,
    SYS_RESET         = 0x03,

    // vMMIO Generic
    MMIO_READ32       = 0x10,
    MMIO_WRITE32      = 0x11,
    MMIO_READ8        = 0x12,
    MMIO_WRITE8       = 0x13,
    MMIO_BULK_READ    = 0x14,
    MMIO_BULK_WRITE   = 0x15,

    // VDMA
    VDMA_START        = 0x20,

    // IRQ
    IRQ_READ_FLAGS    = 0x30,
    IRQ_CLEAR         = 0x31,

    // IPC
    IPC_SEND          = 0x40,
    IPC_RECV          = 0x41,

    // WASI
    WASI_FD_WRITE     = 0x80,
    WASI_FD_READ      = 0x81,
    WASI_FD_CLOSE     = 0x82,
    WASI_CLOCK_TIME_GET = 0x83,
    WASI_PROC_EXIT    = 0x84,
    WASI_RANDOM_GET   = 0x85,
};
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
    - **Shim側ループ設計**: ホストを極小に保つため、原則としてShim（ゲスト側ライブラリ）でベクタをループし、1ベクタごとに `fireball_call` を発行する構成を基本とする。ただし、ベンチマーク結果によりオーバーヘッドが過大な場合はホスト側ループへの移行を検討する。
- **同期WASI と 非同期IPC のブリッジ**: `{WASI_Async_Bridge}`
    - 同期的な WASI 呼び出しを Fireball の非同期 IPC へマッピングする際、ラッパー内の `wait_for_ipc_response` が内部で `co_yield()` を発行する。
    - この `co_yield` を VSoC / COOS が適切にハンドリングし、I/O 完了までタスクをサスペンド状態にする密結合な連携が必要。

TODO(Phase 0.8): WASI Wrapper TLA+ Verification - 同期/非同期変換（co_yield 伝播）時の実行状態の無矛盾性を検証する。

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
```text
// inc/fireball_virtual_interrupts.hxx (仮)
enum class FBVirtualInterruptId : uint32_t {
    FB_VIRT_INT_RESERVED = 0,
    FB_VIRT_INT_TRIGGER_EVENT = 1, // 高速トリガーイベント
    FB_VIRT_INT_TIMER_EXPIRED = 2, // WASI Clocks 用
    FB_VIRT_INT_STREAM_READY = 3,  // WASI I/O 用
};
```

#### 8.1.2. 仮想割り込みペイロード
<!-- traceability: {Asynchronous_Notification} -->
仮想割り込みに関する詳細な情報（例えば、UARTから受信したデータ、タイマーID、非同期操作の結果コードなど）は、vMMIOレジスタや共有メモリ上の事前に定義された領域を介してゲストに伝達される。ゲストは割り込みハンドラ内でこれらの情報を読み取り、適切な非同期イベント処理を行う。

## 9. メモリ安全性
<!-- traceability: {Challenge_SyscallMemorySafety} -->
`fireball_call`を介してゲストメモリへのポインタが渡される場合、統一vMMIOモデルの許可テーブルがセキュリティゲートとして機能する。別途の `vsoc_validate_ptr` は不要。 `{Challenge_SyscallMemorySafety}`

## 10. トラップ状態プロトコル

<!-- traceability: {Trap_Interface} -->
`fireball_call` はWASMの**インポート関数呼び出し**として実行される。そのため、明示的な状態保存/復元は不要。

- **保存**: WASMの呼び出し規約がスタック/ローカル変数を自動保存
- **復元**: WASMの `return` で自動復元
- **PC位置**: トラップ中のPCは `fireball_call` 命令内。戻り値取得後、次の命令に進む。
- **ホスト側**: WASMの実行状態に一切触れない。`REG_SYSCALL_*` レジスタだけが引数/戻り値の受け渡しに使われる。
