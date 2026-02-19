---
name: WASM Development
description: >-
  WebAssembly/WASI仕様, WAMR実装, LLVMバックエンド定義の参照パスと調査手順。
  WHEN: WASM命令の挙動確認, バイナリフォーマット調査, WASI API設計, JITバックエンド実装
  SCOPE: 外部リファレンスへのナビゲーション。Fireball固有の設計判断はfireball_architectureを参照。
  RELATED: project_code_generate（WIT定義からのコード生成）, fireball_architecture（WIT-First原則）
---

# WASM Development

WebAssemblyエコシステム（Spec, WASI, WAMR, LLVM）に関連するリソースを参照し、設計・実装を支援するための統合スキルです。

## 1. WebAssembly Core Spec

プロジェクト内の `references/webassembly/document/core/` を参照し、正確な仕様を確認します。

- **Base Path**: `references/webassembly/document/core/`

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

プロジェクト内の `references/wasi/` を参照し、システムインターフェイス（WASI）の設計を支援します。

- **Base Path**: `references/wasi/`

### 調査手順
1. **`proposals/`**: 機能ごとのWIT/Markdown定義を確認（`filesystem`, `io`, `sockets` 等）。
2. **`.wit` ファイル**: 関数シグネチャと型定義のマスターとして参照。
3. **`.md` ファイル**: セマンティクスと制約の確認。

---

## 3. WAMR Implementation

`references/wamr/` にある WAMR (WebAssembly Micro Runtime) のソースコードを調査し、実装詳細を確認します。

- **Base Path**: `references/wamr/`

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

- **Base Path**: `references/llvm/llvm/lib/Target/`

### 重要なファイル (.td)
命令セットのエンコーディングやパターンマッチングルールは、通常 `*InstrInfo.td` に記述されています。

- **WebAssembly**: `references/llvm/llvm/lib/Target/WebAssembly/WebAssemblyInstrInfo.td`
- **x64 (x86-64)**: `references/llvm/llvm/lib/Target/X86/X86InstrInfo.td`
- **ARMv8-M (Cortex-M)**: `references/llvm/llvm/lib/Target/ARM/ARMInstrInfo.td`
- **RISC-V**: `references/llvm/llvm/lib/Target/RISCV/RISCVInstrInfo.td`

### 調査方法
1. `grep_search` で命令名（例: `i32.add`, `ADDI`）を検索する。
2. `.td` ファイル内の `def` 定義を確認し、ビットパターン (`Inst{...}`) やアセンブリ文字列を確認する。

---

## 5. Standard Include Paths (for explorer.sh)

`explorer.sh summary` を WAMR リファレンスのコードに対して実行する際、正確な AST 解析のために以下のインクルードパスの指定が推奨されます。

### WAMR Core Includes
- `-I inc` (Project local headers)
- `-I references/wamr/core/iwasm/include`
- `-I references/wamr/core/shared/utils`
- `-I references/wamr/core/shared/platform/include`

### 🚀 推奨コマンド

```bash
# WAMRのモジュール構造と重要シンボルを、依存関係（検索パス・インクルードパス）# find で対象ファイルをリストアップし、パイプ経由で graph に渡す
# --pipe-sources フラグにより、標準入力からソースリストを読み込みます
find references/wamr/core/iwasm/common -name "*.c" | \
  bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh graph \
  references/wamr/core/iwasm/common/wasm_runtime_common.c \
  --pipe-sources
h-dir references/wamr/core/iwasm/common \
  -I inc \
  -I references/wamr/core/iwasm/include \
  -I references/wamr/core/shared/utils
```

### ✅ 何がわかって嬉しいか

1. **純粋なロジック構造（Graph）**:
   - `--search-dir` で指定した範囲にある関連ソースを解析対象に含めつつ、`cflow` フィルタにより標準ライブラリのノイズを除去した「関数連関」が見えます。
   - データの流れやエントリーポイントを即座に特定できます。

2. **生存シンボルリスト（Key Symbols）**:
   - `clang-check` 解析（`-I` フラグによる正確なパース）とソース内容の交差フィルタにより、システムヘッダ由来の膨大な定義を無視し、**「そのファイルで実際に定義・使用されている重要な名前」**だけをリストアップします。
   - 型名や重要な関数名が一目でわかるため、詳細を読む前に全体像を脳内にインデックスできます。

3. **トークン効率の最大化と再現性**:
   - 汎用的なツールとして引数（ディレクトリやインクルードパス）を明示するため、プロジェクトの構造が変わったり新しいセッションを開始したりしても、これらのおすすめコマンドを実行するだけで確実な解析が可能です。

---

## 6. 状況別：解析コマンドの使い分け

新しく追加された `report` コマンドにより、多くの場合で「とりあえずこれ」という選択肢ができました。以前のコマンドとの使い分けは以下の通りです。

### 🔍 単一モジュールを詳細に深掘りしたい時 (推奨: `report`)
- **Command**: `bash .agent/skills/general_docker_run/scripts/docker-explore-codebase.sh report <file>`
- **理由**: ノイズ除去済み。一番「賢い」出力が得られます。

### 📊 ディレクトリ全体の構成を知りたい時 (`summary`)
- **Command**: `bash .agent/skills/general_codebase_explore/scripts/explore-codebase.sh summary <directory>`
- **理由**: ディレクトリ内のファイルツリーと、各ファイルの主要シンボルをさらっと見るのに適しています。
- **⚠️ 非推奨**: 単一の巨大なソースファイル（例: `wasm_interp_fast.c`）に対して実行すると、ノイズが多すぎて文脈が埋もれるため推奨されません。

### 🛠️ ツールに詳細な情報を渡したい時 (`ast --json`)
- **Command**: `bash .agent/skills/general_codebase_explore/scripts/docker-explore-codebase.sh ast <file> --json`
- **理由**: エージェント（私）が AST の厳密な構造をプログラム的に解析したい時に使います。人間が読むには重すぎます。

### 🔍 キーワードの周辺情報を一括収集したい時 (`context`)
- **Command**: `bash .agent/skills/general_codebase_explore/docker-explorer.sh context "<keyword>"`
- **理由**: 定義・呼び出し元・関連ドキュメントを横断的に探す際に有効です。
