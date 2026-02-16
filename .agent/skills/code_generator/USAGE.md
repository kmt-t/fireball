# Code Generator - 使用方法

WIT IDLからC++ヘッダを自動生成するワークフローの詳細ドキュメント。

---

## 公式スクリプト

### wit_to_cpp.py（wasm-tools版）

**Source of Truth**: wasm-tools公式パーサー使用

**特徴**:
- ✅ WITパッケージ全体処理
- ✅ 依存関係自動解決
- ✅ ビットフィールド対応（`@bitfield`）
- ✅ Contract抽出（`@pre/@post/@inv`）
- ✅ 14インターフェイス一括生成

**使用方法**:
```bash
python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen
```

**内部処理**:
1. `wasm-tools component wit wit/ --json` でJSON変換
2. JSONから型、インターフェイス、リソースを抽出
3. C++ヘッダ生成

---

## 実行環境

### VSCode devcontainer（推奨）

```bash
cd /workspaces/fireball
bash .agent/skills/code_generator/workflows/wit_all.sh
```

### Git Bash（VSCode以外）

```bash
cd {ワークスペースのパス}
bash .agent/skills/code_generator/workflows/wit_all.sh
```

---

## ワークフロー

### 統合実行（推奨）

```bash
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**処理内容**:
1. WIT→C++生成（14ファイル）
2. 禁止パターン検出
3. 命名規則検証
4. ビルドテスト（オプション）

### 個別実行

```bash
# 生成のみ
bash .agent/skills/code_generator/workflows/wit_gen.sh

# チェックのみ
bash .agent/skills/code_generator/workflows/wit_check.sh

# ビルドのみ
bash .agent/skills/code_generator/workflows/wit_build.sh
```

---

## 生成例

### 入力（WIT）

```wit
/// IPC Router interface
/// @inv: registry_count <= FB_CONF_ROUTER_MAX_SERVICES
resource ipc-router {
    /// Bind service
    /// @pre: sid < FB_CONF_ROUTER_MAX_SERVICES
    /// @post: result.is_ok() -> channel is valid
    bind: func(sid: service-id, address: uri-handle) 
        -> result<channel-id, recovery-strategy>;
}
```

### 出力（C++）

```cpp
/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <cstdint>

/**
 * IPC Router interface
 * @invariant registry_count <= FB_CONF_ROUTER_MAX_SERVICES
 */
struct ipc_router_interface {
  /**
   * Bind service
   * @pre sid < FB_CONF_ROUTER_MAX_SERVICES
   * @post result.is_ok() -> channel is valid
   */
  virtual result<channel_id, recovery_strategy> 
    bind(service_id sid, uri_handle address) = 0;
  
  virtual ~ipc_router_interface() = default;
};
```

---

## ビットフィールド対応

### WIT定義

```wit
/// @bitfield type_scope:u8:0-7, key:u24:8-31, value:u32:32-63
record kv-pair {
    raw: u64,
}
```

### 生成されるC++

```cpp
/**
 * IPC Key-Value pair
 */
struct kv_pair {
  uint64_t type_scope : 8;   // Bits 0-7
  uint64_t key : 24;         // Bits 8-31
  uint64_t value : 32;       // Bits 32-63
};
static_assert(sizeof(kv_pair) == 8, "kv_pair size mismatch");
```

---

## 型マッピング

| WIT型 | C++型 |
|:---|:---|
| `u8`, `u16`, `u32`, `u64` | `uint8_t`, `uint16_t`, `uint32_t`, `uint64_t` |
| `s8`, `s16`, `s32`, `s64` | `int8_t`, `int16_t`, `int32_t`, `int64_t` |
| `bool` | `bool` |
| `string` | `std::string_view` |
| `list<u8>` | `std::span<const uint8_t>` |
| `result<T, E>` | `result<T, E>` |
| `option<T>` | `std::optional<T>` |

---

## 品質チェック

### check_violations.py

禁止パターン検出:

```bash
python3 .agent/skills/code_generator/scripts/check_violations.py inc/gen
```

**検出項目**:
- `void*` 使用
- `malloc/free/new/delete`
- `std::vector/map/string`
- `try/catch/throw`
- `using namespace std`

### check_naming.py

命名規則検証:

```bash
python3 .agent/skills/code_generator/scripts/check_naming.py inc/gen
```

**検証項目**:
- Type: `snake_case`
- Enum値: `UPPER_SNAKE_CASE`
- using宣言: `snake_case`

---

## 生成されるファイル

```
inc/gen/
├── types.hxx          # 基本型定義
├── services.hxx       # IPC Router, Logger
├── hal.hxx            # HAL インターフェイス
├── hal_types.hxx
├── vsoc.hxx           # Virtual SoC
├── vsoc_types.hxx
├── jit.hxx            # JIT Compiler
├── jit_types.hxx
├── coos.hxx           # COOS Scheduler
├── coos_types.hxx
├── memory.hxx         # Memory Manager
├── memory_types.hxx
├── guest_api.hxx      # Guest API
└── trap.hxx           # Trap Handlers
```

**合計14ファイル**

---

## トラブルシューティング

### WIT構文エラー

```
error: expected kebab-case identifier
```

**原因**: スネークケース使用
**解決**: `device_id` → `device-id`

### wasm-tools not found

**原因**: コンテナ外で実行
**解決**: VSCode devcontainer内またはDocker経由で実行

### 生成されたコードがビルドできない

```bash
# チェック実行
bash .agent/skills/code_generator/workflows/wit_check.sh
```

---

## 非推奨スクリプト

以下は古いバージョンです:

- ~~`wit_to_cpp_manual.py`~~ - 手動パーサー版（deprecated/に移動）
- ~~`wit_generator.py`~~ - 削除済み
- ~~`generator.py`~~ - 削除済み

**公式版のみ使用してください**: `wit_to_cpp.py`
