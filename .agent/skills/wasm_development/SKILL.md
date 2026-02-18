---
name: WASM Development
description: >-
  WebAssembly/WASI仕様, WAMR実装, LLVMバックエンド定義の参照パスと調査手順。
  WHEN: WASM命令の挙動確認, バイナリフォーマット調査, WASI API設計, JITバックエンド実装
  SCOPE: 外部リファレンスへのナビゲーション。Fireball固有の設計判断はfireball_architectureを参照。
  RELATED: code_generator（WIT定義からのコード生成）, fireball_architecture（WIT-First原則）
---

# WASM Development

WebAssemblyエコシステム（Spec, WASI, WAMR, LLVM）に関連するリソースを参照し、設計・実装を支援するための統合スキルです。

## 1. WebAssembly Core Spec

プロジェクト内の `docs/references/webassembly/document/core/` を参照し、正確な仕様を確認します。

- **Base Path**: `docs/references/webassembly/document/core/`

### 主なディレクトリ
| ディレクトリ | 内容 | 活用シーン |
|:---|:---|:---|
| `syntax/` | 抽象構文（Types, Instructions） | 命令セットや型の定義確認 |
| `binary/` | バイナリフォーマット | デコーダ実装、バイナリ構造確認 |
| `exec/` | 実行セマンティクス | スタック操作、命令の挙動詳細 |
| `valid/` | バリデーションルール | モジュールの妥当性チェック論理 |

> **Tip**: 仕様の挙動に迷った場合は、`exec/` 下の記述を正解として扱ってください。

---

## 2. WASI Spec

プロジェクト内の `docs/references/wasi/` を参照し、システムインターフェイス（WASI）の設計を支援します。

- **Base Path**: `docs/references/wasi/`

### 調査手順
1. **`proposals/`**: 機能ごとのWIT/Markdown定義を確認（`filesystem`, `io`, `sockets` 等）。
2. **`.wit` ファイル**: 関数シグネチャと型定義のマスターとして参照。
3. **`.md` ファイル**: セマンティクスと制約の確認。

---

## 3. WAMR Implementation

`docs/references/wamr/` にある WAMR (WebAssembly Micro Runtime) のソースコードを調査し、実装詳細を確認します。

- **Base Path**: `docs/references/wamr/`

### 主なパス
| パス | 内容 |
|:---|:---|
| `core/iwasm/interpreter/` | インタープリタ実装（メインループ: `wasm_interp.c`） |
| `core/iwasm/aot/` | AOT/JITコンパイラ実装 |
| `core/iwasm/libraries/` | WASI等のライブラリ実装（例: `libc-wasi` マッピング） |
| `core/shared/utils/` | 共有ユーティリティ（メモリ管理、コレクション） |

---

## 4. LLVM Backend Definition

各アーキテクチャの命令エンコード規則やレジスタ定義を確認するために、LLVMのターゲット定義ファイル（`.td`）を参照します。

- **Base Path**: `docs/references/llvm/llvm/lib/Target/`

### 重要なファイル (.td)
命令セットのエンコーディングやパターンマッチングルールは、通常 `*InstrInfo.td` に記述されています。

- **WebAssembly**: `docs/references/llvm/llvm/lib/Target/WebAssembly/WebAssemblyInstrInfo.td`
- **x64 (x86-64)**: `docs/references/llvm/llvm/lib/Target/X86/X86InstrInfo.td`
- **ARMv8-M (Cortex-M)**: `docs/references/llvm/llvm/lib/Target/ARM/ARMInstrInfo.td`
- **RISC-V**: `docs/references/llvm/llvm/lib/Target/RISCV/RISCVInstrInfo.td`

### 調査方法
1. `grep_search` で命令名（例: `i32.add`, `ADDI`）を検索する。
2. `.td` ファイル内の `def` 定義を確認し、ビットパターン (`Inst{...}`) やアセンブリ文字列を確認する。

---

## 5. Standard Include Paths (for explorer-cli)

`explorer-cli summary` を WAMR リファレンスのコードに対して実行する際、正確な AST 解析のために以下のインクルードパスの指定が推奨されます。

### WAMR Core Includes
- `-I inc` (Project local headers)
- `-I docs/references/wamr/core/iwasm/include`
- `-I docs/references/wamr/core/shared/utils`
- `-I docs/references/wamr/core/shared/platform/include`

### Usage Example
```bash
explorer-cli summary docs/references/wamr/core/iwasm/fast-jit/jit_compiler.c \
  -I inc \
  -I docs/references/wamr/core/iwasm/include \
  -I docs/references/wamr/core/shared/utils
```

## 6. One-Shot Analysis Commands (Docker)

WAMRのソースコードや仕様書を素早く確認するためのコマンドです。
**実行環境についての詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。**

**Note**: Windows環境では **Git Bash** を使用してください。

### Interpreter (Main Loop)
```bash
bash .agent/skills/docker_workaround/scripts/docker-explorer.sh summary docs/references/wamr/core/iwasm/interpreter/wasm_interp_fast.c -I inc -I docs/references/wamr/core/iwasm/include -I docs/references/wamr/core/shared/utils -I docs/references/wamr/core/shared/platform/include
```

### AOT/JIT Loader
```bash
bash .agent/skills/docker_workaround/scripts/docker-explorer.sh summary docs/references/wamr/core/iwasm/aot/aot_loader.c -I inc -I docs/references/wamr/core/iwasm/include -I docs/references/wamr/core/shared/utils -I docs/references/wamr/core/shared/platform/include
```

### WebAssembly Specs
WASMコア仕様とWASIのドキュメントを一括要約します。
```bash
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find docs/references/webassembly docs/references/wasi -maxdepth 3 -name "*.md" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary
```

### RISC-V Specs
RISC-V関連のドキュメントを一括要約します。
```bash
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find docs/references/riscv -maxdepth 3 -name "*.md" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary
```

### ARM & Others
ARMアーキテクチャやその他の外部参照リストを確認します。
```bash
bash .agent/skills/docker_workaround/scripts/docker-cmd.sh find docs/references -maxdepth 1 -name "REFERENCES.md" | bash .agent/skills/docker_workaround/scripts/docker-explorer.sh pipe summary
```

## 8. 環境・前提条件

本スキルの実行には **Dockerコンテナ** の使用を強く推奨します。

- **Docker Workaround**: 詳細は [Docker Workaround](../docker_workaround/SKILL.md) を参照してください。
- **Windowsユーザー**: お使いの環境で直接実行するのではなく、**Git Bash** を経由してスクリプトを実行してください。
