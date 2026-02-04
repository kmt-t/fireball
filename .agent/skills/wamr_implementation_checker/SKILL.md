---
name: WAMR Implementation Checker
description: Local WAMR (WebAssembly Micro Runtime) のソースコードを調査し、実装の詳細や挙動を確認するスキル
---

# WAMR Implementation Checker スキル

このスキルは、本プロジェクトで参照している WAMR の実装コードを調査し、WASMランタイムの具体的な動作（メモリ管理、インタープリタ、JITなど）を理解することを目的とします。

## 1. ソースコードの場所

- **Base Path**: `docs/orders/references/wamr/`

## 2. ディレクトリ構造と主な内容

| ディレクトリ | 内容 | 活用シーン |
|:---|:---|:---|
| `core/iwasm/` | WASMランタイム本体 (Interpreter, AOT, JIT) | 命令の実行ロジックやランタイムの挙動を確認したい時 |
| `core/shared/` | 共有ユーティリティ (メモリ管理、プラットフォーム抽象、ロギング) | 内部的なメモリ割り当てやプラットフォーム依存の処理を確認したい時 |
| `core/iwasm/libraries/` | WASIなどのライブラリ実装 | WASI APIの具体的な実装（例: `posix` 呼び出しへのマッピング）を確認したい時 |
| `doc/` | WAMR固有のドキュメント、ビルド方法、機能紹介 | WAMRの構成方法や拡張機能について調べたい時 |

## 3. 推奨される調査手順

1. **実装の特定**: 調査したい命令や機能に関連するキーワード（例: `wasm_runtime_call_wasm`, `wasm_interp_exec_env`, `wasi_libc_set_args`）を `grep_search` で検索する。
2. **コアロジックの確認**: 
    - インタープリタ: `core/iwasm/interpreter/` 下のファイルを確認。
    - AOT/JIT: `core/iwasm/aot/` や `core/iwasm/fast-jit/` 下を確認。
3. **データ構造の確認**: `core/iwasm/common/` や `core/shared/utils/` 下のヘッダファイルで、重要な構造体定義を確認する。

## 4. 便利なエントリポイント

- `core/iwasm/include/wasm_export.h`: WAMRが外部に提供する公開APIの定義。
- `core/iwasm/interpreter/wasm_interp.c`: インタープリタのメインループ。
- `core/iwasm/libraries/wasi-nn/`: WASI-NNの実装（拡張例として参考になる）。

## 5. 注意事項

- WAMRは多くの構成オプション（CMakeフラグ）を持っており、実際の動作はビルド時の定義に依存します。`#if WASM_ENABLE_...` などのプリプロセッサ命令に注意してコードを読んでください。
- 実装を参考にする際は、本プロジェクトのコーディングスタイルや設計原則との違いに注意し、そのままコピーするのではなく、エッセンスを抽出するようにしてください。
