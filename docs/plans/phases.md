# Fireball Implementation Roadmap

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga
**Target Platform:** STM32F401 (ARM Cortex-M4, 96KB SRAM, 256KB Flash)

---

## Overview

このドキュメントは、Fireball の **PoC（Proof of Concept）実装をPhase 0 からPhase 3 まで段階的に進めるためのロードマップ**です。

各フェーズでは、以下の目標を達成します：

1. **バイナリサイズ目標の検証** - 実装後に `arm-none-eabi-size` で測定
2. **メモリ予算の検証** - performance.md で定義されたRAM/ROMバジェットに収まることを確認
3. **機能の段階的拡張** - Phase 0 コアから Phase 3 完全実装まで

**基準条件（STM32F401）:**
- ROM: ≤ 100KB（Fireball Core、30% マージン含む）
- RAM: 63-80KB（固定 23.2KB + ゲスト 32-48KB + マージン 16-32KB）

---

## Phase 0: COOS Kernel Core

**目標:** COOS カーネルの最小実装（スケジューラー、チャネル、メモリ管理）

### コンポーネント

| コンポーネント | ファイル | 説明 | 目標サイズ |
|-------------|---------|------|----------|
| **co_sched** | `src/coos/co_sched.cpp` | コルーチンスケジューラー | 512B |
| **co_csp** | `src/coos/co_csp.cpp` | CSP チャネル実装 | 3.0KB |
| **co_mem** | `src/coos/co_mem.cpp` | dlmalloc ラッパー | 1.2KB |
| **co_value** | `src/coos/co_value.cpp` | 所有権追跡 | 1.0KB |
| **Promise & Task** | `src/coos/coroutine_promise.h` | コルーチンクラス実装 | - |

### 実装チェックリスト

#### co_sched: Ready Queue & Scheduling

- [ ] Ready Queue（リスト型、最大 8 コルーチン）実装
  - コルーチン ID (1-4 Bytes)
  - ハンドル保存（std::coroutine_handle）
  - タイムスタンプ追跡
- [ ] スケジューリングアルゴリズム
  - 非プリエンプティブ（協調型）
  - Round-robin (次の Ready コルーチンを実行)
- [ ] コルーチン作成・破棄
  - `spawn(std::function<void()>, co_mem*)`
  - `yield()` - コンテキストスイッチ
  - `join(uint32_t coro_id)` - 完了待機

#### co_csp: Channel & Wait Queues

- [ ] Channel struct（チャネルメタデータ）
  - Channel ID
  - 送受信ポーター (co_value<T>)
  - Wait queue（送信側・受信側各 1 個）
- [ ] Wait Queue 実装
  - 最大 4 コルーチンまで待機
  - FIFO 順序
- [ ] Channel operations
  - `ch_open()` - チャネル作成
  - `ch_send(int ch_id, co_value<T>)` - 送信
  - `ch_recv(int ch_id) -> co_value<T>` - 受信
  - `ch_close(int ch_id)`

#### co_mem: Memory Allocation

- [ ] dlmalloc 統合
  - `allocate(size_t size) -> void*`
  - `deallocate(void* ptr, size_t size)`
  - メモリ統計（使用率追跡）
- [ ] 6 分割パーティション初期化
  - P1-P4: 固定割り当て（23.2KB）
  - P5: ゲストヒープ（32-48KB、未使用）
  - P6: システムマージン（2-4KB、予約）
- [ ] コルーチン固有メモリ
  - 各コルーチン用 1KB スタック × 8 個

#### co_value: Ownership Tracking

- [ ] Template class: `template<typename T> class co_value<T>`
  - Move-only semantics (no copy)
  - RAII デストラクタ（自動削除）
- [ ] 64 個までの値追跡
  - グローバルレジストリ
  - オーナーシップ表記（コルーチン ID）
- [ ] Debug: 値のダンプ出力

#### Promise & Task (from coroutine-class-design.md)

- [ ] `task<T>` クラス実装
  - `std::coroutine_handle<promise_type>` 管理
  - Awaitable インターフェース（`operator co_await()`）
- [ ] Promise::operator new/delete
  - TLS (Thread Local Storage) から co_mem を取得
  - 例外安全性 (try-catch)
- [ ] co_await チェーン対応
  - `co_await task1; co_await task2;` の順序実行

### ビルド & テスト

```bash
# ビルド
cd build
cmake -DCMAKE_BUILD_TYPE=Release -DFIREBALL_PHASE=0 ..
make VERBOSE=1

# サイズ測定
arm-none-eabi-size fireball_phase0.elf
# 期待値: text ≤ 12KB

# ユニットテスト
ctest -V
```

### 期待される結果

| メトリクス | 期待値 | 判断 |
|----------|--------|------|
| Binary Size (.text) | 8-12KB | ✅ OK → Phase 1 へ |
| RAM (Fixed OH) | 5-7KB | ✅ OK |
| Memory Leak | 0 | ✅ OK (valgrind) |

### 備考

- Promise の実装は `src/coos/coroutine_promise.h` を参照（既に設計済み）
- TLS パターンは `co_mem::set_current_instance()` / `get_current_instance()` 使用
- x86-64 POSIX で最初に実装、その後 ARM Cortex-M4 に移植

---

## Phase 1: WASM Interpreter (Minimal)

**目標:** WASM インタプリタの最小実装（i32/i64 命令セット、約 60 命令）

### コンポーネント

| コンポーネント | ファイル | 説明 | 目標サイズ |
|-------------|---------|------|----------|
| **Interpreter** | `src/wasm/interpreter.cpp` | i32/i64 命令実装 | 18KB |
| **Module Loader** | `src/wasm/module_loader.cpp` | WASM バイナリ解析 | 5KB |
| **Execution Context** | `src/wasm/exec_context.cpp` | 実行状態（PC/SP/FP/Stack） | 2.5KB |

### 実装チェックリスト

#### Interpreter: Instruction Execution

- [ ] **i32 命令** (35 命令)
  - `i32.const`, `i32.add`, `i32.sub`, `i32.mul`, `i32.div_s`, `i32.div_u`
  - `i32.rem_s`, `i32.rem_u`, `i32.and`, `i32.or`, `i32.xor`, `i32.shl`, `i32.shr_s`, `i32.shr_u`
  - `i32.load`, `i32.load8_s`, `i32.load8_u`, `i32.load16_s`, `i32.load16_u`
  - `i32.store`, `i32.store8`, `i32.store16`
  - `i32.eq`, `i32.ne`, `i32.lt_s`, `i32.le_s`, `i32.gt_s`, `i32.ge_s` (比較)
  - `i32.clz`, `i32.ctz`, `i32.popcnt` (ビット操作)

- [ ] **i64 命令** (35 命令)
  - i32 と同様の 64 ビット版

- [ ] **制御フロー** (10 命令)
  - `block`, `loop`, `if`, `else`, `end`
  - `br`, `br_if`, `br_table`
  - `call`, `return`

- [ ] **スタック操作**
  - `drop`, `select`
  - 値スタック（最大 512 エントリ）
- [ ] **ローカル変数**
  - `local.get`, `local.set`, `local.tee`
  - 最大 32 ローカル変数

#### Module Loader: Binary Parsing

- [ ] WASM ヘッダ検証
  - マジック番号 `\0asm`
  - バージョン確認
- [ ] セクション解析
  - Type セクション（関数シグネチャ）
  - Import セクション（外部関数）
  - Code セクション（関数ボディ）
  - Memory セクション（線形メモリ）
  - Export セクション（外部インターフェース）
- [ ] コード解析
  - 命令列の検証
  - リロケーション情報の抽出

#### Execution Context: State Management

- [ ] **Registers**
  - PC (Program Counter)
  - SP (Stack Pointer)
  - FP (Frame Pointer)
- [ ] **Value Stack**
  - 512 × `uint64_t` スタック
  - スタックトレース情報（デバッグ用）
- [ ] **Error Handling**
  - Trap 情報（エラーコード、PC）
  - スタックアンワインド

### ビルド & テスト

```bash
# ビルド
cmake -DCMAKE_BUILD_TYPE=Release -DFIREBALL_PHASE=1 ..
make VERBOSE=1

# サイズ測定
arm-none-eabi-size fireball_phase1.elf
# 期待値: text ≤ 30KB (Core + Interpreter)

# テスト: 簡単な WASM プログラムを実行
# 例: i32.const 42, i32.const 8, i32.add -> 50
python tools/generate_wasm.py --test=add_test.wasm
./build/fireball_phase1 add_test.wasm
```

### 期待される結果

| メトリクス | 期待値 | 判断 |
|----------|--------|------|
| Binary Size (.text) | 20-30KB | ✅ OK → Phase 2 へ |
| Memory (Interpreter) | 4.7KB | ✅ OK |
| Instructions/Second | >1M IPS | ✅ OK（STM32F401） |

### 備考

- インタプリタは Switch-Case 実装が最適（コンパイラ最適化）
- スタックオーバーフロー検出は Phase 3 で追加
- JIT コンパイル対応は Phase 5

---

## Phase 2: Subsystems (logger + hal)

**目標:** ロギング・HAL サブシステムの実装

### コンポーネント

| コンポーネント | ファイル | 説明 | 目標サイズ |
|-------------|---------|------|----------|
| **IPC Router** | `src/subsystems/ipc_router.cpp` | メッセージルーティング | 2.2KB |
| **logger** | `src/subsystems/logger.cpp` | ロギング（リングバッファ） | 3.0KB |
| **hal** | `src/subsystems/hal.cpp` | GPIO/UART デバイスドライバ | 2.8KB |
| **Module Loader** | Phase 1 からの統合 | - | - |

### 実装チェックリスト

#### IPC Router: Message Hub

- [ ] Component Registry
  - Subsystem/Service URI を DI コンテナに登録
  - URI → Route ID マッピング（binary search で高速化）
- [ ] Message Routing
  - Functional メッセージ（Scope ID = `01x`） → ターゲットサービスへ
  - Dict ID メッセージ（Scope ID = `001`） → Logger へ
- [ ] CSP チャネル統合
  - ルーターが各サービスへのチャネルを管理
  - 型付きKey-Value形式 エンコード/デコード

#### logger: Logging Service

- [ ] **Ring Buffer**
  - Event struct: timestamp, level (DEBUG/INFO/WARN/ERROR), message
  - 256 イベント × 8B = 2KB リングバッファ
- [ ] **ROM Dictionary**
  - `logger_dictionary.cxx`: ログメッセージ文字列（ROM に配置）
  - `log_keys_offsets.hxx`: `constexpr` オフセット定数
- [ ] **UART Backend**
  - UART 経由でホスト PC へ送信
  - 型付きKey-Value形式 エンコード
- [ ] **Filtering**
  - ログレベル（DEBUG, INFO, WARN, ERROR）
  - リアルタイムフィルタリング

#### hal: HAL Device Driver

- [ ] **GPIO Controller**
  - `hal_gpio_set(int pin, int value)`
  - `hal_gpio_get(int pin) -> int`
  - 最大 32 ピン管理
- [ ] **UART Driver**
  - `hal_uart_write(const uint8_t* data, int len)`
  - `hal_uart_read(uint8_t* buf, int len) -> int`
  - Interrupt mode（優先度: Phase 3 で）
- [ ] **Device Registry**
  - Device Handle 管理（最大 16 デバイス）
  - Device Capabilities クエリ

### IPC Protocol Integration (from ipc-protocol.md)

- [ ] 固定長レコード（8B）
  - Scope ID | Key ID (3B) | Value (4B)
- [ ] Message Queue
  - CSP チャネルを通じたメッセージ転送
  - 256B バッファ = 32 レコード

### ビルド & テスト

```bash
# ビルド
cmake -DCMAKE_BUILD_TYPE=Release -DFIREBALL_PHASE=2 ..
make VERBOSE=1

# サイズ測定
arm-none-eabi-size fireball_phase2.elf
# 期待値: text ≤ 55KB (Core + Interpreter + Subsystems)

# テスト: logger + WASM インタプリタ統合
# ゲストが logger へメッセージを送信し、ホスト PC で受信確認
make test
```

### 期待される結果

| メトリクス | 期待値 | 判断 |
|----------|--------|------|
| Binary Size (.text) | 40-55KB | ✅ OK → Phase 3 へ |
| Memory (Router+logger+hal) | 8.8KB | ✅ OK |
| Message Latency | <100µs (STM32F401) | ✅ OK |

### 備考

- logger の ROM 辞書サイズは実装後に測定（予想: 1-2KB）
- Device API は WASM から呼び出し可能に（Phase 3 で export）
- Interrupt handling は初版では Polling で実装

---

## Phase 3: Debugger (GDB RSP)

**目標:** デバッガ バックエンド（RSP over UART）の実装

### コンポーネント

| コンポーネント | ファイル | 説明 | 目標サイズ |
|-------------|---------|------|----------|
| **debugger** | `src/subsystems/debugger.cpp` | GDB RSP プロトコル | 4KB |
| **Transport** | `src/debug/transport_*.cpp` | UART/stdio トランスポート | 1-2KB |

### 実装チェックリスト

#### debugger: GDB Remote Protocol (RSP)

- [ ] **Command Handlers**
  - `g` - Register read (`$g#...` → `$<hexdata>#...`)
  - `m` - Memory read (`$m<addr>,<len>#...`)
  - `G` - Register write
  - `M` - Memory write
  - `c` - Continue
  - `s` - Step
  - `z0`/`Z0` - Breakpoint clear/set
  - `?` - Query stop reason
  - `H` - Thread select

- [ ] **Breakpoint Storage**
  - 最大 10 ブレークポイント
  - アドレス + 条件フラグ

- [ ] **Register Snapshot**
  - 32 レジスタ（ARM Cortex-M4）
  - PC, SP, LR など

#### Transport: UART/stdio

- [ ] **uart_transport** (本番)
  - RSP フレーミング: `$<data>#<checksum>`
  - Checksum 計算（モジュロ 256）
  - UART write/read（115200 bps デフォルト）

- [ ] **stdio_transport** (開発・テスト)
  - シンプル行プロトコル: `<command>\n`
  - x86-64 POSIX で使用

### GDB統合テスト

```bash
# ターミナル 1: Fireball (STM32F401 シミュレータ, QEMU)
qemu-arm-softmmu -M stm32f401 -serial stdio -kernel fireball_phase3.elf

# ターミナル 2: GDB
arm-none-eabi-gdb ./build/fireball_phase3.elf
(gdb) target remote /dev/ttyUSB0 115200
(gdb) break main
(gdb) continue
(gdb) info registers
(gdb) x/4x 0x20000000  # メモリ読み取り
```

### ビルド & テスト

```bash
# ビルド
cmake -DCMAKE_BUILD_TYPE=Release -DFIREBALL_PHASE=3 ..
make VERBOSE=1

# サイズ測定
arm-none-eabi-size fireball_phase3.elf
# 期待値: text ≤ 85KB

# GDB テスト
make test-gdb
```

### 期待される結果

| メトリクス | 期待値 | 判断 |
|----------|--------|------|
| Binary Size (.text) | 65-85KB | ✅ OK → 本実装開始 |
| Memory (debugger+transport) | 3KB | ✅ OK |
| GDB Response Time | <50ms | ✅ OK |
| マージン | 16-32KB | ✅ OK （96KB SRAM） |

---

## Build Configuration & Testing

### CMake オプション

```cmake
# Platform Selection
set(FIREBALL_PLATFORM "arm-cortex-m4" CACHE STRING "Target platform")
# Options: arm-cortex-m0, arm-cortex-m4, arm-cortex-m7, riscv32, riscv64, x86-64

# Phase Selection
set(FIREBALL_PHASE "3" CACHE STRING "Implementation phase")
# Options: 0 (COOS only), 1 (+ Interpreter), 2 (+ Subsystems), 3 (+ Debugger)

# Build Optimization (STM32F401推奨)
set(CMAKE_CXX_FLAGS_RELEASE "-O2 -flto -fno-unroll-loops")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,--gc-sections -Wl,--print-memory-usage")

# Debug Build (開発用)
set(CMAKE_CXX_FLAGS_DEBUG "-g -O0")
```

### Size Measurement

```bash
# Detailed size breakdown
arm-none-eabi-size -A fireball_phase3.elf

# Top 20 symbols (code bloat detection)
arm-none-eabi-nm --print-size --size-sort fireball_phase3.elf | tail -20

# Section analysis
arm-none-eabi-objdump -h fireball_phase3.elf | grep ".text\|.data\|.rodata"

# Memory map
arm-none-eabi-objdump -M intel -S fireball_phase3.elf | head -100
```

### Unit Tests

```bash
# All tests
ctest -V

# Specific test suites
ctest -R "test_coos" -V      # COOS kernel tests
ctest -R "test_interpreter" -V  # WASM interpreter tests
ctest -R "test_ipc" -V       # IPC router tests
ctest -R "test_gdb" -V       # Debugger tests
```

### Continuous Integration

```bash
# GitHub Actions / CI Pipeline
# .github/workflows/fireball-ci.yml

name: Fireball CI
on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install ARM Toolchain
        run: sudo apt-get install gcc-arm-none-eabi
      - name: Build Phase 0-3
        run: |
          mkdir build && cd build
          for phase in 0 1 2 3; do
            cmake -DFIREBALL_PHASE=$phase ..
            make clean && make
            arm-none-eabi-size fireball_phase$phase.elf
          done
      - name: Run Tests
        run: cd build && ctest -V
```

---

## Verification Checklist

### Phase 0 Completion

- [ ] COOS コア（co_sched, co_csp, co_mem, co_value）が動作
- [ ] バイナリサイズ 8-12KB 以内
- [ ] メモリリークなし（valgrind）
- [ ] 8 コルーチン× 1KB スタック動作確認
- [ ] Unit tests 全パス

### Phase 1 Completion

- [ ] WASM インタプリタが i32/i64 命令を実行
- [ ] Module loader が WASM バイナリを解析
- [ ] バイナリサイズ 20-30KB 以内（Core + Interpreter）
- [ ] 簡単な WASM プログラム（add, 条件分岐）が動作
- [ ] Unit tests 全パス

### Phase 2 Completion

- [ ] IPC Router がメッセージを正しくルーティング
- [ ] logger が UART で出力
- [ ] hal が GPIO/UART デバイスを制御
- [ ] バイナリサイズ 40-55KB 以内
- [ ] ゲストが logger、hal を通じて通信可能
- [ ] Unit tests 全パス

### Phase 3 Completion

- [ ] GDB が RSP over UART で接続
- [ ] ブレークポイント設定・継続・ステップ実行が動作
- [ ] レジスタ・メモリ読み取り/書き込みが動作
- [ ] バイナリサイズ 65-85KB 以内（マージン 30% 込み）
- [ ] RAM 使用量 63-80KB（マージン 16-32KB 確保）
- [ ] GDB統合テスト全パス

---

## Resource Budget Validation

### ROM Budget（STM32F401: 256KB Flash）

| Phase | Core | Guest Space | OTA Slot | 使用率 | 余裕 |
|-------|------|------------|----------|--------|------|
| 0 | 8-12KB | 200KB+ | 32KB | 15-18% | ✅ OK |
| 1 | 20-30KB | 190KB+ | 32KB | 22-29% | ✅ OK |
| 2 | 40-55KB | 170KB+ | 32KB | 32-40% | ✅ OK |
| 3 | 65-85KB | 150KB+ | 32KB | 45-50% | ✅ OK |

### RAM Budget（STM32F401: 96KB SRAM）

| Phase | Core Fixed | Guest Heap | System Margin | 合計 | 使用率 | 余裕 |
|-------|----------|-----------|--------------|------|--------|------|
| 0 | 10.7KB | 40KB | 20KB | 70.7KB | 74% | ✅ 25KB |
| 1 | 15.4KB | 40KB | 20KB | 75.4KB | 79% | ✅ 21KB |
| 2 | 23.2KB | 40KB | 16KB | 79.2KB | 83% | ✅ 17KB |
| 3 | 23.2KB | 32-48KB | 16-32KB | 63-80KB | 66-83% | ✅ 16-32KB |

**重要:** Phase 3 では WAMR 最悪ケース（104KB）を回避し、Fireball は 80KB で収まる。

---

## Next Steps (After Phase 3)

1. **Phase 4: Multi-Platform Support**
   - RISC-V (T-Head TH1100, WCH CH32V203) 対応
   - STM32L0（超低消費電力）対応

2. **Phase 5: JIT Compiler**
   - Cranelift 統合（コード生成）
   - Dynamic code cache

3. **Phase 6: Advanced Debugging**
   - VSCode Cortex-Debug 拡張
   - Real-time event tracing

4. **Phase 7: Production Hardening**
   - Security audit
   - Stress testing (100+ コルーチン)
   - Memory fuzzing

---

## References

- [performance.md](performance.md) - メモリ予算詳細
- [coroutine-class-design.md](coroutine-class-design.md) - Promise/Task 実装
- [ipc-protocol.md](ipc-protocol.md) - IPC プロトコル仕様
- [subsystem-services.md](subsystem-services.md) - サブシステムアーキテクチャ
- [rsp-specification.md](rsp-specification.md) - GDB RSP 仕様
- [architecture.md](architecture.md) - 全体アーキテクチャ
