# HAL & Virtual SoC Design

**Version:** 0.1.0
**Date:** 2025-11-28
**Author:** Takuya Matsunaga

---

## 概要（Overview）

**概要：** Fireball HAL（Hardware Abstraction Layer）は、WASM ゲストコードがハードウェアデバイスにアクセスする際の**プラットフォーム非依存インターフェース**を提供します。

**2 層の抽象化モデル：**

1. **Virtual SoC（プラットフォーム非依存）**: UART、GPIO、I2C、SPI、Timer などの標準的なデバイスタイプの統一インターフェース定義。WASM ゲストは、実際のハードウェアの詳細を知らずに、このインターフェース経由でアクセス
2. **HAL Backend（プラットフォーム固有）**: Virtual SoC API を実装する、Zephyr RTOS、ベアメタル、FreeRTOS など異なるプラットフォーム向けのドライバ実装

**利点：**
- **ポータビリティ**: WASM ゲストコードが異なるハードウェア間で変更なしで動作
- **分離**: Virtual SoC API を変更しない限り、ドライバ実装の変更は上位層に波及しない
- **テスト性**: テスト用に模擬（mock）実装を提供できる

---

## 2. HAL アーキテクチャ（HAL Architecture）

### 2.1 システムフロー（System Flow）

**概要：** WASM ゲストが `write(1, buffer, len)` を呼び出したとき、以下のフローで処理されます：

```
┌─────────────────────────────────────────────────────────────┐
│                  WebAssembly Guest Module                    │
│  WASM syscalls (read, write, ioctl)                         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Fireball HAL                              │
│  Device URI Router (device://uart/0, device://gpio/5, etc.) │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Zephyr RTOS / Bare Metal / FreeRTOS            │
│  UART, GPIO, I2C, SPI, Timer drivers                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Virtual SoC デバイスタイプ（Virtual SoC Device Types）

**概要：** Virtual SoC は、組み込みシステムの標準デバイス 5 つを定義します。各デバイスは URI ベースで識別され、統一インターフェースで操作されます。

**標準 Virtual SoC デバイス：**

| デバイス | URI パターン | 主要操作 | 例 |
|---------|------------|--------|-----|
| **UART** | `device://uart/N` | open, close, read, write, set_config（ボーレート） | `/dev/uart/0` |
| **GPIO** | `device://gpio/N` | set（出力）、get（入力）、set_mode（I/O 設定）、set_interrupt（割り込み） | `/dev/gpio/5` |
| **I2C** | `device://i2c/N` | read、write、set_frequency（SCL クロック） | `/dev/i2c/0` |
| **SPI** | `device://spi/N` | read、write、set_frequency（SCLK）、set_mode | `/dev/spi/0` |
| **Timer** | `device://timer/N` | start、stop、set_callback（割り込みハンドラー） | `/dev/timer/0` |

**特徴：**
- **URI ベース**: `device://type/index` 形式で、全デバイスの命名が統一
- **操作の統一化**: 異なるデバイスタイプでも、read/write などの基本操作は共通パターン
- **プラットフォーム非依存**: UART は ESP32、STM32、RISC-V で同じ API

### 2.3 デバイスインターフェース（Device Interface - HAL API）

**概要：** HAL API は、すべてのデバイス操作を統一インターフェースで表現します。operation パラメータにより、同じ構造体で異なるデバイス操作（read、write、set_config など）を表現できます。

**設計上の特徴：**
- **統一構造体**: `hal_device_io` で全操作を表現（ダック型）
- **operation パラメータ**: "read"、"write"、"set_config" など、文字列で操作を指定
- **型付きKey-Value形式 ペイロード**: 操作固有のパラメータは key-value マップで表現
- **レスポンス**: 同じ構造体で結果を返し、戻り値や状態を含む

**HAL API インターフェース：**

```cpp
namespace fireball { namespace services {

typedef struct  {
  uint32_t device_id;                          // デバイス ID またはハンドル
  std::string_view operation;                  // "read", "write", "set_config" など
  ipc::value_map parameters;                   // 操作固有のパラメータ
} hal_device_io_t;

// 戻り値も同じ構造体：
typedef struct {  // レスポンス用
  uint32_t status;                             // ステータスコード
  ipc::value_map result;                       // 結果データ（bytes_read、success など）
} hal_device_io_t;

class hal_service {
 public:
  virtual ~hal_service() = default;

  // Device I/O operations
  virtual ipc::value_map query(const hal_device_io_t& req) = 0;
  virtual ipc::value_map mutate(const hal_device_io_t& req) = 0;

  // List available devices
  virtual std::vector<std::string> list_devices() const = 0;

  // Device registration (platform-specific)
  virtual void register_device(const std::string& uri,
                               hal_device_handler_t handler) = 0;
};

} } // namespace fireball { namespace services
```

---

## 3. デバイスルーティング（Device Routing）

デバイスルーターは、デバイス URI に基づいてリクエストを適切なハンドラに振り分けます。

### 3.1 静的デバイスマップ（Static Device Map）

デバイスはコンパイル時に静的テーブルで登録されます：

```cpp
// platform/zephyr/hal_device_map.cpp

static const hal_device_registration_t DEVICE_MAP[] = {
  { "device://uart/0", uart_handler },
  { "device://uart/1", uart_handler },
  { "device://gpio/*", gpio_handler },
  { "device://i2c/0",  i2c_handler },
  { nullptr, nullptr }  // ターミネーター
};

extern const hal_device_registration_t* get_device_map() {
  return DEVICE_MAP;
}
```

### 3.2 ルーター実装（Router Implementation）

```cpp
class hal_router {
 private:
  const hal_device_registration_t* device_map_;

 public:
  explicit hal_router(const hal_device_registration* map)
      : device_map_(map) {}

  ipc::value_map dispatch(const std::string& device_uri,
                          const std::string& operation,
                          const ipc::value_map& args) {
    // リニアサーチ（初期化時のみ、ホットパスでのループコストなし）
    for (const auto* entry = device_map_; entry && entry->handler; ++entry) {
      if (uri_matches(device_uri, entry->uri_pattern)) {
        return entry->handler(operation, args);
      }
    }

    // デバイスが見つからない
    ipc::value_map error;
    error["error"] = ipc::value("Device not found");
    return error;
  }

 private:
  static bool uri_matches(const std::string_view device,
                         const std::string_view pattern) {
    // 完全一致またはワイルドカード一致
    if (pattern.back() == '*') {
      return device.compare(0, pattern.size() - 1, pattern.substr(0, pattern.size() - 1)) == 0;
    }
    return device == pattern;
  }
};
```

---

## 4. プラットフォーム固有実装（Platform-Specific Backend）

### 4.1 Zephyr 実装（Zephyr Implementation）

**ロケーション：** `platform/zephyr/`

#### UART ハンドラ例（UART Handler Example）

```cpp
// platform/zephyr/hal_uart.cpp

#include <zephyr/drivers/uart.h>

static ipc::value_map uart_handler(const std::string& operation,
                                   const ipc::value_map& args) {
  static const device_t *uart_dev = device_get_binding("UART_0");

  ipc::value_map response;

  if (operation == "query_status") {
    // Query UART status
    response["is_open"] = ipc::value(true);
    response["bytes_sent"] = ipc::value(1024);
    response["bytes_received"] = ipc::value(512);
    response["has_error"] = ipc::value(false);
    return response;
  }
  else if (operation == "query_config") {
    // Query UART configuration
    response["baudrate"] = ipc::value(115200);
    response["parity"] = ipc::value("none");
    response["databits"] = ipc::value(8);
    response["stopbits"] = ipc::value(1);
    return response;
  }
  else if (operation == "mutate_config") {
    // Change UART configuration
    uint32_t baudrate = args.at("baudrate").as_int();
    std::string parity = args.at("parity").as_string();

    struct uart_config cfg = {
      .baudrate = baudrate,
      .parity = parity_from_string(parity),
      .stop_bits = UART_CFG_STOP_BITS_1,
      .data_bits = UART_CFG_DATA_BITS_8,
      .flow_ctrl = UART_CFG_FLOW_CTRL_NONE
    };

    int ret = uart_configure(uart_dev, &cfg);
    response["success"] = ipc::value(ret == 0);
    return response;
  }
  else if (operation == "mutate_write") {
    // Write data to UART
    auto data = args.at("data").as_bytes();
    for (uint8_t byte : data) {
      uart_poll_out(uart_dev, byte);
    }
    response["bytes_written"] = ipc::value((int32_t)data.size());
    return response;
  }
  else if (operation == "query_read") {
    // Read data from UART (non-blocking)
    uint8_t buffer[256];
    int len = 0;
    int c;
    while ((c = uart_poll_in(uart_dev, &buffer[len])) == 0 && len < 256) {
      len++;
    }
    response["data"] = ipc::value(std::span<uint8_t>(buffer, len));
    response["bytes_read"] = ipc::value(len);
    return response;
  }

  response["error"] = ipc::value("Unknown operation");
  return response;
}
```

#### GPIO ハンドラ例（GPIO Handler Example）

```cpp
// platform/zephyr/hal_gpio.cpp

#include <zephyr/drivers/gpio.h>

static ipc::value_map gpio_handler(const std::string& operation,
                                   const ipc::value_map& args) {
  static const device_t *gpio_dev = device_get_binding("GPIO_0");

  ipc::value_map response;

  if (operation == "query_status") {
    // GPIO ピンの状態を読み取り
    uint32_t pin = args.at("pin").as_int();
    int value = gpio_pin_get(gpio_dev, pin);
    response["pin"] = ipc::value((int32_t)pin);
    response["value"] = ipc::value((int32_t)value);
    return response;
  }
  else if (operation == "mutate_set") {
    // GPIO ピンレベルを設定
    uint32_t pin = args.at("pin").as_int();
    uint32_t value = args.at("value").as_int();
    int ret = gpio_pin_set(gpio_dev, pin, value);
    response["success"] = ipc::value(ret == 0);
    return response;
  }
  else if (operation == "mutate_configure") {
    // GPIO ピンを設定
    uint32_t pin = args.at("pin").as_int();
    std::string mode = args.at("mode").as_string();  // "input", "output"

    gpio_flags_t flags = (mode == "input") ? GPIO_INPUT : GPIO_OUTPUT;
    int ret = gpio_pin_configure(gpio_dev, pin, flags);
    response["success"] = ipc::value(ret == 0);
    return response;
  }

  response["error"] = ipc::value("Unknown operation");
  return response;
}
```

### 4.2 ベアメタル実装（Bare Metal Implementation）

RTOS なしのプラットフォームでは、レジスタに直接アクセスします：

```cpp
// platform/stm32l0/hal_uart.cpp

static ipc::value_map uart_handler(const std::string& operation,
                                   const ipc::value_map& args) {
  ipc::value_map response;

  if (operation == "mutate_write") {
    auto data = args.at("data").as_bytes();
    for (uint8_t byte : data) {
      // 直接レジスタ書き込み（STM32L0 UART）
      while (!(USART1->SR & USART_SR_TXE));
      USART1->DR = byte;
    }
    response["bytes_written"] = ipc::value((int32_t)data.size());
  }
  // ... その他の操作

  return response;
}
```

---

## 5. メッセージプロトコル（Message Protocol - 型付きKey-Value形式）

HAL は 型付きKey-Value形式 形式で通信します。詳細は [ipc-protocol.md](ipc-protocol.md) を参照：

### クエリメッセージ（Query Message）

```
メタ: {version=1, type=hal_query, id=101, flags=query}
ペイロード: {
  "device": "device://uart/0",
  "operation": "query_status"
}
```

### ミューテートメッセージ（Mutate Message）

```
メタ: {version=1, type=hal_mutate, id=102, flags=mutate}
ペイロード: {
  "device": "device://uart/0",
  "operation": "mutate_write",
  "data": [0x48, 0x65, 0x6c, 0x6c, 0x6f]  // "Hello"
}
```

---

## 6. WASM との統合（Integration with WASM）

### 6.1 WASM システムコール バインディング（WASM Syscall Binding）

WASM ゲストの呼び出しは HAL 操作にマップされます：

```cpp
// src/wasm/wasm_imports.cpp

int32_t wasm_syscall_write(uint32_t fd, uint32_t buf_ptr, uint32_t len) {
  // WASM 線形メモリポインタをホストバッファにマップ
  uint8_t* buffer = get_current_module_memory() + buf_ptr;

  // fd を Virtual SoC デバイスにマップ
  std::string device_uri = fd_to_device_uri(fd);

  // HAL メッセージを構築
  ipc::value_map args;
  args["data"] = ipc::value(std::span<uint8_t>(buffer, len));

  // HAL サービスを呼び出し
  ipc::value_map result = hal_service->mutate(device_uri, "mutate_write", args);

  // 書き込まれたバイト数を返す
  return result["bytes_written"].as_int();
}
```

### 6.2 ファイルディスクリプタマッピング（File Descriptor Mapping）

```cpp
// WASM fd を Virtual SoC デバイスにマップ
static const struct {
  int wasm_fd;
  std::string_view device_uri;
} FD_MAP[] = {
  { 0, "device://uart/0" },  // stdin
  { 1, "device://uart/0" },  // stdout
  { 2, "device://uart/0" },  // stderr
  { 3, "device://gpio/5" },
  { 4, "device://i2c/0" },
};

std::string fd_to_device_uri(int fd) {
  for (const auto& entry : FD_MAP) {
    if (entry.wasm_fd == fd) {
      return std::string(entry.device_uri);
    }
  }
  return "";  // 無効な fd
}
```

---

## 7. 設計制約（Design Constraints）

1. **静的構成**: すべてのデバイスはコンパイル時に定義
2. **動的割り当てなし**: デバイスマップは静的配列
3. **直接レジスタアクセス**: 適用可能な場合（ベアメタル）
4. **型付きKey-Value形式 プロトコル**: IPC レイヤーと一貫性
5. **ブロッキング操作**: すべての HAL 呼び出しはブロッキング

---

## 8. パフォーマンス考慮事項（Performance Considerations）

### デバイスアクセスコスト（Device Access Cost）

```
WASM syscall → WASM import binding → HAL router dispatch → Device handler
              (~10 命令)    (~50 命令)    (~可変)
```

### 最適化戦略（Optimization Strategy）

- 静的デバイスマップ（ハッシュテーブルオーバーヘッドなし）
- リニアサーチは初期化時のみ
- 直接ハンドラー呼び出し（間接参照なし）
- 型付きKey-Value形式 span 経由のゼロコピーバッファアクセス

---

**詳細な IPC プロトコルについては [ipc-protocol.md](ipc-protocol.md) を参照**
