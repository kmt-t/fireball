# Fireball HAL インターフェース設計

**Version:** 0.2.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

Fireball HAL（Hardware Abstraction Layer）は、全ハードウェア デバイスアクセスに対して**統一された、ストリームベースのインターフェース**を提供します。デバイス固有の操作ではなく、6 つの標準的なシステムコールを使用します。

**設計思想：**
- **最小 API**: 6 操作のみ: `open`、`close`、`read`、`write`、`seek`、`ioctl`
- **ストリームベース**: 各デバイスが複数の独立ストリーム（ファイルディスクリプタ）をサポート
- **プラットフォーム非依存**: ドライバがプラットフォーム固有の詳細を処理、HAL はルーティングのみ
- **関数ポインタ登録**: デバイスドライバはコールバック関数で登録、デバイスツリー不要

---

## 1. コア HAL システムコール

### 1.1 6 つの操作

| 操作 | 目的 | シグネチャ |
|-----------|---------|-----------|
| **open** | デバイス ストリーム取得 | `int open(const char* uri, int flags)` |
| **close** | デバイス ストリーム解放 | `int close(int fd)` |
| **read** | デバイスからデータ読み取り | `ssize_t read(int fd, void* buf, size_t count)` |
| **write** | デバイスへデータ書き込み | `ssize_t write(int fd, const void* buf, size_t count)` |
| **seek** | 読み書き位置変更 | `off_t seek(int fd, off_t offset, int whence)` |
| **ioctl** | デバイス固有制御 | `int ioctl(int fd, uint32_t cmd, void* arg)` |

### 1.2 操作セマンティクス

**open(uri, flags)**: URI でデバイスストリーム開く → fd 返却。複数 open で独立ストリーム

**close(fd)**: fd を閉じてリソース解放

**read(fd, buf, count)**: 最大 count バイト読み込み → 読んだバイト数（EOF=0、エラー=-1）

**write(fd, buf, count)**: count バイト書き込み → 書いたバイト数（エラー=-1）

**seek(fd, offset, whence)**: 読み書き位置変更（`SEEK_SET`、`SEEK_CUR`、`SEEK_END`）。UART等ストリーミングデバイスは非対応

**ioctl(fd, cmd, arg)**: デバイス固有制御（例：`TIOCBAUD`、`GPIO_SET_DIR`）

---

## 2. デバイス ストリーム モデル

### 2.1 デバイスあたり複数ストリーム

各デバイスは複数の独立ストリームをサポート：

```
デバイス: "device://uart/0"
  ├─ ストリーム（fd=0）: open(flags=O_RDONLY） → UART 受信のみ
  ├─ ストリーム（fd=1）: open(flags=O_WRONLY） → UART 送信のみ
  ├─ ストリーム（fd=2）: open(flags=O_RDWR） → UART 双方向
  └─ ストリーム（fd=3）: open(flags=O_RDWR | O_NONBLOCK） → ノンブロッキングモード
```

**メリット：**
- 複数アプリケーションが同一デバイスに同時アクセス可能
- 各ストリームは独立した読み書き位置（シーク可能デバイスの場合）
- ストリーム単位の独立バッファリングとフロー制御

### 2.2 ファイルディスクリプタ管理

ファイルディスクリプタは HAL が管理する不透明ハンドル：

```cpp
typedef int fd_t;

// 有効範囲: 0-1023
// 負値: エラー
// 特殊値：
//   -1: EINVAL（不正な引数）
//   -2: ENODEV（デバイス不在）
//   -3: EACCES（アクセス拒否）
//   -4: EAGAIN（リソース一時的利用不可）
```

---

## 3. デバイス URI スキーム

### 3.1 URI フォーマット

すべてのデバイスを URI で識別：`device://TYPE/INDEX`

| フォーマット | 例 |
|--------|----------|
| `device://uart/0` | UART デバイス 0 |
| `device://gpio/5` | GPIO ピン 5 |
| `device://i2c/0` | I2C バス 0 |
| `device://spi/0` | SPI バス 0 |
| `device://adc/0` | ADC チャネル 0 |
| `device://timer/0` | タイマー 0 |

### 3.2 デバイスタイプ

| タイプ | 役割 | 主要操作 |
|--------|------|---------|
| **uart** | シリアル通信 | read、write、ioctl（ボーレート） |
| **gpio** | デジタル I/O | read、write、ioctl（方向、割り込み） |
| **i2c** | I2C バスマスタ | read、write、ioctl（周波数、アドレス） |
| **spi** | SPI バスマスタ | read、write、ioctl（速度、モード） |
| **adc** | アナログ・デジタル変換 | read、ioctl（チャネル、サンプリング速度） |
| **timer** | ハードウェア タイマー | read、write、ioctl（周波数、コールバック） |

---

## 4. ドライバ登録

### 4.1 関数ポインタ API

ドライバはコールバック関数で HAL に登録：

```cpp
typedef struct {
  int (*open_fn)(const char* uri, int flags);
  int (*close_fn)(int fd);
  ssize_t (*read_fn)(int fd, void* buf, size_t count);
  ssize_t (*write_fn)(int fd, const void* buf, size_t count);
  off_t (*seek_fn)(int fd, off_t offset, int whence);
  int (*ioctl_fn)(int fd, uint32_t cmd, void* arg);
} hal_device_driver_t;

class hal_registry {
 public:
  int register_device(const char* uri_pattern, const hal_device_driver_t& driver);
  int unregister_device(const char* uri_pattern);
};
```

### 4.2 ドライバ実装例

```cpp
// platform/zephyr/uart_driver.cpp

int uart_open(const char* uri, int flags) {
  int uart_index = parse_uart_index(uri);
  return zephyr_uart_open(uart_index, flags);
}

ssize_t uart_read(int fd, void* buf, size_t count) {
  return zephyr_uart_read(fd, buf, count);
}

ssize_t uart_write(int fd, const void* buf, size_t count) {
  return zephyr_uart_write(fd, buf, count);
}

int uart_ioctl(int fd, uint32_t cmd, void* arg) {
  if (cmd == TIOCBAUD) {
    uint32_t baud = *(uint32_t*)arg;
    return zephyr_uart_set_baud(fd, baud);
  }
  return -EINVAL;
}

const hal_device_driver_t UART_DRIVER = {
  .open_fn = uart_open,
  .close_fn = NULL,
  .read_fn = uart_read,
  .write_fn = uart_write,
  .seek_fn = NULL,
  .ioctl_fn = uart_ioctl,
};

void uart_driver_init() {
  hal::register_device("device://uart/*", UART_DRIVER);
}
```

### 4.3 デバイスツリー不要

重要な設計判断：Fireball はデバイスツリー（DT）を使用しません：

1. ドライバはシステム バイナリにコンパイル
2. デバイス登録は初期化時に実施
3. 各ボードはプラットフォーム固有コードでデバイスマップ定義

メリット：ランタイム オーバーヘッド ゼロ、完全静的デバイス設定。

---

## 5. 一般的なデバイス操作

### 5.1 UART

```c
int fd = open("device://uart/0", O_RDWR);
uint32_t baud = 115200;
ioctl(fd, TIOCBAUD, &baud);
write(fd, "Hello\n", 6);
char buf[256];
ssize_t n = read(fd, buf, sizeof(buf));
close(fd);
```

### 5.2 GPIO

```c
int fd = open("device://gpio/5", O_WRONLY);
uint32_t dir = GPIO_DIR_OUT;
ioctl(fd, GPIO_SET_DIR, &dir);
uint8_t value = 1;
write(fd, &value, 1);
uint8_t state;
read(fd, &state, 1);
close(fd);
```

### 5.3 I2C

```c
int fd = open("device://i2c/0", O_RDWR);
uint32_t freq = 400000;
ioctl(fd, I2C_SET_FREQ, &freq);
uint8_t addr = 0x50;
ioctl(fd, I2C_SET_ADDR, &addr);
uint8_t data[] = {0x00, 0xAA, 0xBB};
write(fd, data, sizeof(data));
uint8_t buf[16];
ssize_t n = read(fd, buf, sizeof(buf));
close(fd);
```

---

## 6. エラー処理

### 6.1 エラーコード

標準 POSIX errno 値：

| errno | 意味 |
|-------|--------|
| `ENODEV` | デバイス不在 |
| `EACCES` | アクセス拒否 |
| `EAGAIN` | リソース一時的利用不可（ノンブロッキング） |
| `EIO` | 入出力エラー |
| `EBADF` | 不正ファイル ディスクリプタ |
| `EINVAL` | 不正引数 |
| `ENOSPC` | デバイス領域不足 |
| `ETIMEDOUT` | 操作タイムアウト |

---

## 7. パフォーマンス特性

### 7.1 操作レイテンシ

| 操作 | レイテンシ | 備考 |
|-----------|---------|-------|
| **open** | <100 μs | デバイス登録検索、ストリーム割り当て |
| **close** | <50 μs | ストリーム クリーンアップ、リソース解放 |
| **read**（UART） | 500-2000 μs | ハードウェア依存、ブロッキング可能 |
| **write**（UART） | 500-2000 μs | ハードウェア依存、ブロッキング可能 |
| **write**（GPIO） | <50 μs | vOffloader 経由直接レジスタ書き込み |
| **ioctl** | <100 μs | コマンド固有（通常は高速） |

### 7.2 メモリ オーバーヘッド

| コンポーネント | サイズ | 備考 |
|-----------|------|-------|
| ファイルディスクリプタ テーブル | 32B × fd × 64 最大 | 典型値: 2KB |
| デバイス レジストリ | ~1KB | プラットフォーム依存 |
| ストリーム状態 | 16B × オープンストリーム | 典型値: 256B（16 ストリーム） |
| **合計** | **~3.5KB** | Phase 2 予算内 |

---

## 8. マルチストリーム例

```c
// アプリケーション A: UART 送信
int tx_fd = open("device://uart/0", O_WRONLY);
write(tx_fd, "APP_A sending data\n", 19);

// アプリケーション B: UART 受信
int rx_fd = open("device://uart/0", O_RDONLY);
char buf[256];
ssize_t n = read(rx_fd, buf, sizeof(buf));

// 両ストリームは独立：
// - 異なるファイルディスクリプタ
// - 独立バッファリング
// - 相互干渉なし
// - 同一ハードウェアへの同時アクセス可能

close(tx_fd);
close(rx_fd);
```

---

## 9. 実装チェックリスト

- [ ] ターゲット ハードウェア プラットフォーム定義（ARM、RISC-V）
- [ ] UART ドライバ実装（マルチストリーム対応）
- [ ] GPIO ドライバ実装（方向・割り込み対応）
- [ ] I2C ドライバ実装（ターゲット対応時）
- [ ] SPI ドライバ実装（ターゲット対応時）
- [ ] ADC ドライバ実装（ターゲット対応時）
- [ ] ファイルディスクリプタ テーブルと ストリーム管理
- [ ] デバイス レジストリとルーティング ロジック
- [ ] エラー処理と errno スレッドローカル ストレージ
- [ ] プラットフォーム固有 hal_registry 初期化
- [ ] 全デバイス・全操作の HAL テスト
- [ ] 各デバイスタイプのサンプル アプリケーション

---

## 10. Interrupt Service Routine (ISR) Safety

### 10.1 ISR 安全性確保のための設計

Fireball HALでは、ISR（割り込みサービスルーチン）内での直接的なHAL API呼び出しを原則として行いません。代わりに、以下のメカニズムでISRからの安全なイベント処理を実現します。

**設計原則：**

- **フラグベースの通知**: HALレイヤーのドライバまたはハードウェア固有のISRは、イベント発生時にグローバルな割り込みフラグ（または特定のビット）をセットします。
- **インタープリタによる監視**: インタープリタ（ゲストの実行環境）は、定期的にこの割り込みフラグを監視します。これはゲストのコンテキストスイッチ時や特定の命令実行前に行われる可能性があります。
- **コンテキストスイッチ時のチェック**: ゲストのコンテキストがスイッチされる際、ランタイムはこのフラグをチェックし、割り込みが保留中であれば適切なアクション（例: イベントキューへの追加、ゲストへの通知）を実行します。
- **ISR実行時間の最小化**: ISR自体はフラグをセットするなどの最小限の処理に留め、複雑なロジックやHAL API呼び出しによるデッドロック・競合状態を避けます。

**メリット：**

- **シンプルさ**: ロック機構や複雑なISRセーフなAPIを導入する必要がないため、カーネルの複雑性が低減されます。
- **低レイテンシISR**: ISRの実行時間が極めて短くなり、割り込み応答性が向上します。
- **決定論的動作**: 割り込み処理がゲストの実行フローと明確に分離されるため、システムの決定論的動作を維持しやすくなります。

```cpp
// 抽象的なHALドライバのISR例
extern volatile uint32_t fireball_isr_flags; // グローバル割り込みフラグ

void hal_timer_isr() {
    // 最小限の処理
    clear_timer_interrupt_source();
    fireball_isr_flags |= (1 << TIMER_INTERRUPT_BIT); // フラグをセット
}

// インタープリタ/ランタイム側の処理例
void fireball_runtime_context_switch() {
    if (fireball_isr_flags & (1 << TIMER_INTERRUPT_BIT)) {
        // 割り込みフラグがセットされている場合
        fireball_isr_flags &= ~(1 << TIMER_INTERRUPT_BIT); // フラグをクリア
        // 適切なゲストイベントをトリガー、またはスケジューラに通知
        coos_kernel_notify_event(COOS_EVENT_TIMER);
    }
    // 通常のコンテキストスイッチ処理を続行
}
```

## まとめ

Fireball HAL は、6 つのシステムコール（`open`、`close`、`read`、`write`、`seek`、`ioctl`）で、すべてのデバイスタイプを統一的にアクセスする**最小限のストリームベース インターフェース**を提供します。マルチストリーム対応と関数ポインタ登録により：

- **シンプル**: デバイスタイプごとの複雑な API 不要
- **柔軟**: 新規デバイスタイプ追加容易
- **並行性**: 複数アプリが同一デバイスを安全にアクセス可能
- **移植性**: API は全プラットフォーム共通
