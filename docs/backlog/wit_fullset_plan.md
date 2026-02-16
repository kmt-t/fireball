# WIT フルセット化とビットフィールド自動生成 実装計画

WIT定義の完全化とIPC周りのビットフィールド構造の自動生成対応を、盆栽的に段階的に実現する。

## 問題の整理

### 現状の仕様齟齬

#### 1. `kv_pair` の二重定義問題

**[router.md](file:///n:/sources/fireball/docs/components/router.md#L34-L41)**:
```
kv_pair (Key-Valueペア)
- 型スコープ: 8bit (ビットフラグ)
- 識別キー: 24bit (ID値)
- 属性値: 32bit (値)
```

**[types.wit](file:///n:/sources/fireball/wit/types.wit#L33-L37)**:
```wit
record key-value {
    key: u32,
    value: shm-id,
}
```

**問題点**:
- 設計仕様 (`router.md`) ではビットフィールド構造 (8+24+32 = 64bit)
- WIT定義 (`types.wit`) では単純な2フィールド構造 (32+32 = 64bit)
- ビットレイアウトが異なり、型スコープの情報が失われている

#### 2. 自動生成ツールの不在

- `wit_to_cpp.py` のようなコード生成スクリプトが存在しない
- 手動でWIT → C++ヘッダを記述する必要がある
- 仕様書とWITとC++実装の3者間で同期が取れていない可能性

#### 3. WITで表現困難な型

- `std::span<T>` のような参照型 (`binary_view`, `mutable_binary_view`)
- ビットフィールド構造
- `constexpr` による静的サイズ定義
- テンプレート型 (`result<T, E>`, `optional<T>`)

---

## User Review Required

> [!IMPORTANT]
> **設計判断の確認事項**
>
> 以下の方針について、ユーザの意見・方向性の確認が必要です。

### 1. ビットフィールド構造のWIT表現方法

**Option A: WIT拡張アノテーション (推奨)**

```wit
/// Bitfield layout: [scope:8][key:24][value:32]
/// @bitfield scope:u8:0-7, key:u24:8-31, value:u32:32-63
record kv-pair {
    raw: u64,  // アクセスはヘルパー関数経由
}
```

- ✅ WIT IDLとして合法
- ✅ コメントベースで自動生成ヒントを埋め込める
- ❌ カスタムパーサーが必要

**Option B: 別ファイルでメタデータ定義**

```yaml
# ipc_types.yaml
kv_pair:
  layout: bitfield
  total_bits: 64
  fields:
    - name: scope
      type: u8
      bits: 0-7
    - name: key
      type: u24
      bits: 8-31
    - name: value
      type: u32
      bits: 32-63
```

- ✅ WITを汚さない
- ✅ JSONスキーマで拡張可能
- ❌ 定義が分散する

**Option C: C++側で手動定義 + WITは簡略版**

```wit
// WITは型の存在だけ定義
record kv-pair {
    data: u64,
}
```

```cpp
// C++側でビットフィールド構造を手動定義
struct kv_pair {
  uint64_t scope : 8;
  uint64_t key : 24;
  uint64_t value : 32;
};
```

- ✅ 実装が素直
- ❌ WIT-First原則に反する
- ❌ 自動生成の意味が薄れる

### 2. 自動生成ツールのスコープ

**Question**: どこまでを自動生成対象とするか?

- [ ] **Minimal**: WIT primitiveのみ (record, enum, flags)
- [ ] **Moderate**: + ビットフィールド構造
- [ ] **Full**: + constexpr定数, 型エイリアス, Contract検証コード

---

## Proposed Changes

アプローチ: **段階的実装 (Incremental Approach)**

盆栽の原則に従い、全体を見ながら少しずつ整える。

### Phase 1: WIT定義の整合性確保

#### [MODIFY] [types.wit](file:///n:/sources/fireball/wit/types.wit)

```diff
 /// Key-Value pair for structured messaging.
+/// Bitfield layout: [scope:8][key:24][value:32]
+/// @bitfield scope:u8:0-7, key:u24:8-31, value:u32:32-63
-record key-value {
-    key: u32,
-    value: shm-id,
-}
+record kv-pair {
+    raw: u64,
+}
+
+/// Message containing up to 8 KV pairs.
+record message {
+    pairs: list<kv-pair>,  // Fixed size: 8
+}
```

**変更理由**:
- `router.md` の仕様と一致させる
- ビットフィールドレイアウトをコメントで明示
- 将来の自動生成ツールがパース可能な形式

---

#### [MODIFY] [services.wit](file:///n:/sources/fireball/wit/services.wit)

```diff
 interface services {
-    use types.{uri-handle, channel-id, task-id, service-id, key-value, operation-result, recovery-strategy, log-level, shm-id};
+    use types.{uri-handle, channel-id, task-id, service-id, kv-pair, message, operation-result, recovery-strategy, log-level, shm-id};

-    type message-handle = shm-id;
+    // Remove redundant type alias, use 'message' directly

     resource ipc-router {
         bind: func(sid: service-id, address: uri-handle) -> result<channel-id, recovery-strategy>;
         connect: func(address: uri-handle) -> result<service-id, recovery-strategy>;
-        send: func(chan: channel-id, msg: message-handle) -> operation-result;
-        recv: func(chan: channel-id) -> result<message-handle, recovery-strategy>;
+        send: func(chan: channel-id, msg: message) -> operation-result;
+        recv: func(chan: channel-id) -> result<message, recovery-strategy>;
     }
 }
```

---

### Phase 2: 既存ツールの拡張 (ビットフィールド対応)

> [!NOTE]
> **既存ツールの発見**: `.agent/skills/code_generator/scripts/wit_to_cpp.py` が既に存在し、基本的なWIT→C++変換は完成している。
> 本フェーズでは、このツールにビットフィールド生成機能を追加する。

#### [MODIFY] [scripts/wit_to_cpp.py](file:///n:/sources/fireball/.agent/skills/code_generator/scripts/wit_to_cpp.py)

**追加機能**:
1. `@bitfield` アノテーションのパース
2. C++ bitfield構造体の生成
3. `static_assert` によるサイズ検証コードの挿入

**変更箇所**:

```python
# Line 51-59: parse_contracts メソッドの拡張
def parse_contracts(self, doc_buffer):
    contracts = {"pre": [], "post": [], "inv": [], "bitfield": None}
    general_doc = []
    for d in doc_buffer:
        if d.startswith("@pre:"): contracts["pre"].append(d[5:].strip())
        elif d.startswith("@post:"): contracts["post"].append(d[6:].strip())
        elif d.startswith("@inv:"): contracts["inv"].append(d[5:].strip())
        elif d.startswith("@bitfield"):  # NEW: bitfield annotation
            contracts["bitfield"] = d[10:].strip()  # "scope:u8:0-7, key:u24:8-31, ..."
        else: general_doc.append(d)
    return general_doc, contracts
```

```python
# Line 188-196: generate メソッドの record 生成部分を拡張
for rec in iface['records']:
    if rec['doc']:
        f.write("/**\n")
        for d in rec['doc']: f.write(f" * {d}\n")
        f.write(" */\n")
    
    # NEW: Check for bitfield annotation
    bitfield_spec = rec.get('bitfield_spec')
    if bitfield_spec:
        # Generate C++ bitfield struct
        f.write(f"struct {rec['name']} {{\n")
        total_bits = 0
        for field in bitfield_spec:
            f.write(f"  {field['type']} {field['name']} : {field['width']};  // Bits {field['start']}-{field['end']}\n")
            total_bits += field['width']
        f.write("};\n")
        f.write(f"static_assert(sizeof({rec['name']}) == {total_bits // 8}, \"{rec['name']} size mismatch\");\n\n")
    else:
        # Original logic for normal structs
        f.write(f"struct {rec['name']} {{\n")
        for fname, ftype in rec['fields']:
            f.write(f"  {ftype} {fname};\n")
        f.write("};\n\n")
```

**実装例の出力**:

入力 (WIT with `@bitfield`):
```wit
/// IPC Key-Value pair.
/// @bitfield scope:u8:0-7, key:u24:8-31, value:u32:32-63
record kv-pair {
  raw: u64,
}
```

出力 (C++):
```cpp
/**
 * IPC Key-Value pair.
 */
struct kv_pair {
  uint64_t scope : 8;   // Bits 0-7
  uint64_t key : 24;    // Bits 8-31
  uint64_t value : 32;  // Bits 32-63
};
static_assert(sizeof(kv_pair) == 8, "kv_pair size mismatch");
```

---

**生成例**:

```cpp
#pragma once
#include <cstdint>

namespace fireball::ipc {

// Generated from types.wit::kv-pair
// Bitfield layout: [scope:8][key:24][value:32]
struct kv_pair {
  uint64_t scope : 8;   // Bits 0-7
  uint64_t key : 24;    // Bits 8-31
  uint64_t value : 32;  // Bits 32-63
  
  static_assert(sizeof(kv_pair) == 8, "kv_pair must be 64 bits");
};

// Generated from types.wit::message
struct message {
  kv_pair pairs[8];  // Fixed-size array
};

}  // namespace fireball::ipc
```

#### [NEW] [scripts/bitfield_metadata.json](file:///n:/sources/fireball/scripts/bitfield_metadata.json)

ビットフィールド構造のメタデータ (Option Bの場合)。

```json
{
  "kv_pair": {
    "total_bits": 64,
    "fields": [
      {"name": "scope", "type": "uint8_t", "offset": 0, "width": 8},
      {"name": "key", "type": "uint32_t", "offset": 8, "width": 24},
      {"name": "value", "type": "uint32_t", "offset": 32, "width": 32}
    ]
  }
}
```

---

### Phase 3: 生成されたヘッダの配置

#### [NEW] [inc/generated/types.hxx](file:///n:/sources/fireball/inc/generated/types.hxx)

自動生成されたC++ヘッダの配置先。手動編集禁止。

```cpp
// THIS FILE IS AUTO-GENERATED FROM wit/types.wit
// DO NOT EDIT MANUALLY
#pragma once

#include <cstdint>
#include <span>

namespace fireball::types {

// Type aliases (from Fireball Vocabulary)
using address = uint32_t;
using byte_count = uint32_t;
using byte_offset = uint32_t;
using channel_id = uint32_t;
using task_id = uint32_t;
using service_id = uint32_t;
using shm_id = uint32_t;

// Recovery strategy enum
enum class recovery_strategy : uint8_t {
  IGNORE = 0,
  RETRY = 1,
  RESTART = 2,
  PANIC = 3
};

// KV Pair (bitfield)
struct kv_pair {
  uint64_t scope : 8;
  uint64_t key : 24;
  uint64_t value : 32;
};

// Message
struct message {
  kv_pair pairs[8];
};

}  // namespace fireball::types
```

---

## Verification Plan

### Automated Tests

#### 1. WITパーサーのユニットテスト

```bash
# Location: scripts/tests/test_wit_parser.py
python -m pytest scripts/tests/test_wit_parser.py -v
```

**検証内容**:
- WITファイルの正常パース
- `@bitfield` アノテーションの抽出
- 型変換ルールの正確性

#### 2. 生成コードのコンパイルテスト

```bash
# Location: tests/codegen/
mkdir -p build/codegen_test
cd build/codegen_test
cmake ../../tests/codegen -DCMAKE_CXX_COMPILER=g++
make
```

**検証内容**:
- 生成されたヘッダファイルがコンパイル可能
- `static_assert` が通る
- 命名規則準拠 (Lintチェック)

#### 3. ビットフィールドレイアウトの検証

```cpp
// tests/codegen/test_bitfield_layout.cxx
#include "generated/types.hxx"
#include <cassert>

void test_kv_pair_layout() {
  using namespace fireball::types;
  
  kv_pair kv{};
  kv.scope = 0xFF;
  kv.key = 0xABCDEF;
  kv.value = 0x12345678;
  
  uint64_t expected = 0x12345678ABCDEFFULL;
  assert(*reinterpret_cast<uint64_t*>(&kv) == expected);
}
```

```bash
./build/codegen_test/test_bitfield_layout
```

### Manual Verification

#### 1. WIT → C++ 生成の手動確認

```bash
# Step 1: 生成スクリプトの実行
python scripts/wit_to_cpp.py --input wit/types.wit --output inc/generated/types.hxx

# Step 2: 生成されたファイルのレビュー
cat inc/generated/types.hxx

# 確認ポイント:
# - ビットフィールドのレイアウトがrouter.mdと一致しているか
# - 命名規則が snake_case になっているか
# - static_assert が挿入されているか
# - コメントにWITソースへの参照が含まれているか
```

#### 2. トレーサビリティマトリクスのレビュー

```bash
# Step 1: マトリクス生成 (手動またはスクリプト)
# 出力: docs/traceability/matrix_20260216_0912.md

# Step 2: レビュー内容
# - WIT定義 → C++型 の対応が記載されているか
# - router.md の `{Keyword}` が追跡可能か
# - 漏れている型定義がないか
```

**レビュー基準**:
- すべてのWIT `record` がC++ `struct` に対応している
- すべてのWIT `enum` がC++ `enum class` に対応している
- ビットフィールドアノテーションがC++ bitfield構造に反映されている

---

## 残された設計判断 (Design Decisions for Review)

1. **ビットフィールド表現方法**: Option A (アノテーション) vs Option B (YAML) vs Option C (手動)
2. **自動生成スコープ**: Minimal vs Moderate vs Full
3. **生成ヘッダの配置**: `inc/generated/` vs `inc/wit/`
4. **WITファイルの分割粒度**: 1ファイル vs コンポーネント単位
5. **C++ namespace構造**: `fireball::types` vs `fireball::ipc::types`

これらは次のフィードバックラウンドで詰めていく。
