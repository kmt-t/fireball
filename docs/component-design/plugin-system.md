# Fireball WebAssembly プラグインシステム設計

**Version:** 0.1.0
**Date:** 2025-11-28
**Author:** Takuya Matsunaga

---

## 概要（Overview）

Fireball WebAssembly ランタイムは、**WebAssembly で記述されたプラグイン**をサポートしています。
プラグインは、特定の WASM 命令、システムコール、ランタイム操作の動作をインターセプト、
修正、または置き換えることができます。

このプラグインシステムにより、コアランタイムの変更なしに、
ゲストプログラムの実行動作を柔軟にカスタマイズできます。

### 1.1 主要な概念（Key Concepts）

プラグインシステムの基本的な考え方：

- **プラグインは WASM モジュール**: すべてのプラグインコードは WASM にコンパイルできる言語で記述（C、Rust、AssemblyScript など）
- **命令フック**: 特定の WASM オペコードの実行をインターセプト
- **命令オーバーロード**: デフォルトの命令実装をカスタムロジックで置き換え
- **システムコールフック**: WASI および カスタムシステムコールをインターセプト
- **ホットプラグ対応**: プラグインはランタイム中に読み込み/アンロード可能

### 1.2 ユースケース（Use Cases）

プラグインシステムが想定する具体的な用途：

1. **計装（Instrumentation）**: 命令実行回数の計測、パフォーマンス測定
2. **セキュリティサンドボックス**: メモリアクセスの検証、システムコール制限
3. **デバッグ**: ブレークポイント、ウォッチポイント、シングルステップ実行
4. **カスタム命令**: ドメイン固有のオペコード実装
5. **最適化**: JIT コンパイルヒント、定数畳み込み

---

## 2. アーキテクチャ（Architecture）

### 2.1 システム概要（System Overview）

プラグインシステムは、ゲスト WASM モジュールからの命令実行、ホスト側の命令ディスパッチャ、
プラグインマネージャを通じて、プラグインが実行される流れを示します。

```
┌────────────────────────────────────────────────────────────┐
│                    Guest WASM Module                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  fn main() {                                         │  │
│  │    i32.add    ◄─────────┐ (hook triggered)          │  │
│  │    call $foo  ◄─────┐   │                           │  │
│  │  }            │     │   │                           │  │
│  └───────────────┼─────┼───┼───────────────────────────┘  │
└─────────────────┼─────┼───┼──────────────────────────────┘
                  │     │   │
                  │     │   └──► Instruction Hook
                  │     └──────► Call Hook
                  │
                  ▼
┌────────────────────────────────────────────────────────────┐
│                 Fireball Runtime Core                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           Instruction Dispatcher                     │  │
│  │  ┌─────────────────────────────────────────────┐    │  │
│  │  │  if (has_hook(opcode)) {                    │    │  │
│  │  │    result = invoke_plugin_hook(opcode, ctx) │    │  │
│  │  │    if (result.override) return result       │    │  │
│  │  │  }                                           │    │  │
│  │  │  execute_default_instruction(opcode)        │    │  │
│  │  └─────────────────────────────────────────────┘    │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                   │
│                         ▼                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │            Plugin Manager                            │  │
│  │  - Load/unload plugins                               │  │
│  │  - Register hooks                                    │  │
│  │  - Invoke plugin callbacks                           │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                   │
│                         ▼                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Plugin WASM Module                           │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │  export fn hook_i32_add(ctx) {                 │  │  │
│  │  │    log("i32.add intercepted")                  │  │  │
│  │  │    return { override: false }                  │  │  │
│  │  │  }                                             │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 2.2 プラグインライフサイクル（Plugin Lifecycle）

プラグインの読み込みから実行、アンロードまでのライフサイクルを示します。

```
プラグイン読み込み
    │
    ├─► プラグイン WASM モジュールをパース
    ├─► プラグインエクスポートを検証
    ├─► 隔離されたコンテキストでインスタンス化
    ├─► フック/オーバーロードを登録
    └─► plugin_init() エントリポイントを呼び出し
           │
           ▼
    ┌──────────────┐
    │ プラグイン準備完了 │
    └──────────────┘
           │
           ├─► フック呼び出し（ゲスト実行中）
           │   ├─► hook_i32_add()
           │   ├─► hook_call()
           │   └─► hook_syscall()
           │
           ▼
    プラグインアンロード
    │
    ├─► plugin_cleanup() エントリポイントを呼び出し
    ├─► すべてのフックを未登録
    └─► プラグインリソースを解放
```

---

## 3. プラグイン API（Plugin API）

プラグインが実装すべきインターフェースと、
Fireball ランタイムが提供するインポート関数について説明します。

### 3.1 プラグインエクスポート（Plugin Exports - 必須）

すべてのプラグインは以下の関数をエクスポートする必要があります：

```c
/**
 * Plugin metadata (compile-time constant)
 */
typedef struct {
  uint32_t api_version;       // Plugin API version (1)
  const char* name;           // Plugin name
  const char* version;        // Plugin version
  const char* author;         // Author
  const char* description;    // Description
} plugin_metadata_t;

/**
 * Get plugin metadata
 * REQUIRED: Must be exported by all plugins
 */
__attribute__((export_name("plugin_get_metadata")))
const plugin_metadata_t* plugin_get_metadata();

/**
 * Initialize plugin
 * Called when plugin is loaded
 * REQUIRED: Must be exported by all plugins
 */
__attribute__((export_name("plugin_init")))
int plugin_init();

/**
 * Cleanup plugin
 * Called when plugin is unloaded
 * OPTIONAL
 */
__attribute__((export_name("plugin_cleanup")))
void plugin_cleanup();
```

### 3.2 フック登録（Hook Registration）

プラグインが登録できるフックのタイプと、登録 API について説明します：

```c
/**
 * フックタイプ
 */
typedef enum {
  HOOK_INSTRUCTION,    // WASM 命令フック
  HOOK_CALL,           // 関数呼び出しフック
  HOOK_SYSCALL,        // システムコールフック
  HOOK_MEMORY,         // メモリアクセスフック
  HOOK_TABLE,          // テーブルアクセスフック
} hook_type_t;

/**
 * Hook priority (lower = higher priority)
 */
typedef uint32_t hook_priority_t;

/**
 * Register instruction hook
 *
 * @param opcode WASM opcode (e.g., 0x6a for i32.add)
 * @param callback Plugin callback function
 * @param priority Hook priority (0 = highest)
 * @return 0 on success, -1 on error
 */
__attribute__((import_module("fireball"), import_name("register_instruction_hook")))
int register_instruction_hook(uint8_t opcode, void* callback, hook_priority_t priority);

/**
 * Register syscall hook
 *
 * @param syscall_name Syscall name (e.g., "wasi_snapshot_preview1::fd_write")
 * @param callback Plugin callback function
 * @param priority Hook priority
 * @return 0 on success, -1 on error
 */
__attribute__((import_module("fireball"), import_name("register_syscall_hook")))
int register_syscall_hook(const char* syscall_name, void* callback, hook_priority priority);

/**
 * Unregister hook
 *
 * @param hook_id Hook ID (returned by register_*_hook)
 * @return 0 on success, -1 on error
 */
__attribute__((import_module("fireball"), import_name("unregister_hook")))
int unregister_hook(uint32_t hook_id);
```

### 3.3 フックコールバック（Hook Callbacks）

フックが実装すべきコールバック関数のシグネチャと用途について説明します。

#### 3.3.1 命令フック（Instruction Hook）

```c
/**
 * 命令実行コンテキスト
 */
typedef struct {
  uint8_t opcode;             // WASM opcode
  uint32_t pc;                // Program counter (offset in code section)
  uint32_t* stack;            // Value stack pointer
  uint32_t stack_size;        // Number of values on stack
  uint32_t* locals;           // Local variables
  uint32_t locals_count;      // Number of locals
  void* memory;               // Linear memory base
  uint32_t memory_size;       // Memory size (bytes)
} instruction_context_t;

/**
 * Hook result
 */
typedef struct {
  bool override;              // If true, skip default instruction execution
  uint32_t result;            // Result value (if override=true)
  int error;                  // Error code (0 = success)
} hook_result_t;

/**
 * Instruction hook callback
 *
 * @param ctx Instruction context
 * @return Hook result
 */
typedef hook_result_t (*instruction_hook_fn_t)(const instruction_context_t* ctx);

/**
 * Example: Hook i32.add instruction
 */
__attribute__((export_name("hook_i32_add")))
hook_result hook_i32_add(const instruction_context_t* ctx) {
  // Read operands from stack
  uint32_t b = ctx->stack[ctx->stack_size - 1];
  uint32_t a = ctx->stack[ctx->stack_size - 2];

  // Log the operation
  plugin_log("i32.add: %d + %d", a, b);

  // Let default implementation handle it
  return (hook_result_t){ .override = false };
}

/**
 * Example: Override i32.add with saturation
 */
__attribute__((export_name("hook_i32_add_saturating")))
hook_result hook_i32_add_saturating(const instruction_context_t* ctx) {
  uint32_t b = ctx->stack[ctx->stack_size - 1];
  uint32_t a = ctx->stack[ctx->stack_size - 2];

  // Saturating addition
  uint32_t result = a + b;
  if (result < a) {
    result = 0xFFFFFFFF;  // Saturate to max
  }

  // Override default implementation
  return (hook_result_t){
    .override = true,
    .result = result,
    .error = 0
  };
}
```

#### 3.3.2 システムコールフック（Syscall Hook）

```c
/**
 * システムコール実行コンテキスト
 */
typedef struct {
  const char* name;           // Syscall name
  uint32_t* args;             // Arguments
  uint32_t args_count;        // Number of arguments
  void* memory;               // Linear memory base
  uint32_t memory_size;       // Memory size
} syscall_context_t;

/**
 * Syscall hook callback
 */
typedef hook_result (*syscall_hook_fn_t)(const syscall_context_t* ctx);

/**
 * Example: Hook WASI fd_write
 */
__attribute__((export_name("hook_wasi_fd_write")))
hook_result hook_wasi_fd_write(const syscall_context_t* ctx) {
  uint32_t fd = ctx->args[0];
  uint32_t iovs_ptr = ctx->args[1];
  uint32_t iovs_len = ctx->args[2];

  // Validate file descriptor
  if (fd > 2) {
    plugin_log("Blocked fd_write to fd=%d", fd);
    return (hook_result_t){
      .override = true,
      .result = -1,  // EBADF
      .error = 0
    };
  }

  // Allow write to stdout/stderr
  return (hook_result){ .override = false };
}
```

#### 3.3.3 メモリフック（Memory Hook）

```c
/**
 * メモリ操作タイプ
 */
typedef enum {
  MEM_READ,          // メモリ読み取り
  MEM_WRITE,         // メモリ書き込み
  MEM_GROW,          // メモリ拡張
} memory_op_t;

/**
 * Memory context
 */
typedef struct {
  memory_op op;               // Operation type
  uint32_t address;           // Memory address
  uint32_t size;              // Access size (bytes)
  uint32_t value;             // Value (for writes)
  void* memory;               // Memory base
  uint32_t memory_size;       // Memory size
} memory_context_t;

/**
 * Memory hook callback
 */
typedef hook_result_t (*memory_hook_fn_t)(const memory_context* ctx);

/**
 * Example: Guard against out-of-bounds writes
 */
__attribute__((export_name("hook_memory_write")))
hook_result_t hook_memory_write(const memory_context_t* ctx) {
  // Check bounds
  if (ctx->address + ctx->size > ctx->memory_size) {
    plugin_log("Out-of-bounds write at 0x%x", ctx->address);
    return (hook_result_t){
      .override = true,
      .result = 0,
      .error = -1  // Trap
    };
  }

  return (hook_result_t){ .override = false };
}
```

### 3.4 プラグインインポート（Plugin Imports - ランタイム提供）

プラグインは Fireball ランタイムから以下の関数をインポート可能です：

```c
/**
 * Logging
 */
__attribute__((import_module("fireball"), import_name("log")))
void plugin_log(const char* format, ...);

/**
 * Memory allocation
 */
__attribute__((import_module("fireball"), import_name("alloc")))
void* plugin_alloc(size_t size);

__attribute__((import_module("fireball"), import_name("free")))
void plugin_free(void* ptr);

/**
 * Get current guest module context
 */
__attribute__((import_module("fireball"), import_name("get_guest_module_id")))
uint32_t plugin_get_guest_module_id();

/**
 * Pause/resume guest execution
 */
__attribute__((import_module("fireball"), import_name("pause_guest")))
void plugin_pause_guest();

__attribute__((import_module("fireball"), import_name("resume_guest")))
void plugin_resume_guest();

/**
 * Trigger trap (terminate guest with error)
 */
__attribute__((import_module("fireball"), import_name("trap")))
void plugin_trap(const char* message);

/**
 * Access guest module metadata
 */
__attribute__((import_module("fireball"), import_name("get_guest_export")))
void* plugin_get_guest_export(const char* name);

/**
 * Call guest function from plugin
 */
__attribute__((import_module("fireball"), import_name("call_guest_function")))
int plugin_call_guest_function(const char* name, uint32_t* args, uint32_t args_count, uint32_t* result);
```

---

## 4. プラグイン例（Plugin Examples）

Fireball プラグインの具体的な実装例です。

### 4.1 命令カウンタープラグイン（Instruction Counter Plugin - C）

```c
// instruction_counter.c
#include "fireball_plugin.h"

static uint64_t instruction_count = 0;

__attribute__((export_name("plugin_get_metadata")))
const plugin_metadata_t* plugin_get_metadata() {
  static const plugin_metadata_t meta = {
    .api_version = 1,
    .name = "instruction_counter",
    .version = "1.0.0",
    .author = "Fireball Team",
    .description = "Counts executed instructions"
  };
  return &meta;
}

__attribute__((export_name("plugin_init")))
int plugin_init() {
  instruction_count = 0;
  plugin_log("Instruction counter initialized");
  return 0;
}

__attribute__((export_name("hook_any_instruction")))
hook_result_t hook_any_instruction(const instruction_context_t* ctx) {
  instruction_count++;
  return (hook_result){ .override = false };
}

__attribute__((export_name("plugin_get_count")))
uint64_t plugin_get_count() {
  return instruction_count;
}

__attribute__((export_name("plugin_cleanup")))
void plugin_cleanup() {
  plugin_log("Total instructions: %llu", instruction_count);
}
```

### 4.2 メモリガードプラグイン（Memory Guard Plugin - Rust）

```rust
// memory_guard.rs
use fireball_plugin_sdk::*;

#[no_mangle]
pub extern "C" fn plugin_get_metadata() -> &'static PluginMetadata {
    &PluginMetadata {
        api_version: 1,
        name: "memory_guard",
        version: "1.0.0",
        author: "Security Team",
        description: "Prevents out-of-bounds memory access",
    }
}

#[no_mangle]
pub extern "C" fn plugin_init() -> i32 {
    register_memory_hook(MemoryOp::Write, hook_memory_write, 0);
    register_memory_hook(MemoryOp::Read, hook_memory_read, 0);
    plugin_log("Memory guard enabled");
    0
}

#[no_mangle]
pub extern "C" fn hook_memory_write(ctx: &MemoryContext) -> HookResult {
    if ctx.address + ctx.size > ctx.memory_size {
        plugin_log("Blocked out-of-bounds write: addr=0x{:x}, size={}",
                   ctx.address, ctx.size);
        plugin_trap("Out-of-bounds memory access");
        return HookResult {
            override_default: true,
            result: 0,
            error: -1,
        };
    }
    HookResult::default()
}

#[no_mangle]
pub extern "C" fn hook_memory_read(ctx: &MemoryContext) -> HookResult {
    if ctx.address + ctx.size > ctx.memory_size {
        plugin_log("Blocked out-of-bounds read: addr=0x{:x}, size={}",
                   ctx.address, ctx.size);
        plugin_trap("Out-of-bounds memory access");
        return HookResult {
            override_default: true,
            result: 0,
            error: -1,
        };
    }
    HookResult::default()
}
```

### 4.3 システムコールロガープラグイン（Syscall Logger Plugin - AssemblyScript）

```typescript
// syscall_logger.ts
import {
  PluginMetadata,
  SyscallContext,
  HookResult,
  plugin_log,
  register_syscall_hook
} from "fireball-plugin-sdk";

export function plugin_get_metadata(): PluginMetadata {
  return {
    api_version: 1,
    name: "syscall_logger",
    version: "1.0.0",
    author: "Debug Team",
    description: "Logs all syscall invocations"
  };
}

export function plugin_init(): i32 {
  register_syscall_hook("*", hook_all_syscalls, 0);
  plugin_log("Syscall logger initialized");
  return 0;
}

export function hook_all_syscalls(ctx: SyscallContext): HookResult {
  let args_str = "";
  for (let i = 0; i < ctx.args_count; i++) {
    args_str += ctx.args[i].toString();
    if (i < ctx.args_count - 1) args_str += ", ";
  }

  plugin_log(`SYSCALL: ${ctx.name}(${args_str})`);

  return { override: false, result: 0, error: 0 };
}
```

### 4.4 カスタム命令プラグイン（Custom Instruction Plugin - C）

```c
// custom_crypto.c
#include "fireball_plugin.h"

// Define custom opcode (use unreserved range 0xFC 0x00-0xFF)
#define OPCODE_CUSTOM_AES_ENCRYPT 0xFC00

__attribute__((export_name("plugin_init")))
int plugin_init() {
  // Register handler for custom instruction
  register_instruction_hook(OPCODE_CUSTOM_AES_ENCRYPT, hook_aes_encrypt, 0);
  plugin_log("Custom AES instruction registered");
  return 0;
}

__attribute__((export_name("hook_aes_encrypt")))
hook_result_t hook_aes_encrypt(const instruction_context_t* ctx) {
  // Read parameters from stack
  uint32_t key_ptr = ctx->stack[ctx->stack_size - 1];
  uint32_t data_ptr = ctx->stack[ctx->stack_size - 2];
  uint32_t output_ptr = ctx->stack[ctx->stack_size - 3];

  // Access linear memory
  uint8_t* memory = (uint8_t*)ctx->memory;
  uint8_t* key = &memory[key_ptr];
  uint8_t* data = &memory[data_ptr];
  uint8_t* output = &memory[output_ptr];

  // Perform AES encryption (implementation omitted)
  aes_encrypt_block(key, data, output);

  // Override instruction (return nothing on stack)
  return (hook_result_t){
    .override = true,
    .result = 0,
    .error = 0
  };
}
```

---

## 5. プラグインマネージャ実装（Plugin Manager Implementation）

プラグイン管理の中核となるプラグインマネージャの設計と実装例です。

### 5.1 プラグインレジストリ（Plugin Registry）

```cpp
namespace fireball { namespace plugin {

/**
 * Plugin handle
 */
typedef uint32_t plugin_handle_t;

/**
 * Hook descriptor
 */
typedef struct {
  hook_type type;
  union {
    uint8_t opcode;           // For instruction hooks
    const char* name;         // For syscall hooks
  };
  void* callback;             // WASM function pointer
  hook_priority priority;
  plugin_handle owner;        // Owning plugin
} hook_descriptor_t;

/**
 * Plugin manager
 */
class plugin_manager {
 public:
  /**
   * Load plugin from WASM module
   */
  plugin_handle load_plugin(const uint8_t* wasm_data, size_t size);

  /**
   * Unload plugin
   */
  void unload_plugin(plugin_handle_t handle);

  /**
   * Get plugin metadata
   */
  const plugin_metadata* get_plugin_metadata(plugin_handle_t handle);

  /**
   * Register hook (called by plugin)
   */
  uint32_t register_hook(plugin_handle_t plugin, const hook_descriptor_t& hook);

  /**
   * Unregister hook
   */
  void unregister_hook(uint32_t hook_id);

  /**
   * Invoke instruction hooks
   */
  hook_result invoke_instruction_hooks(uint8_t opcode, const instruction_context_t* ctx);

  /**
   * Invoke syscall hooks
   */
  hook_result invoke_syscall_hooks(const char* name, const syscall_context_t* ctx);

 private:
  std::unordered_map<plugin_handle_t, wasm_module*> plugins_;
  std::multimap<uint8_t, hook_descriptor_t> instruction_hooks_;
  std::multimap<std::string, hook_descriptor_t> syscall_hooks_;
  uint32_t next_hook_id_ = 1;
};

} } // namespace fireball { namespace plugin {
```

### 5.2 フック呼び出し（Hook Invocation）

```cpp
// WASM インタプリタメインループ内
hook_result_t fireball::runtime::execute_instruction(uint8_t opcode) {
  // Build instruction context
  instruction_context_t ctx = {
    .opcode = opcode,
    .pc = current_frame->pc,
    .stack = value_stack.data(),
    .stack_size = value_stack.size(),
    // ...
  };

  // Invoke plugin hooks
  hook_result_t result = plugin_mgr->invoke_instruction_hooks(opcode, &ctx);

  if (result.override) {
    // Plugin overrode instruction, use plugin result
    return result;
  }

  // Execute default instruction
  return execute_default_instruction(opcode, &ctx);
}
```

---

## 6. セキュリティ考慮事項（Security Considerations）

プラグインシステムにおけるセキュリティ設計について説明します。

### 6.1 プラグイン隔離（Plugin Isolation）

セキュリティと安定性のため、プラグインは厳格に隔離されます：

- **独立したメモリ空間**: 各プラグインは独自の WASM インスタンスで実行
- **リソース制限**: CPU 時間、メモリ使用量のクォータ（プラグインごと）
- **能力ベースセキュリティ**: プラグインは読み込み時にパーミッションを要求

### 6.2 パーミッションシステム（Permissions System）

```c
/**
 * Plugin permissions
 */
typedef enum {
  PERM_HOOK_INSTRUCTIONS  = 0x0001,
  PERM_HOOK_SYSCALLS      = 0x0002,
  PERM_ACCESS_MEMORY      = 0x0004,
  PERM_PAUSE_GUEST        = 0x0008,
  PERM_CALL_GUEST         = 0x0010,
} plugin_permission_t;

/**
 * Request permissions (called in plugin_init)
 */
__attribute__((import_module("fireball"), import_name("request_permission")))
int plugin_request_permission(plugin_permission_t perm);
```

### 6.3 プラグイン検証（Plugin Verification）

- **署名検証**: 信頼できる認証局によって署名されたプラグイン
- **サンドボックス化**: プラグインはホストファイルシステムやネットワークに直接アクセス不可
- **監査ログ**: すべてのプラグイン操作をセキュリティレビュー用にログ

---

## 7. ビルドシステム（Build System）

### 7.1 プラグイン SDK（Plugin SDK）

```bash
# Fireball プラグイン SDK をインストール
npm install -g fireball-plugin-sdk

# 新規プラグインプロジェクトを作成
fireball-plugin init my-plugin --lang=rust

# プラグインをビルド
cd my-plugin
fireball-plugin build

# 出力：my-plugin.wasm
```

### 7.2 プラグインマニフェスト（Plugin Manifest）

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "My custom plugin",
  "api_version": 1,
  "permissions": [
    "hook_instructions",
    "hook_syscalls"
  ],
  "build": {
    "lang": "rust",
    "target": "wasm32-unknown-unknown",
    "optimize": true
  }
}
```

---

## 8. ディレクトリ構造（Directory Structure）

```
fireball/
├── inc/
│   └── plugin/
│       ├── plugin_api.h         # C plugin API
│       ├── plugin_manager.hxx   # C++ plugin manager
│       └── plugin_context.hxx   # Execution contexts
├── src/
│   └── plugin/
│       ├── plugin_manager.cxx
│       ├── plugin_loader.cxx
│       └── hook_dispatcher.cxx
├── sdk/
│   ├── c/
│   │   └── fireball_plugin.h
│   ├── rust/
│   │   └── fireball-plugin-sdk/
│   └── assemblyscript/
│       └── fireball-plugin-sdk/
├── plugins/
│   ├── instruction_counter/
│   ├── memory_guard/
│   └── syscall_logger/
└── docs/
    └── wasm-plugin-design.md
```

---

## 9. 将来の拡張（Future Enhancements）

プラグインシステムの将来的な改善予定について説明します。

### 9.1 ホットリロード（Hot Reload）

ランタイムを停止せずにプラグインを再読み込みする機能：

```cpp
// ランタイムを停止せずにプラグインを再読み込み
int plugin_reload(plugin_handle_t handle, const uint8_t* new_wasm_data, size_t size);
```

### 9.2 プラグインマーケットプレイス（Plugin Marketplace）

- プラグインレジストリ（検索/発見機能付き）
- バージョン管理
- 自動更新機能

### 9.3 マルチ言語 SDK（Multi-language SDK）

- C SDK（完了）
- Rust SDK（完了）
- AssemblyScript SDK（完了）
- Go SDK（TinyGo）
- Zig SDK

### 9.4 パフォーマンス最適化（Performance Optimizations）

- ホットプラグインパスの JIT コンパイル
- フックキャッシング
- 遅延フック評価

---

## まとめ（Summary）

Fireball プラグインシステムは、WASM ベースの拡張可能で安全なプラグインアーキテクチャを提供します。
プラグインは独立した WASM インスタンスで実行され、機能ベースのセキュリティモデルにより
信頼できないコードを安全に隔離できます。

このシステムにより、ユーザーはコアランタイムの変更なしに、
カスタム命令実装、パフォーマンス計装、セキュリティ検証などの機能を追加可能です。
