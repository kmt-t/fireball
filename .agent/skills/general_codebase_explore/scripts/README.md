# Codebase Explorer Scripts

コードベースを構造的に把握し、シンボルの要約や AST 解析を行うためのツール群。

## 1. 役割と数学的性質 (Role & Axioms)
- **目的**: 大規模なコードベースから、関数、クラス、型定義などの重要な構造（物理層）を抽出し、設計ドキュメントの論理的な制約と照らし合わせるための情報を提供する。
- **不変条件 (Invariants)**:
    - `summary`: 常に出力は相対パスで正規化され、シンボルの一意な所在（$\exists$ Existence）を証明可能な形式で提供する。
    - `ast`: Clang AST に基づき、マクロ展開後やテンプレート解決済みの「真の型」情報を抽出する。
- **影響範囲 (Side Effects)**: なし（読み取り専用スキャン）。

## 2. インターフェース (CLI & Interface)

### [explore-codebase.sh](file:///w:/mysrc/fireball/.agent/skills/general_codebase_explore/scripts/explore-codebase.sh)
`bash explore-codebase.sh <command> [args...]`

- **コマンド (Commands)**:
    - `summary <path> [--json]`: ファイル/ディレクトリの概要を抽出。
    - `ast <path> [--json] [compiler_flags...]`: Clang AST を出力。
    - `callers <function_name> [--depth N] [--search-dir DIR]`: 指定した関数の呼び出し元を検索。
    - `graph <path> [--source FILE] [--search-dir DIR]`: コールグラフの生成。`--source` は `cflow` に渡す追加ソース。
    - `symbols <path> [compiler_flags...]`: ファイル内で実際に使用されているシンボルリストの抽出。
    - `report <path> [-I INC] [--search-dir DIR]`: `graph` と `symbols` を統合したレポート生成。
    - `pipe summary`: 標準入力から渡されたパス（1行1件）を順次 `summary --json` で処理。

- **共通フラグ (Common Flags)**:
    - `--json`: 出力をパース可能な JSON 形式にする（一部のコマンドのみ）。
    - `--depth <int>`: 再帰検索の深さ（デフォルト: 1）。
    - `--search-dir <dir>`: 追加のスキャン対象ディレクトリ。
    - `--source <file>`: 依存解析に含める追加のソースファイル。
    - `--pipe-sources`: `cflow` 実行時に標準入力からファイルリストを読み込む。


## 3. 使用方法 (Usage) サンプル

### パターンA: ディレクトリ全体の俯瞰 (Summary)
```bash
bash .agent/skills/general_codebase_explore/scripts/explore-codebase.sh summary src/
```

### パターンB: 単一ファイルの Clang AST 解析 (JSON)
```bash
bash .agent/skills/general_codebase_explore/scripts/explore-codebase.sh ast inc/fireball.hxx --json -Iinc
```

### パターンC: 特定のキーワードに関する呼び出し元の特定 (Callers)
```bash
bash .agent/skills/general_codebase_explore/scripts/explore-codebase.sh callers "init_hal"
```

## 4. データ構造 (Schema)
`--json` 出力は以下の構造を持ちます（簡略化）:
```json
{
  "file": "src/main.cxx",
  "symbols": [
    { "name": "main", "kind": "Function", "line": 10 },
    { "name": "vmmio_manager", "kind": "Class", "line": 20 }
  ]
}
```

## 5. エラーリカバリ (Recovery)
- **Parse Error**: `clang` がヘッダを見つけられません。`-I` オプションでインクルードパスを明示してください。
- **Memory Error**: 巨大なファイルを解析する場合、メモリが不足することがあります。`summary` で対象を絞り込んでから詳細解析を行ってください。
