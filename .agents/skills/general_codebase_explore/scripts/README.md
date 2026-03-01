# Codebase Explorer Scripts

## 1. 役割と数学的性質
コードベースの物理構造（AST, シンボル, 呼び出し関係, キーワード）を機械的に抽出し、事実データとして提供する。
不変条件: 出力は常にプロジェクトルートからの相対パスで正規化され、解釈を含まない「コード上の事実」のみを出力する。

## 2. インターフェース

### [run-explorer.sh](.agent/skills/general_codebase_explore/scripts/run-explorer.sh)
`bash run-explorer.sh <subcommand> [path] [options]`
- **Subcommands**: `summary`, `ast`, `callers`, `graph`, `symbols`, `report`.
- **目的**: 探索ツール群への統合インターフェースを提供。

### [explore_codebase.py](.agent/skills/general_codebase_explore/scripts/explore_codebase.py)
`python3 explore_codebase.py [path] [options] [-- extra_args]`

#### Arguments
- `path`: 解析対象のファイルまたはディレクトリ。デフォルトはカレントディレクトリ。

#### Options
- `--ls`: ディレクトリ内のアイテム一覧を表示。
- `--symbols`: ファイル内で実際に使用されているシンボルリストを抽出。
- `--ast`: Clang AST をダンプ。
- `--graph`: `cflow` 連携によるコールグラフ生成。
- `--callers [func]`: 指定した関数の呼び出し元を検索。
- `--keywords`: トレーサビリティキーワード `{Keyword}` を抽出。
- `-p, --stdin-paths`: STDIN から解析対象パスを一行ずつ読み込む。
- `-j, --json`: 出力をパース可能な JSON 形式にする。
- `--depth [int]`: 再帰検索の深さ。
- `--search-dir [dir]`: 追加のスキャン対象ディレクトリ。
- `-- extra_args [args...]`: コンパイラに渡す追加フラグ。

### [generate_summary.py](.agent/skills/general_codebase_explore/scripts/generate_summary.py)
`python3 generate_summary.py [path] [--json]`
- **目的**: C++ ソースまたは Markdown から高密度な概要を抽出。

### [print_tree.py](.agent/skills/general_codebase_explore/scripts/print_tree.py)
`python3 print_tree.py [path]`
- **目的**: ディレクトリ階層をツリー形式で表示。

## 3. 使用方法 パイプ連携

### Example: Finding callers of modified functions
```bash
git diff --name-only | grep "\.cxx$" | python3 explore_codebase.py --stdin-paths --symbols | xargs -I {} python3 explore_codebase.py . --callers "{}"
```

## 4. データ構造
`--json` 出力の一般形式:
```json
{
  "file": "src/main.cxx",
  "symbols": ["vmmio_read", "HAL_Init", ...],
  "keywords": ["Requirement_HAL_01", ...]
}
```

## 5. エラーリカバリ
- **Command Not Found**: `clang` や `cflow` がインストールされていない環境では動作しません。WSL2 または指定されたビルドコンテナ内で実行してください。
- **Include Error**: AST 解析で型が見つからない場合、`-- extra_args -I/path/to/inc` を使用してください。
