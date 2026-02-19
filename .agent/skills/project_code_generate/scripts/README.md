# WIT Code Generator Scripts

公式のWIT→C++ヘッダ自動生成ツール。wasm-toolsベースで全WITパッケージを一括処理します。

---

## 推奨スクリプト

### generate_cpp.py (公式)

**wasm-tools**ベースのパッケージ全体パーサー。

**特徴**:
- ✅ wasm-tools公式パーサー使用
- ✅ パッケージ全体の依存関係解決
- ✅ ビットフィールド対応（@bitfield）
- ✅ 全インターフェイス一括生成
- ✅ Contract対応

**使用方法**:

```bash
# Docker内で実行（推奨）
docker exec <container-id> bash -c "cd /workspaces/fireball && python3 .agent/skills/project_code_generate/scripts/generate_cpp.py wit/ inc/gen"

# または簡略版
python3 .agent/skills/project_code_generate/scripts/generate_cpp.py wit/ inc/gen
```

**生成例**:
```
Parsing WIT package: wit/
Generating C++ headers to inc/gen...
Generated: inc/gen/types.hxx
Generated: inc/gen/services.hxx
Generated: inc/gen/hal.hxx
Generated: inc/gen/vsoc.hxx
...
Done!
```

---

## ディレクトリ構造

```
scripts/
├── generate_cpp.py          # 公式スクリプト（wasm-tools版）
├── deprecated/            # 非推奨スクリプト（参考用）
│   ├── wit_to_cpp_manual.py    # 旧手動パーサー
│   ├── wit_to_cpp_v2.py        # プレースホルダー
│   ├── wit_to_cpp_wasm.py      # 単一ファイル版（非推奨）
│   └── generate_all.py         # 旧バッチスクリプト
└── README.md              # このファイル
```

---

## 生成ファイル

`generate_cpp.py`は以下を生成します:

| WIT Interface | 生成ファイル | 内容 |
|:---|:---|:---|
| types | types.hxx | 基本型、enum、**kv_pair bitfield** |
| services | services.hxx | IPC Router、Logger |
| hal | hal.hxx, hal_types.hxx | HAL、GPIO、Timer |
| vsoc | vsoc.hxx, vsoc_types.hxx | vSoC Runtime |
| jit | jit.hxx, jit_types.hxx | JIT Compiler |
| coos | coos.hxx, coos_types.hxx | COOS Scheduler |
| memory | memory.hxx, memory_types.hxx | Memory Manager |
| guest-api | guest_api.hxx | Guest API |

---

## トラブルシューティング

### wasm-tools not found

```bash
# Dockerコンテナ内で実行してください
docker exec <container-id> bash -c "wasm-tools --version"
```

### WIT構文エラー

wasm-toolsはWIT仕様に厳格です:
- ✅ ケバブケース: `device-id`, `operation-result`
- ❌ スネークケース: `device_id`, `operation_result`
- ✅ 予約語回避: `stream-device`
- ❌ 予約語使用: `stream`

---

## 旧スクリプト（非推奨）

`deprecated/`フォルダのスクリプトは参考用です。新規開発には使用しないでください。

### wit_to_cpp_manual.py
手動パーサー版。wasm-toolsなしで動作しますが、WIT仕様変更に追従できません。

### wit_to_cpp_wasm.py
単一ファイル版。依存関係の問題があり、パッケージ版に置き換えられました。

---

## 開発者向け

### スクリプトの動作

1. `wasm-tools component wit wit/ --json` でWITパッケージをJSONに変換
2. JSONをパースしてインターフェイス、型、リソースを抽出
3. C++ヘッダを生成:
   - Type alias → `using`
   - Enum → `enum class`
   - Record → `struct` (または bitfield)
   - Resource → `*_interface` (抽象クラス)

### ビットフィールド対応

WITのdocコメントに`@bitfield`アノテーションを記述:

```wit
/// @bitfield type_scope:u8:0-7, key:u24:8-31, value:u32:32-63
record kv-pair {
    raw: u64,
}
```

生成されるC++:
```cpp
struct kv_pair {
  uint64_t type_scope : 8;   // Bits 0-7
  uint64_t key : 24;         // Bits 8-31
  uint64_t value : 32;       // Bits 32-63
};
static_assert(sizeof(kv_pair) == 8, "kv_pair size mismatch");
```

---

## 更新履歴

- **2026-02-16**: wasm-toolsパッケージ版を公式化
- **2026-02-16**: WITファイルをwasm-tools準拠に修正
- **2026-02-16**: 旧スクリプトをdeprecatedに移動
