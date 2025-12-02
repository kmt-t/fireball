# Fireball Architecture Overview

**Version:** 0.1.0
**Date:** 2025-11-28
**Author:** Takuya Matsunaga

---

## Overview

Fireball は、リソース制約のある組み込みシステム向けの WebAssembly ハイパーバイザーです。本ドキュメントは、Fireball システム全体のアーキテクチャ、各コンポーネント間の依存関係、データフロー、メモリレイアウト、パフォーマンス特性を詳細に説明します。

**核となる設計思想：**

- **マイクロカーネル設計**: COOS（Cooperative Operating System）カーネルは最小限の 4 つのコンポーネント（スケジューラー、CSP チャネル、メモリ管理、値の所有権追跡）のみで構成。ロギング、ハードウェア管理、デバッグなどはサービスレイヤーとして独立
- **協調的マルチタスク**: プリエンプティブなタイマー割り込みを使用せず、コルーチンが明示的に制御を譲り合う方式。これにより複雑なロック機構を排除
- **CSP による通信**: チャネルを通じた同期通信が唯一の同期機構。「メモリ共有による通信をするな；通信によってメモリを共有せよ」というホーアの原理に従う
- **メモリ隔離**: 各 WASM モジュールは独立したメモリスペースを持ち、ヒープ枯渇が他モジュールに波及しない

以降では、システムレイヤー構成、コンポーネント依存関係、データフロー、メモリレイアウト、パフォーマンス特性を順に説明します。

---

## 1. System Layers

Fireball は、下記の 6 つのレイヤーから構成されています。各レイヤーは明確な責務を持ち、下位のレイヤーのみに依存します（逆方向の依存はない）。

**レイヤーの役割分担：**

1. **WebAssembly Guest Modules**: ユーザーが提供する WASM バイナリコード（アプリケーション層）
2. **Virtual System-on-Chip (vSoC)**: 仮想 SoC の中核実行エンジン。Interpreter（i32 命令セット実行）+ JIT（最適化実行）を統合。vOffloader（アクセラレータ・システムコール）を MMIO マッピング。
   - **主要実行**: vSoC が命令実行の中心
   - **従要素**: Interpreter は vSoC の命令セット実装、JIT は最適化モジュール
   - **周辺**: vOffloader は MMIO でマッピングされ、ネイティブ BLAS などのアクセラレータとシステムコールを提供
3. **COOS Kernel Core**: 協調的スケジューリング、CSP チャネル、メモリ割り当て、値の所有権追跡（最小限のカーネル）
4. **Subsystems & Services Layer**:
   - **Router (DI Container)**: URI ベースルーティング、アクセス制御、コンポーネント登録（依存性注入）
   - **Subsystems (ネイティブ実装)**: ロギング（logger）、ハードウェア抽象化（hal）、デバッグ
   - **Services (WASM プラグイン)**: ユーザー提供の WASM サービス実装
5. **HAL Backend**: UART、GPIO、I2C、SPI、Timer などのドライバ実装（プラットフォーム固有）
6. **Hardware**: ARM Cortex-M / RISC-V / x86 (for testing)

以下は、システム全体のレイヤー構成を視覚化したものです。矢印は制御フロー（上から下への呼び出し）を示します。

```
┌──────────────────────────────────────────────────────────────┐
│                  WebAssembly Guest Modules                    │
│                     (user code)                               │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│             Virtual System-on-Chip (vSoC)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │    Module    │  │ Interpreter  │  │   Linear    │       │
│  │    Loader    │  │  (i32 core)  │  │   Memory    │       │
│  └──────────────┘  ├──────────────┤  └──────────────┘       │
│                    │ JIT Compiler │                         │
│                    │  (Optional)  │                         │
│                    └──────────────┘                         │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  COOS Kernel Core（最小限の4コンポーネント）                │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  co_sched (Scheduler)                              │   │
│  │  co_csp (CSP Channels)                             │   │
│  │  co_mem (Memory Allocator)                         │   │
│  │  co_value (Ownership Tracking)                     │   │
│  └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Subsystems & Services Layer（COOS非依存、IPC通信）         │
│                                                               │
│        ┌─────────────────────────────────┐                  │
│        │   Router ★ (DI Container)       │                  │
│        │   - URI-based routing           │                  │
│        │   - Access control              │                  │
│        └────────────┬─────────────────────┘                  │
│                     │                                        │
│     ┌───────────────┼───────────────────┐                   │
│     │               │                   │                   │
│     ▼               ▼                   ▼                   │
│ ┌─────────┐   ┌──────────────┐   ┌──────────────┐          │
│ │ logger  │   │   hal        │   │  Services    │          │
│ └─────────┘   │(HAL bridge)  │   │  (WASM)      │          │
│               └──────────────┘   └──────────────┘          │
│ ┌─────────┐                                                │
│ │debugger │                                                │
│ │[Phase3] │                                                │
│ └─────────┘                                                │
│                                                               │
│  ★ Router: DI Container + URI routing                       │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│    Hardware Abstraction Layer (HAL Backend)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │  UART    │  │   GPIO   │  │   I2C    │  │  Timer   │    │
│  │  Driver  │  │  Driver  │  │  Driver  │  │  Driver  │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                      Hardware                                 │
│  ARM Cortex-M / RISC-V / x86 (for testing)                  │
└──────────────────────────────────────────────────────────────┘
```

## 2. Component Dependencies

Fireball の設計では、**依存関係が明確で循環参照がない**ことが重要です。これにより、各コンポーネントを独立にテストでき、変更による影響を局所化できます。

以下は、COOS カーネルコアと Subsystems & Services Layer における依存関係を示したものです。

**重要な設計原則：**

- **COOS カーネルコア**: 4 つのコンポーネント間に依存関係がありますが、循環参照はありません。スケジューラーはメモリ管理とチャネルに、チャネルは所有権追跡とスケジューラーに依存します
- **Subsystems (Native C++)**: COOS カーネルコンポーネントへの直接依存がありません。すべての通信は CSP チャネル経由で行われるため、logger・hal などのサブシステムは COOS の実装詳細に依存しません
- **Services (WASM プラグイン)**: ユーザーが提供する WASM サービス実装。プラグイン性により、独立した複数サービスの同時実行が可能です

以下は、各コンポーネントの依存関係の詳細です。

**COOS Kernel Core（4コンポーネント、相互独立）：**

```
co_sched (Scheduler)
    ├── co_mem (coroutine context allocation)
    └── co_csp (blocked by channels, coordination)

co_csp (CSP Channels)
    ├── co_value (value transfer, ownership)
    └── co_sched (resume blocked coroutines)

co_mem (Memory Manager)
    └── (no dependencies)

co_value (Ownership Tracking)
    └── (no dependencies, header-only template)
```

**Router（DI Container + ルーティング）：**

```
Router (Dependency Injection Container)
    ├── co_csp (CSP channels for all routing)
    ├── co_mem (routing table, component registry)
    ├── logger (component registration logging)
    ├── hal (HAL subsystem)
    ├── debugger (debugger subsystem)
    └── Services (User WASM plugins)

Responsibility:
- URI-based component routing and discovery
- Access control (request verification before routing)
- Component registration and lifecycle management
- Request serialization (型付きKey-Value)
- Star topology management (all components register with Router)
- Route caching via binary search for O(log N) lookup
```

**Subsystems（ネイティブ実装、COOS非依存）：**

```
logger (Logging Subsystem)
    └── Router (route management via IPC)

hal (HAL Subsystem)
    └── Router (route management via IPC)

debugger [Phase 3] (GDB Protocol Subsystem)
    └── Router (route management via IPC)

jit [Future] (JIT Compiler Subsystem)
    └── Router (route management via IPC)
```

**Services（WASM プラグイン、ユーザー実装）：**

```
User Service 1
    └── Router (IPC communication via encoded messages)

User Service N
    └── Router (IPC communication via encoded messages)
```

**重要：**
- **Router は DI Container として機能**。全てのコンポーネント登録・発見がこれを経由
- **スター型トポロジ**：vSoC、すべての Subsystems、すべての Services が Router に登録
- **URI ベースルーティング**：全てのリクエストは Router 経由で URI から宛先に解決
- **アクセス制御**：Router が権限管理を行い、不正なリクエストを拒否
- **Request Serialization**：型付きKey-Value プロトコル は Router が担当
- **Subsystems と Services は独立した隔離ヒープを使用**し、メモリ枯渇による相互の影響を防止

### 2.1 Runtime-Embedded Functions（パフォーマンス最適化設計）

**背景**：基本設計は Router + 型付きKey-Value（柔軟・セキュア）ですが、GPIO トグル（<50 サイクル要件）など**ホットパス操作**は IPC オーバーヘッド（数千サイクル）を負担できません。

**設計原則**：性能クリティカル機能（GPIO、GPU/NEON、Debugger）は vSoC ランタイム内蔵、非同期操作（Logger、ADC）は Router IPC。これは Windows GPU カーネルや Android で採用されている標準パターン。

**実装例**：gpio_embedded（GPIO 操作）は vSoC 内蔵で Router 経由なし。offloader_embedded（MMIO マップ）は Accelerator、Debugger、JIT を統合。vsoc_impl は builtin_* 関数群で WASM コードから直接呼び出し可能。

**性能特性（実行位置と レイテンシ）**

| 操作 | 実行位置 | サイクル |
|-----|---------|---------|
| GPIO write | Runtime-embedded | 8-15 |
| GPU matmul | Runtime-embedded (MMIO) | 100-10000+ |
| Breakpoint check | Runtime-embedded | <50 |
| JIT hotpath hint | Runtime-embedded | O(1) |
| Log write | Router enqueue | <5 |
| ADC read | Router IPC | 500-2000 |
| File read (local) | HAL direct | 50-500 |
| Service RPC call | Router IPC | 500-2000 |

**重要な設計原則**：
- **Router + 型付きKey-Value** は外部通信・Services 用として維持
- **vOffloader**: GPIO/GPU/Debugger/JIT を統合、<50 サイクル要件達成、コルーチン内実行
- **Router IPC**: ADC・Logger などハードウェア待ち時間が許容可能な操作向け
- **標準パターン**: Android、Windows GPU、組み込み RTOS で採用

---

## 3. Data Flow

本セクションは、Fireball における主要な処理フロー（コルーチンライフサイクル、チャネル通信、メモリ割り当て）を説明します。

### 3.1 Coroutine Lifecycle

コルーチン：spawn → run/step → done/suspend。各ステップで co_mem（スタック割り当て）、co_sched（FIFO Ready Queue）、logger（イベント記録）が連携。

```
spawn()
  │
  ├─► [Allocate stack]  ◄──── co_mem
  ├─► [Create coroutine_handle] ◄──── C++20 coroutines
  ├─► [Add to ready_queue]
  └─► [Log: coroutine_spawned] ◄──── logger subsystem

run() / step()
  │
  ├─► [Pop from ready_queue]
  ├─► [Log: coroutine_resumed] ◄──── logger subsystem
  ├─► [Execute until co_yield or done]
  │
  ├─► If done:
  │     ├─► [Log: coroutine_completed] ◄──── logger subsystem
  │     ├─► [Deallocate stack] ◄──── co_mem
  │     └─► [Remove from tracking]
  │
  └─► If suspended:
        ├─► [Log: coroutine_suspended] ◄──── logger subsystem
        └─► [Re-add to ready_queue]
```

### 3.2 Channel Communication

CSP チャネル：唯一の同期・通信メカニズム。Rendezvous（出会い）まで両者ブロック。`co_value<T>` で所有権自動移譲（データ競合なし）。

```
Sender:
  send(value)
    │
    ├─► [Wrap in co_value<T>] ◄──── co_value
    ├─► [Log: channel_send] ◄──── logger subsystem
    │
    ├─► If receiver waiting:
    │     ├─► [Transfer value ownership]
    │     ├─► [Resume receiver] ◄──── co_sched
    │     └─► [Continue sender]
    │
    └─► If no receiver:
          ├─► [Store value in channel]
          ├─► [Add sender to wait queue]
          └─► [co_yield to scheduler] ◄──── co_sched

Receiver:
  recv()
    │
    ├─► [Log: channel_recv] ◄──── logger subsystem
    │
    ├─► If sender waiting:
    │     ├─► [Take value from channel]
    │     ├─► [Transfer ownership] ◄──── co_value
    │     ├─► [Resume sender] ◄──── co_sched
    │     └─► [Return value]
    │
    └─► If no sender:
          ├─► [Add receiver to wait queue]
          ├─► [co_yield to scheduler] ◄──── co_sched
          └─► [When resumed, return value]
```

### 3.3 Memory Allocation

co_mem（dlmalloc ベース）：各 WASM モジュールが独立 mspace で隔離。モジュールメモリ枯渇が他に影響しない。Stack は page-aligned、ガード ページ対応可。

```
Application Request:
  allocate(size, alignment)
    │
    ├─► [dlmalloc mspace_malloc] ◄──── dlmalloc
    ├─► [Update statistics]
    └─► [Return pointer]

Stack Allocation:
  allocate_stack(size)
    │
    ├─► [Round up to page boundary]
    ├─► [dlmalloc mspace_memalign] ◄──── dlmalloc
    ├─► [Optional: add guard page]
    └─► [Return stack pointer]

Deallocation:
  deallocate(ptr)
    │
    ├─► [dlmalloc mspace_free] ◄──── dlmalloc
    └─► [Update statistics]
```

---

## 4. Concurrency Model

Fireball：**協調的スケジューリング** + **CSP 同期**。プリエンプティブ割り込みなし、`co_yield` で制御譲り合い。WASM は 1000 命令ごとに自動 yield 注入→ゲストはプリエンプティブに見える。

```
Time ──────────────────────────────────────────────►

Coro A: ████████ (running)
             │ co_yield
             ▼
Coro B:      ████████ (running)
                  │ co_yield
                  ▼
Coro C:           ████████ (running)
                       │ co_yield
                       ▼
Coro A:                ████████ (running)
                            │ ...
```

**Key Points:**
- **No preemption**: コルーチンは明示的に yield するまで実行を続ける
- **No interrupts**: ハードウェアタイマー割り込みによるコンテキストスイッチなし
- **Deterministic**: 同じ入力 → 常に同じ実行順序

### 4.2 CSP Synchronization

CSP：チャネル経由の唯一の通信手段。Rendezvous、所有権移譲（データ競合なし）、デッドロック回避。ロック機構なし。

```
Coro 1:                    Channel:              Coro 2:
                                   │
send(42) ──────────────────────►  │
  (blocks)                         │ [sender_ready]
                                   │
                                   │  ◄────────────── recv()
                                   │                    (blocks)
                                   │
                                   │ [rendezvous!]
                                   │
  (resumes) ◄────────────────────  │
                                   │  ──────────────► (resumes with 42)
                                   │
  (continues)                      │ [idle]           (continues)
```

**Key Points:**
- Blocking send/receive: both sides must meet
- No buffering: direct value transfer
- Ownership transfer: sender loses access after send

---

## 5. Memory Layout

32 bit アドレス空間：
- **0xC0000000～0xFFFFFFFF**: デバイスメモリ（MMIO）
- **0x00000000～0x40000000**: メインメモリ（.text、.rodata、.data、.bss、ヒープ↑、スタック↓）

```
0xFFFFFFFF ┌────────────────────────┐
           │    (Reserved/Invalid)  │
0xC0000000 ├────────────────────────┤
           │     Device Memory      │
           │  (UART, GPIO, etc.)    │
0x40000000 ├────────────────────────┤
           │       RAM              │
           │  ┌──────────────────┐  │
           │  │   Heap (dlmalloc)│  │
           │  ├──────────────────┤  │
           │  │   Stacks (↓)     │  │
           │  ├──────────────────┤  │
           │  │   .bss           │  │
           │  ├──────────────────┤  │
           │  │   .data          │  │
           │  └──────────────────┘  │
0x00000000 └────────────────────────┘
```

### 5.2 Coroutine Stack

各コルーチン：独立スタック 8-16KB（デフォルト）。高アドレス→低アドレス成長。ガード ページ（4KB、オプション）でオーバーフロー検出可。

```
High Address
    ┌────────────────────┐
    │   Guard Page       │ (optional, read-only)
    ├────────────────────┤
    │   Stack Space      │ (grows downward)
    │   ...              │
    │   Local Variables  │
    │   Return Addresses │
    ├────────────────────┤
    │   Stack Pointer    │ ◄─── Current SP
    └────────────────────┘
Low Address
```

**Stack Configuration:**
- **Default**: 8KB - 16KB（カスタマイズ可能）
- **Maximum**: 64KB（組み込みシステムの制約）
- **Guard Page**: 4KB（サポートされている場合）

---

## 6. Build Artifacts

`libfireball.a`：COOS（co_sched、co_csp、co_mem）、WASM runtime（module、executor、memory）、Subsystems（logger、hal）、Allocator（dlmalloc）、Utils（backtrace）から構成。

```
libfireball.a
  ├── coos/
  │   ├── co_sched.o
  │   ├── co_csp.o
  │   ├── co_mem.o
  │   └── coos_kernel.o
  ├── wasm/
  │   ├── module.o
  │   ├── executor.o
  │   └── memory.o
  ├── services/
  │   ├── logger.o
  │   └── hal.o
  ├── allocator/
  │   ├── malloc.o
  │   └── stdcxx_allocator.o
  └── utils/
      └── backtrace.o
```

### 6.2 Executable Structure

```
fireball (ELF/binary)
  ├── .text (code)
  ├── .rodata (constants)
  ├── .data (initialized data)
  ├── .bss (zero-initialized)
  └── symbol table
```

---

## 7. Execution Model

### 7.1 Startup Sequence

起動順序：HAL → Subsystems → COOS kernel → Router → vSoC → WASM module読込 → 初期コルーチン生成 → イベントループ → シャットダウン。

```
main()
  │
  ├─► [Initialize HAL]
  ├─► [Initialize Subsystems (logger, hal)]
  ├─► [Create coos_kernel instance]
  ├─► [kernel->initialize()]
  │     ├─► [Initialize co_mem with dlmalloc]
  │     ├─► [Initialize co_sched]
  │     └─► [Set global kernel instance]
  │
  ├─► [Initialize Router (DI Container)]
  │     └─► [Register component URIs]
  │
  ├─► [Initialize vSoC]
  │     ├─► [Initialize Interpreter (i32 core)]
  │     ├─► [Initialize JIT module (if enabled)]
  │     └─► [Initialize vOffloader (MMIO mapping)]
  │
  ├─► [Load WASM module]
  │     └─► [Verify with Interpreter]
  │
  ├─► [Spawn initial coroutine]
  │     └─► kernel->get_scheduler()->spawn(...)
  │
  ├─► [kernel->run()]
  │     └─► [Event loop until all coroutines complete]
  │
  ├─► [kernel->shutdown()]
  └─► [Exit]
```

### 7.2 Event Loop Execution

`kernel->run()`：while pending_count > 0、各ステップで step() 実行、yield/done で次へ切り替え。

```
kernel->run():
  while scheduler->pending_count() > 0:
    if not scheduler->step():
      break

  // All coroutines completed or stopped
  return
```

---

## 8. Error Handling

エラー分類：
1. **Assertion Failures**: 所有権違反→スタックトレース出力、abort
2. **Channel Errors**: 閉じたチャネル送受信→std::nullopt、graceful shutdown
3. **Memory Errors**: ヒープ枯渇→nullptr返却、呼び出し側で処理
4. **WASM Errors**: 不正命令、スタックオーバーフロー→trap、logger記録

### 8.2 Debug Support

- **Backtrace**: std::stacktrace で呼び出しチェーン表示
- **Event Log**: logger がコルーチン/チャネル/メモリ操作を記録
- **Memory Dump**: mspace ごと使用量・断片化表示
- **Coroutine Dump**: アクティブコルーチン一覧（ID、ステータス、深度）

---

## 9. Performance Considerations

### 9.1 Context Switch Cost
```
Context Switch:
  1. Save current coroutine state (~10 instructions)
  2. Pop next coroutine from ready queue (~5 instructions)
  3. Resume coroutine handle (~20 instructions)
  ────────────────────────────────────────────────
  Total: ~35 instructions (~100-200 CPU cycles)
```

### 9.2 Channel Send/Receive Cost

**概要：** チャネル操作のコストは、受信側がすでに待機しているか（fast path）、新たにブロックする必要があるか（slow path）で大きく異なります。

- **Fast Path** (受信側待機)：~25 命令（~75-150 サイクル）
- **Slow Path** (ブロック)：~60 命令（~180-300 サイクル）

### 9.3 Memory Overhead

- **Per Coroutine**: ~8-16KB（handle 8B + Stack 8-16KB + metadata ~64B）
- **Per Channel**: ~40B + sizeof(T)

---

## 10. Target Device Constraints

### Platform Target Strategy

**Tier 1 (RISC-V)**: WCH CH32V307 (144MHz, 96KB SRAM), T-Head C906
**Tier 2 (Wireless)**: Nordic nRF52840 (64MHz, 256KB RAM), nRF5340 (512KB RAM)

### 10.1 WCH CH32V307 (Tier 1 Primary)

```
CPU: RISC-V RV32IMACZicsr @ 144 MHz
RAM: 96 KB SRAM
Flash: 256 KB
Address Space: 32-bit

Constraints:
  - ~60 KB available for application (after system)
  - ~3-5 coroutines max (at 16KB/stack)
  - ~30-50 channels typical
  - Context switch: ~2-3 µs
```

### 10.2 Nordic nRF52840 (Tier 2 Wireless)

```
CPU: ARM Cortex-M4 @ 64 MHz
RAM: 256 KB SRAM
Flash: 1 MB
Address Space: 32-bit

Constraints:
  - ~150 KB available for application (after system/BLE)
  - ~8-10 coroutines max (at 16KB/stack)
  - ~80-120 channels typical
  - Context switch: ~3-4 µs
```

---

## Architectural Decision Records (ADR)

### ADR-001: Multi-Guest Support
**Decision**: ランタイムで複数のゲストを実行できるようにする。マルチコア対応はコアごとにランタイムをアフィニティする構成。
**Rationale**: 単一ゲストのみの現状から、将来的な複数のWASMモジュールやサービス実行を可能にするための拡張性確保。マルチコア環境では、各コアに独立したランタイムインスタンスをアフィニティすることで、競合や複雑な同期機構を回避し、性能と安全性を両立させる。

### ADR-002: ISR Safety Mechanism
**Decision**: HALから割り込みフラグを立て、インタープリタから監視する。ゲストのコンテキストスイッチ時にランタイムがフラグをチェックする。
**Rationale**: 割り込みハンドラ（ISR）からの安全な操作を保証するため、複雑なロック機構やISRセーフなAPI（例: `send_from_isr`）を直接提供する代わりに、より軽量なフラグベースのメカニズムを採用する。これにより、ISRの実行時間を最小限に抑えつつ、割り込み起因のイベントをゲスト側で確実に処理できる。

### ADR-003: Memory Partitioning
**Decision**: ヘッダファイル形式のコンフィグファイルを定義しその中のマクロで容量などは固定する。
**Rationale**: リソース制約の厳しい組み込みシステムにおいて、動的なメモリパーティショニングのオーバーヘッドを避け、シンプルかつ予測可能なメモリ使用を保証するため。コンフィグファイルによる固定化は、ビルド時にメモリマップを確定させ、ランタイムの複雑性を低減する。

### ADR-004: Maximum Coroutines Configuration
**Decision**: ヘッダファイル形式のコンフィグファイルを定義しその中のマクロでヒープサイズなどを固定する。
**Rationale**: コルーチンの最大数を固定することで、スタックや関連リソースの事前割り当てを可能にし、ランタイムでの動的なリソース管理オーバーヘッドを削減する。ヒープサイズも同様に固定することで、メモリフットプリントを最適化し、確定的な動作を保証する。

### ADR-005: JIT Fallback Strategy
**Decision**: 基本方針はJITコンパイラのレイテンシの最小化である。テンプレートをヒープに展開しパッチを当てる方式。機械語の最適化はしない。レイテンシを小さくすることで原則ホットスポット分析なしフォールバックなしにしたいが実現性は現状不明である。
**Rationale**: JITコンパイラの導入に伴う遅延を最小限に抑えるため、最適化よりもレイテンシを優先する。その目的は以下の二点に厳格に限定される：
1.  **命令のデコードの削除**: インタプリタの命令デコードオーバーヘッドを排除し、実行効率を向上させる。
2.  **分岐予測が外れる可能性が高い分岐の除去**: 高コストな分岐予測ミスを減らし、CPUパイプラインの効率を高める。
生成される機械語は、テンプレートをヒープに展開しパッチを当てる方式で、基本的な算術演算を除き、分岐予測が有効なランタイムAPI呼び出しをコンパイルするのみであり、複雑な機械語最適化は行わない。これにより、シンプルかつ高速なコード生成を実現し、ホットスポット分析やインタプリタへのフォールバックを不要にすることを目指す。この限定されたスコープは、JITの設計リスクを最小限に抑えつつ、その適用範囲を広げることを可能にする。

## 11. Future Architecture Changes

### 11.1 Potential Optimizations

- **Coroutine Pool**: フレーム事前割り当て、割り当て/解放オーバーヘッド削減
- **Stack Overflow Detection**: ガード ページ/カナリア値で検出
- **Zero-Copy Channels**: 大メッセージは参照転送
- **Buffered Channels**: 有界 FIFO でバースト送信サポート

### 11.2 Scalability

- **Multi-core**: コアごと独立スケジューラー
- **Distributed**: ネットワークチャネルでリモート通信
- **Hierarchical**: 親・子カーネル構造で複数インスタンス統合
