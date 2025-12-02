# Fireball Debugging Strategy

**Version:** 0.1.0
**Date:** 2025-11-28
**Author:** Takuya Matsunaga

---

## Overview

**概要：** Fireball debugger サブシステム（Phase 3 で実装予定）は、GDB Remote Protocol をサポートし、VSCode、GDB、lldb などの標準的なデバッガと統合します。これにより、WASM ゲストコード、COOS コルーチン、システムサブシステムをステップ実行したり、ブレークポイントを設定したりできるようになります。

**デバッグの 3 つのレベル：**

1. **WASM ゲストレベル**: 個々の WASM 命令のステップ実行、ローカル変数の監視
2. **コルーチンレベル**: コルーチン間のコンテキストスイッチ、チャネル操作の追跡
3. **システムレベル**: メモリ割り当て、サービス通信の可視化

---

## 1. GDB Remote Protocol Support

### 概要（Overview）

GDB Remote Protocol（RSP）は、デバッガとデバッグターゲットの標準的な通信プロトコルです。Fireball デバッガサブシステムは RSP サーバーとして動作し、以下の機能を提供します：

- **ブレークポイント管理**: 最大 5 個のブレークポイント設定・削除
- **ステップ実行**: 1 WASM 命令単位での実行制御
- **レジスタ読み取り**: コルーチンレジスタ状態（PC、ローカル変数、スタック）
- **メモリアクセス**: WASM 線形メモリの読み取り・書き込み（バウンダリチェック付き）
- **スレッド情報**: コルーチン一覧、現在のコルーチン ID 取得
- **IDE 統合**: VSCode、GDB、lldb との通信

### サポートされる RSP コマンド

| コマンド | 説明 | 実装状態 |
|--------|------|---------|
| `g` | レジスタ読み取り | ✅ Phase 3 |
| `G` | レジスタ書き込み | ⚠️ Future |
| `m` | メモリ読み取り | ✅ Phase 3 |
| `M` | メモリ書き込み | ⚠️ Future |
| `z/Z` | ブレークポイント設定・削除 | ✅ Phase 3 |
| `c` | 実行継続 | ✅ Phase 3 |
| `s` | ステップ実行 | ✅ Phase 3 |
| `H` | スレッド選択 | ✅ Phase 3 |
| `?` | 停止理由取得 | ✅ Phase 3 |
| `qfThreadInfo` | スレッド一覧 | ✅ Phase 3 |
| `qC` | 現在のスレッド | ✅ Phase 3 |

### RSP 通信層（Transport Layer）

**概要：** RSP はプロトコルレベルの仕様のみ定義し、物理的な通信経路は実装に任されています。Fireball では、以下の 3 つの通信方法をサポートします：

#### 方式 1: Serial Port (UART) - 推奨

**特徴：**
- 標準的な組み込みシステムの選択
- ハードウェア: UART（ボーレート 115200、8N1）
- 物理層: RS232、USB-Serial、など

**フロー：**
```
┌──────────────────────┐
│ VSCode/GDB (Host)    │
│ RSP クライアント     │
└──────────┬───────────┘
           │ TCP localhost:3333
           ▼
┌──────────────────────┐
│ Fireball Debugger    │
│ RSP サーバー         │
└──────────┬───────────┘
           │
        UART (/dev/ttyUSB0)
           │
┌──────────▼───────────┐
│ Hardware (STM32等)   │
└──────────────────────┘
```

**実装例：**

```cpp
// Debugger が RSP コマンドを UART から受信
class debugger_uart_backend {
 private:
  int uart_fd_;
  std::vector<uint8_t> rx_buffer_;

 public:
  // UART からデータ受信（RSP フォーマット）
  std::optional<std::string> read_rsp_command() {
    uint8_t c = uart_read_byte();  // ブロッキング読み込み

    // RSP はパケット形式： $<data>#<checksum>
    if (c == '$') {
      std::string cmd;
      while (true) {
        c = uart_read_byte();
        if (c == '#') {
          // チェックサム読み込み
          uint8_t cksum_hi = uart_read_byte();
          uint8_t cksum_lo = uart_read_byte();
          return cmd;
        }
        cmd += (char)c;
      }
    }
    return std::nullopt;
  }

  // RSP レスポンス送信
  void send_rsp_response(const std::string& data) {
    // フォーマット: $<data>#<checksum>
    uint8_t cksum = calculate_checksum(data);
    uart_printf("$%s#%02x", data.c_str(), cksum);
  }
};
```

**プラットフォーム別ボーレート設定：**

| プラットフォーム | ボーレート | 注釈 |
|------------|--------|------|
| WCH CH32V307 | 115200 | デフォルト |
| nRF52840 | 115200 | Zephyr デフォルト |
| STM32F4 | 115200 | 互換性保証 |

#### 方式 2: stdin/stdout - デバッグのみ（非推奨）

**特徴：**
- ホスト PC での開発・テスト用
- UART ハードウェアが不要
- パフォーマンスは低い（遅延が大きい）

**用途：**
- Simulator (x86/POSIX) での単体テスト
- CI/CD パイプライン

**実装例：**

```cpp
class debugger_stdio_backend {
 public:
  std::optional<std::string> read_rsp_command() {
    std::string cmd;
    char c;
    // stdin からパケット受信
    while (std::cin.get(c)) {
      if (c == '$') {
        while (std::cin.get(c) && c != '#') {
          cmd += c;
        }
        // チェックサム破棄（単体テスト用）
        std::cin.ignore(3);  // #XX を読み飛ばし
        return cmd;
      }
    }
    return std::nullopt;
  }

  void send_rsp_response(const std::string& data) {
    uint8_t cksum = calculate_checksum(data);
    std::cout << "$" << data << "#" << std::hex << (int)cksum << std::endl;
  }
};
```

**制限：**
- ボーレート無制限（TCP/IP over USB）
- ローカルホストのみ（Network 接続不可）
- デバッグ・テスト専用

#### 方式 3: TCP/IP - 将来対応

**特徴：**
- Ethernet / Wi-Fi 経由のリモートデバッグ
- 高帯域幅
- ネットワークデバイスが必須

**計画：**
- Phase 3 では未実装
- 将来の拡張予定（Nordic nRF + Thread/Wi-Fi）

### RSP パケットフォーマット（Packet Format）

RSP の基本パケット構造：

```
┌─────────┬──────────────┬─────┬───────────┐
│ '$'     │   <data>     │ '#' │ <checksum>│
├─────────┼──────────────┼─────┼───────────┤
│ 開始    │ コマンド/    │ 区切 │ チェック │
│ マーク  │ レスポンス   │     │ サム      │
└─────────┴──────────────┴─────┴───────────┘

チェックサム = sum(<data>) & 0xFF (16進数表記)
```

**例：**

```
送信 (Host → Target):
$m400,10#2a

分解:
  $ : 開始マーク
  m : コマンド (メモリ読み取り)
  400,10 : パラメータ（アドレス 0x400、サイズ 16 バイト）
  # : チェックサム区切り
  2a : チェックサム

受信 (Target → Host):
$48656c6c6f20576f726c64212021#56

分解:
  $ : 開始マーク
  48656c6c6f... : レスポンス（16進数エンコード）
  # : チェックサム区切り
  56 : チェックサム
```

### デバッガバックエンド選択

**ビルド時フラグ：**

```cmake
# UART backend (組み込みシステム)
target_compile_definitions(debugger PRIVATE DEBUGGER_BACKEND_UART)

# stdio backend (シミュレーター)
target_compile_definitions(debugger PRIVATE DEBUGGER_BACKEND_STDIO)

# TCP/IP backend (将来)
# target_compile_definitions(debugger PRIVATE DEBUGGER_BACKEND_TCP)
```

**ランタイム選択：**

```cpp
class debugger_service {
 private:
  std::unique_ptr<debugger_backend> backend_;

 public:
  explicit debugger_service(debugger_backend_type type) {
    switch (type) {
      case debugger_backend_type::UART:
        backend_ = std::make_unique<debugger_uart_backend>();
        break;
      case debugger_backend_type::STDIO:
        backend_ = std::make_unique<debugger_stdio_backend>();
        break;
      default:
        backend_ = nullptr;
    }
  }

  // コマンド受信・処理・送信
  void event_loop() {
    while (true) {
      auto cmd = backend_->read_rsp_command();
      if (!cmd) continue;

      auto response = process_command(*cmd);
      backend_->send_rsp_response(response);
    }
  }
};
```

---

## 2. ブレークポイント管理（Breakpoint Management）

### ブレークポイントタイプ（Breakpoint Types）

```cpp
enum class breakpoint_type {
  INSTRUCTION,      // WASM 命令ポインタ上
  MEMORY_READ,      // メモリ読み取りアクセス
  MEMORY_WRITE,     // メモリ書き込みアクセス
  SYSCALL,          // システムコール実行時
};

struct breakpoint {
  uint32_t id;
  uint32_t address;           // コード中のオフセット
  breakpoint_type type;
  bool enabled;
  uint32_t hit_count;         // ヒット回数（条件付きブレークポイント用）
};
```

### ブレークポイント設定例（Setting Breakpoints）

```cpp
// GDB RSP: Z0,1024,1 (命令ブレークポイント、アドレス 0x400)
debugger_service->add_breakpoint(
  breakpoint_type::INSTRUCTION,
  0x400,
  true  // enabled
);

// GDB RSP: z0,1024,1 (ブレークポイント削除)
debugger_service->remove_breakpoint(0x400);

// リスト表示
auto bps = debugger_service->list_breakpoints();
for (const auto& bp : bps) {
  std::cout << "BP[" << bp.id << "]: addr=0x" << std::hex << bp.address
            << " type=" << (int)bp.type << " hits=" << bp.hit_count << std::endl;
}
```

### 実装（Implementation）

```cpp
namespace fireball { namespace services {

class debugger_service {
 private:
  std::map<uint32_t, breakpoint> breakpoints_;
  uint32_t next_bp_id_ = 1;

 public:
  // ブレークポイント追加
  uint32_t add_breakpoint(uint32_t address, breakpoint_type type);

  // ブレークポイント削除
  void remove_breakpoint(uint32_t address);

  // ブレークポイント有効化/無効化
  void enable_breakpoint(uint32_t address, bool enabled);

  // ブレークポイント一覧取得
  std::vector<breakpoint> list_breakpoints() const;

  // ブレークポイントチェック（命令実行時）
  bool check_breakpoint(uint32_t pc);
};

} } // namespace fireball { namespace services
```

---

## 3. レジスタマップ（Register Map）

Fireball は WASM の実行状態を以下のレジスタセットで表現します：

| レジスタ | ID | 内容 | 用途 |
|---------|----|----|------|
| `pc` | 0 | Program Counter | コード位置（バイトオフセット） |
| `fp` | 1 | Frame Pointer | コルーチンフレーム位置 |
| `sp` | 2 | Stack Pointer | スタック頂点 |
| `locals[0-15]` | 3-18 | ローカル変数 | 現在のコルーチン内のローカル |
| `memory_base` | 19 | メモリベースアドレス | WASM 線形メモリの開始位置 |
| `memory_size` | 20 | メモリサイズ | WASM 線形メモリのサイズ |
| `coro_id` | 21 | コルーチン ID | 現在のコルーチン識別子 |
| `status` | 22 | ステータス | 0=running, 1=blocked, 2=suspended |

### レジスタ読み取り実装（Register Read）

```cpp
void debugger_service::read_registers(uint32_t coro_id, register_set& regs) {
  auto coro = scheduler->get_coroutine(coro_id);
  if (!coro) return;

  regs.pc = coro->frame->pc;
  regs.fp = coro->frame->frame_ptr;
  regs.sp = coro->stack_ptr;

  // ローカル変数を読み取り
  for (int i = 0; i < 16 && i < coro->frame->locals_count; ++i) {
    regs.locals[i] = coro->frame->locals[i];
  }

  regs.memory_base = coro->module->linear_memory;
  regs.memory_size = coro->module->memory_size;
  regs.coro_id = coro_id;
  regs.status = (uint32_t)coro->state;
}
```

---

## 4. メモリアクセス制御（Memory Access Control）

### アクセス検証（Access Validation）

```cpp
bool debugger_service::validate_memory_access(
  uint32_t coro_id,
  uint32_t address,
  uint32_t size,
  bool write) {
  auto coro = scheduler->get_coroutine(coro_id);
  if (!coro) return false;

  uint32_t mem_base = coro->module->linear_memory;
  uint32_t mem_size = coro->module->memory_size;

  // バウンダリチェック
  if (address + size > mem_base + mem_size) {
    return false;  // メモリ範囲外
  }

  // 将来: パーミッションチェック
  // if (write && !has_write_permission(coro_id, address)) return false;

  return true;
}
```

### メモリ読み取り例（Memory Read）

```cpp
// GDB RSP: m10,10 (アドレス 0x10 から 16 バイト読み取り)
std::vector<uint8_t> data = debugger_service->read_memory(
  current_coro_id,
  0x10,
  16
);

// GDB RSP レスポンス: "48656c6c6f..." (16 進数エンコード)
```

---

## 5. ステップ実行（Stepping）

### ステップタイプ（Step Types）

```cpp
enum class step_type {
  INSTRUCTION,     // 1 WASM 命令実行
  OVER,            // 関数呼び出しを 1 ステップ
  INTO,            // 関数内へステップイン
  OUT,             // 関数から抜ける
};

// WASM インタプリタメインループ内で処理
void execute_with_step() {
  if (debugger->is_stepping()) {
    execute_one_instruction();
    debugger->handle_step();  // デバッガに通知
  } else {
    // 通常実行
  }
}
```

### GDB RSP ステップコマンド

```
s          # ステップイン（1 命令）
s:1        # コルーチン 1 のステップ
n          # ステップオーバー
c          # 実行継続
```

---

## 6. VSCode 統合（VSCode Integration）

### launch.json 設定例

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Debug Fireball",
      "type": "gdb",
      "request": "launch",
      "program": "${workspaceFolder}/build/fireball-simulator",
      "args": [],
      "stopAtEntry": true,
      "cwd": "${workspaceFolder}",
      "externalConsole": false,
      "MIMode": "gdb",
      "miDebuggerPath": "/usr/bin/gdb",
      "setupCommands": [
        {
          "description": "Enable pretty-printing for gdb",
          "text": "-enable-pretty-printing",
          "ignoreFailures": true
        }
      ],
      "preLaunchTask": "build"
    }
  ]
}
```

### Python Pretty Printer（Fireball 構造体用）

```python
# ~/.gdbinit
python
import sys
sys.path.insert(0, '/path/to/fireball/tools/gdb')
from fireball_pretty_printers import register_printers
register_printers(gdb.current_objfile())
end

# Coroutine 構造体の可視化
class CoroutineType:
    def __init__(self, val):
        self.val = val
    def to_string(self):
        coro_id = self.val['id']
        state = self.val['state']
        return f"Coroutine#{coro_id} [{state}]"
```

---

## 7. デバッグ情報の可視化（Debug Information Visualization）

### デバッグコンソール出力例

```
(gdb) info threads
  Id   Target Id         Frame
* 1    Coro#0            0x00001024 in main ()
  2    Coro#1            0x00002048 in producer_task ()
  3    Coro#2            [blocked on channel]

(gdb) info locals
id = 42
buffer = 0x20001000 "Hello, Fireball!"
remaining = 16

(gdb) x/16bx $sp
0x2000ff00:  0x01  0x02  0x03  0x04  0x05  0x06  0x07  0x08
0x2000ff08:  0x09  0x0a  0x0b  0x0c  0x0d  0x0e  0x0f  0x10

(gdb) break *0x1024
Breakpoint 1 at 0x1024

(gdb) continue
Breakpoint 1, 0x00001024 in main ()

(gdb) step
0x00001028 in main ()
```

---

## 8. セキュリティ考慮事項（Security Considerations）

### アクセス制限（Access Restrictions）

- **ホストメモリ保護**: WASM 線形メモリ外へのアクセスを禁止
- **パーミッションチェック**: 将来的にはデバッガ実行権限の検証
- **監査ログ**: すべてのデバッグ操作をログ記録

### 実装例（Implementation）

```cpp
// デバッグ操作を監査ログに記録
void debugger_service::log_debug_operation(const std::string& op) {
  logger_service->log({
    .level = log_level::DEBUG,
    .message = "DEBUG_OP: " + op,
    .timestamp = get_timestamp()
  });
}
```

---

## 9. ディレクトリ構造（Directory Structure）

```
fireball/
├── inc/
│   └── services/
│       └── debugger.hxx        # Debugger API
├── src/
│   └── services/debugger/
│       ├── debugger.cpp        # Debugger implementation
│       ├── gdb_rsp.cpp         # GDB Remote Protocol parser
│       ├── breakpoint.cpp      # Breakpoint management
│       └── register.cpp        # Register map
├── tools/
│   └── gdb/
│       └── fireball_pretty_printers.py
└── docs/
    └── debugging.md
```

---

## 10. Phase 3 実装タスク

- [ ] GDB Remote Protocol パーサー実装
- [ ] ブレークポイント管理（追加・削除・有効化）
- [ ] レジスタスナップショット機構
- [ ] メモリアクセス制御（バウンダリチェック）
- [ ] UART 通信層（GDB プロトコル送受信）
- [ ] VSCode GDB 統合テスト
- [ ] Python Pretty Printer 実装

---

## 11. 将来の拡張（Future Enhancements）

- **ウォッチポイント**: メモリ値の変更を監視
- **条件付きブレークポイント**: 特定条件で一時停止
- **デバッグスクリプト**: GDB スクリプト言語対応
- **リモートデバッグ**: ネットワーク経由のデバッグ
- **パフォーマンスプロファイリング**: ホットパス検出、CPU 使用率計測

---

## まとめ（Summary）

Fireball デバッガサブシステムは、GDB Remote Protocol を通じて標準的なデバッグツール（VSCode、GDB、lldb）と統合します。これにより、WASM ゲストコードとコルーチンの挙動を可視化し、効率的にデバッグできます。

セキュリティと性能を両立させながら、組み込みシステムのデバッグニーズに対応する設計です。
