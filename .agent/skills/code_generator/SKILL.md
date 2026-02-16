---
name: Code Generation
description: >
  JSONデータ/WIT IDLからPythonスクリプトでC++コードを自動生成するメタスキル。
  WHEN: 命令セット定義, レジスタマップ生成, WITからのスタブ生成, 規則性のあるコードの追加・変更
  SCOPE: コード生成の手順と原則。生成対象のデータ定義は各コンポーネントのdataディレクトリを参照。
  RELATED: wasm_development（WIT定義の参照元）, fireball_vocabulary（生成コードの型語彙）
---

# Code Generator スキル

WIT IDLからC++ヘッダを自動生成し、品質チェックまで一貫して実行するスキル。

---

## 概要

**WIT-First開発**における自動生成ワークフローを提供します:
- WIT IDL → C++ヘッダの自動生成（wasm-tools使用）
- 生成コードの品質自動チェック（禁止パターン、命名規則）
- ビルドテスト統合

---

## 推奨実行環境

### VSCode devcontainer使用時（最優先）

VSCode内でdevcontainerが正常動作している場合、VSCodeターミナルで直接実行:

```bash
cd /workspaces/fireball
bash .agent/skills/code_generator/workflows/wit_all.sh
```

### VSCode以外のエディタ使用時

**Git Bash推奨**（Windows）:

```bash
cd {ワークスペースのパス}
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**PowerShellは非推奨** - execution policy問題があるため。

---

## メインワークフロー

### 統合実行（推奨）

```bash
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**処理内容**:
1. WIT→C++生成（14ファイル）
2. 禁止パターン検出（void*, malloc等）
3. 命名規則検証（snake_case等）
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

## 生成スクリプト

### wit_to_cpp.py（公式）

wasm-toolsベースのWIT→C++変換器。

**特徴**:
- パッケージ全体処理
- 依存関係自動解決
- ビットフィールド対応（`@bitfield`）
- Contract抽出（`@pre/@post/@inv`）

**使用例**:
```bash
python3 .agent/skills/code_generator/scripts/wit_to_cpp.py wit/ inc/gen
```

---

## 品質チェックスクリプト

### check_violations.py

禁止パターン検出:
- `void*`, `malloc/free`, `new/delete`
- `std::vector/map/string`
- `try/catch/throw`

### check_naming.py

命名規則検証:
- Type: `snake_case`
- Enum値: `UPPER_SNAKE_CASE`

---

## WIT仕様準拠

### 必須ルール

- **ケバブケース**: `device-id` (not `device_id`)
- **予約語回避**: `stream-device` (not `stream`)
- **interface分離**: `export` は world 内で使用

### ビットフィールド記法

```wit
/// @bitfield type_scope:u8:0-7, key:u24:8-31, value:u32:32-63
record kv-pair {
    raw: u64,
}
```

生成されるC++:
```cpp
struct kv_pair {
  uint64_t type_scope : 8;
  uint64_t key : 24;
  uint64_t value : 32;
};
```

---

## ファイル構成

```
.agent/skills/code_generator/
├── scripts/
│   ├── wit_to_cpp.py          # 公式生成スクリプト
│   ├── check_violations.py    # 禁止パターン検出
│   ├── check_naming.py        # 命名規則検証
│   └── deprecated/
└── workflows/
    ├── wit_gen.sh             # 生成
    ├── wit_check.sh           # チェック
    ├── wit_build.sh           # ビルド
    ├── wit_all.sh             # 統合
    └── README.md
```

---

## トラブルシューティング

### WIT構文エラー

```
error: expected kebab-case identifier
```
→ `device_id` を `device-id` に修正

### wasm-tools not found

VSCode devcontainer内で実行するか、Docker経由で実行してください。

### PowerShell execution policy

Git Bashを使用してください。

---

## Docker workaround

VSCode devcontainerが使えない場合は、`docker_workaround` スキルを参照してください。

詳細: [docker_workaround/SKILL.md](../docker_workaround/SKILL.md)
