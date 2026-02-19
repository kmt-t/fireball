# WIT Code Generation Workflow

完全自動化されたWIT→C++ヘッダ生成ワークフロー。

**VSCode devcontainer内でも、WSL2 Bashでも動作します！**

---

## ワークフロー概要

```
WIT編集 → 生成 → 品質チェック → (ビルド) → 完了
          ↓        ↓              ↓
     generate-code.sh  check-quality.sh  build-project.sh
                    ↓
            自動品質チェック:
            - 禁止パターン検出
            - 命名規則検証
```

---

## スクリプト

### 1. generate-code.sh - C++ヘッダ生成

WITパッケージからC++ヘッダを自動生成。

```bash
bash .agent/skills/project_code_generate/workflows/generate-code.sh
```

**処理内容**:
- wasm-toolsでWIT→JSON変換
- JSONからC++ヘッダ生成（14ファイル）

**環境自動判定**:
- VSCode devcontainer内: 直接実行
- WSL2 Bash等: Docker exec経由

---

### 2. check-quality.sh - 品質チェック ⭐

生成されたC++コードを自動チェック。**パワハラ回避の要**。

```bash
bash .agent/skills/project_code_generate/workflows/check-quality.sh
```

**チェック項目**:

#### [1/2] 禁止パターン検出 (`check_violations.py`)
- ❌ `void*` 使用
- ❌ `malloc/free/new/delete`
- ❌ `std::vector/map/string` (embedded禁止)
- ❌ `try/catch/throw` (例外禁止)

#### [2/2] 命名規則検証 (`check_naming.py`)
- ✅ Type: `snake_case`
- ✅ Enum値: `UPPER_SNAKE_CASE`

---

### 3. build-project.sh - ビルドテスト

生成されたヘッダでビルドテスト。

```bash
bash .agent/skills/project_code_generate/workflows/build-project.sh
```

---

### 4. run-workflow.sh - 統合ワークフロー ⭐⭐

生成→チェック→ビルドを一発実行。

```bash
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**処理フロー**:
1. `generate-code.sh` - 生成
2. `check-quality.sh` - チェック
3. `build-project.sh` - ビルド（オプション）

**オプション**:
- `--no-build`: ビルドスキップ

---

## 使い方

### VSCode devcontainer内

```bash
# VSCodeターミナルで直接実行
cd /workspaces/fireball
bash .agent/skills/project_code_generate/workflows/run-workflow.sh
```

### WSL2 Bash（外部）

```bash
# WSL2 Bashで実行
cd /n/sources/fireball
bash .agent/skills/code_generator/workflows/wit_all.sh
```

**どちらの環境でも同じコマンドで動作します！**

---

## 出力例

### 成功時

```
[*] Generating C++ headers from WIT package...
[*] Running inside container
[OK] Generation complete

[*] Running quality checks on generated code...
  [1/2] Checking prohibited patterns...
[OK] No violations found
  [2/2] Checking naming conventions...
[OK] All naming conventions correct

[OK] All quality checks passed!
```

---

## ディレクトリ構成

```
.agent/skills/project_code_generate/
├── scripts/
│   ├── generate_cpp.py          # メイン生成スクリプト
│   ├── check_violations.py    # 禁止パターン検出 ⭐
│   ├── check_naming.py        # 命名規則検証 ⭐
│   └── deprecated/
└── workflows/
    ├── generate-code.sh       # 生成
    ├── check-quality.sh       # チェック ⭐⭐
    ├── build-project.sh       # ビルド
    └── run-workflow.sh        # 統合 ⭐⭐⭐
```

---

## パワハラ回避チェックリスト

- ✅ 禁止パターン自動検出
- ✅ 命名規則自動検証
- ✅ ワンコマンド実行
- ✅ エラー箇所明示
- ✅ 目視チェック不要
- ✅ **VSCode/WSL2 Bash (`bash`) 両対応**

**これで明日も安心！**🌸
