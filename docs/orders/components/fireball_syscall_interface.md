# Fireball System Call Interface Specification

## 1. 目的 (Purpose)
本ドキュメントは、WebAssemblyゲスト環境からホストの提供するサービスを呼び出すための汎用システムコール `fireball_call` のインターフェイス仕様を定義する。特に、WASI (WebAssembly System Interface) 呼び出しを `fireball_call` にマッピングするための規約、および関連するShimライブラリとWASIホスト側実装の役割に焦点を当てる。

## 2. 背景 (Background)
`fireball_call` は、シングル・トラップ命令とvMMIOレジスタによる引数渡しを介して、ホスト側のグルーコードを最小化し、複雑なロジックをゲスト側のShimライブラリにオフロードすることを目的としている。

## 3. `fireball_call` WIT定義 (WIT Definition)
`fireball_call`のWIT (WebAssembly Interface Type) 定義は以下の通りである。

```wit
package fireball:host;

interface trap {
  /// Performs a host call with a given ID and arguments.
  ///
  /// `id`: The identifier for the host operation.
  /// `arg0`, `arg1`, `arg2`, `arg3`: General-purpose arguments.
  ///
  /// Returns a `u32` result, typically an error code or a direct return value.
  fireball-call: func(id: u32, arg0: u32, arg1: u32, arg2: u32, arg3: u32, arg4: u32, arg5: u32) -> u32;
}

world fireball {
  export trap;
}
```

## 4. `fireball_call` 呼び出し規約 (Calling Convention)

### 4.1. 引数のパッキング (Argument Packing)
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

### 4.2. 戻り値 (Return Value)
`fireball_call`は `u32` 型の値を返す。これは通常、0が成功を示し、非0の値はWASIの`errno`に準拠したエラーコードを示す。

## 5. システムコールID (System Call IDs)
システムコールIDは、`fireball_call`が実行する特定の操作を識別するために使用される。これらのIDは `inc/fireball_syscalls.hxx` にて列挙型として定義される。

例:
```cpp
// inc/fireball_syscalls.hxx (仮)
enum class FBSyscallId : uint32_t {
    FB_SYSCALL_RESERVED = 0,
    // WASI File System
    FB_SYSCALL_WASI_FD_WRITE = 1,
    FB_SYSCALL_WASI_FD_READ = 2,
    FB_SYSCALL_WASI_FD_SEEK = 3,
    FB_SYSCALL_WASI_FD_CLOSE = 4,
    // ... その他WASI関数
    // vMMIO
    FB_SYSCALL_VMMIO_READ = 0x1000,
    FB_SYSCALL_VMMIO_WRITE = 0x1001,
    // ... その他Fireball固有サービス
};
```

## 6. Fireball Shim (`libfireball_shim`)

### 6.1. 役割 (Role)
ゲストのWASI互換ライブラリ（`wasi-libc`など）からの呼び出しを傍受し、`fireball_call`呼び出し規約に従ってホストの`fireball_call`へ変換する。

### 6.2. WASI `fd_write` のマッピング例 (Example: WASI `fd_write` Mapping)
WASI `fd_write` のシグネチャ:
`fd_write(fd: fd, iovs: const_iovec_array, num_iovs: size) -> result<size, errno>`

`fireball_call` へのマッピング:
*   `id`: `FB_SYSCALL_WASI_FD_WRITE`
*   `arg0`: `fd` (ファイルディスクリプタ)
*   `arg1`: `iovs_ptr` (ゲストメモリ内の `wasi_iovec_t` 配列のポインタ)
*   `arg2`: `iovs_len` (配列の要素数)
*   `arg3`: `nwritten_ptr` (書き込まれたバイト数を格納するゲストメモリ上の `size_t` のポインタ)
*   `arg4`: `0` (未使用)
*   `arg5`: `0` (未使用)

`wasi_iovec_t` 構造体はゲストメモリに配置される。
```c
// ゲスト側 (libfireball_shim.h)
typedef struct {
    uint32_t buf;  // ポインタ (ゲストメモリ内のアドレス)
    uint32_t buf_len; // バッファ長
} wasi_iovec_t;

// ゲスト側でのfd_writeの実装例
ssize_t __wasi_fd_write(int fd, const wasi_iovec_t* iovs, size_t iovs_len, size_t* nwritten) {
    uint32_t result = __fireball_call(
        (uint32_t)FBSyscallId::FB_SYSCALL_WASI_FD_WRITE,
        (uint32_t)fd,
        (uint32_t)iovs, // iovs配列のゲストメモリ上のポインタ
        (uint32_t)iovs_len,
        (uint32_t)nwritten, // nwritten変数のゲストメモリ上のポインタ
        0, // 未使用
        0  // 未使用
    );
    // resultからerrnoへの変換処理
    return (ssize_t)result; // 便宜上の変換。実際はWASIのエラーハンドリングに従う
}
```

## 7. WASIホスト側実装 (WASI Host-Side Implementation)

### 7.1. 役割 (Role)
`fireball_call` を捕捉し、`id` に基づいて適切なハンドラにディスパッチする。WASI関連の呼び出しに対しては、対応するサービスや下位レイヤーのハードウェアHAL（Zephyr/SoC SDKなど）の操作を実行する。

### 7.2. WASI `fd_write` の処理例 (Example: WASI `fd_write` Handling)
ホスト側では、`fireball_call`のハンドラが以下のように動作する。

1.  `id` が `FB_SYSCALL_WASI_FD_WRITE` であることを確認。
2.  `arg0` から `fd` を抽出。
3.  `arg1` (`iovs_ptr`) と `arg2` (`iovs_len`) からゲストメモリ内の `wasi_iovec_t` 配列を読み取る。この際、ゲストメモリのアドレスをホストメモリのアドレスに変換する必要がある。
4.  `arg3` (`nwritten_ptr`) から書き込まれたバイト数を格納するゲストメモリ上のポインタを取得。
5.  ホストOSの`writev`または同等の関数を呼び出し、実際の書き込みを行う。
6.  書き込み結果（バイト数またはエラーコード）を `nwritten_ptr` が指すゲストメモリに書き込み、`fireball_call`の戻り値としてエラーコードを返す。

## 8. ホストからゲストへの非同期通知メカニズム (Host-to-Guest Asynchronous Notification Mechanism)

ホスト側で非同期に発生したイベント（例: ハードウェア割り込みの完了、タイマーイベント、非同期I/Oの完了など）をゲストに通知するために、`fireball_call`とは独立したメカニズムを定義する。

### 8.1. 仮想割り込み (Virtual Interrupts)
ホストは、ゲストに対して**仮想割り込み**をトリガーすることで、イベントの発生を通知する。これはvSoCの`notify_virtual_interrupt`機能を利用する。

#### 8.1.1. 仮想割り込みID (Virtual Interrupt IDs)
各仮想割り込みにはユニークなIDが割り当てられる。これらのIDは、`inc/fireball_virtual_interrupts.hxx`などのヘッダファイルで定義される。

例:
```cpp
// inc/fireball_virtual_interrupts.hxx (仮)
enum class FBVirtualInterruptId : uint32_t {
    FB_VIRT_INT_RESERVED = 0,
    FB_VIRT_INT_UART0_RX_READY = 1,
    FB_VIRT_INT_TIMER0_EXPIRED = 2,
    // ... その他割り込みイベント
};
```

#### 8.1.2. 仮想割り込みペイロード (Virtual Interrupt Payload)
仮想割り込みに関する詳細な情報（例えば、UARTから受信したデータ、タイマーID、非同期操作の結果コードなど）は、vMMIOレジスタや共有メモリ上の事前に定義された領域を介してゲストに伝達される。ゲストは割り込みハンドラ内でこれらの情報を読み取ることができる。

### 8.2. ゲスト側での処理 (Guest-Side Handling)
ゲストは、ホストからの仮想割り込みを受信した際に、対応する割り込みハンドラを実行する。このハンドラ内で、仮想割り込みIDを解析し、vMMIOレジスタや共有メモリからペイロードを読み取り、適切な非同期イベント処理を行う。

### 8.3. 非同期I/Oの完了通知 (Asynchronous I/O Completion)
`fireball_call`で開始された非同期I/O操作（例: 非ブロッキング`fd_read`）の完了は、仮想割り込みを介してゲストに通知される。通知には、完了した操作のID、結果ステータス、読み書きされたデータ長などの情報が含まれる。

## 9. メモリ安全性 (Memory Safety)
`fireball_call`を介してゲストメモリへのポインタが渡される場合、WASIホスト側実装は以下の検証を行う必要がある。
*   渡されたポインタがゲストのメモリ空間内に収まっていること。
*   アクセスされるメモリ領域が、ゲストが所有し、かつ要求された操作（読み取り/書き込み）に対して適切なパーミッションを持つこと。
*   `{Challenge_SyscallMemorySafety}` にて定義された追加のメモリ安全対策を適用する。

## 10. 考慮事項 (Considerations)
*   **同期/非同期操作の混在**: `fireball_call` は同期操作の開始を、仮想割り込みは非同期操作の完了を担うことで、異なる種類の操作を適切に処理する。
*   **イベントキューの導入**: 多数の非同期イベントが頻繁に発生する場合、仮想割り込みとvMMIOを組み合わせたイベントキューを導入し、ゲストが効率的にイベントを処理できるようにすることも検討する。
*   **オーバーヘッド**: 非同期通知の頻度とペイロードのサイズがパフォーマンスに与える影響を評価し、必要に応じて最適化を行う。
*   **拡張性**: 新しい非同期イベントを追加する際には、新しい仮想割り込みIDを定義し、ホストとゲストの両方のハンドラを更新する。
