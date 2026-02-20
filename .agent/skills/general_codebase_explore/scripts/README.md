# Codebase Explore Script

[Role & Axioms]
コードベースの物理構造（AST, シンボル, 呼び出し関係, キーワード）を機械的に抽出し、事実データとして提供する。
不変条件: 出力は常にプロジェクトルートからの相対パスで正規化され、解釈を含まない「コード上の事実」のみを出力する。

## Full-Spec Interface

### explore_codebase.py
`python explore_codebase.py [path] [options] [-- extra_args]`

#### Arguments
- `path`: 解析対象のファイルまたはディレクトリ。デフォルトはカレントディレクトリ。

#### Options
- `--ls`: ディレクトリ内のアイテム一覧を表示。
- `--symbols`: ファイル内で実際に使用されているシンボルリストを抽出。
- `--ast`: Clang AST をダンプ。
- `--graph`: `cflow` 連携によるコールグラフ生成。
- `--callers [func]`: 指定した関数の呼び出し元を検索。
- `--keywords`: トレーサビリティキーワード `{Keyword}` を抽出。
- `--json`: 出力をパース可能な JSON 形式にする。
- `--depth [int]`: 再帰検索の深さ。
- `--search-dir [dir]`: 追加のスキャン対象ディレクトリ。
- `-- extra_args [args...]`: コンパイラに渡す追加フラグ。

## Composition (Pipe)

### Example: Finding callers of modified functions
```bash
git diff --name-only | grep "\.cxx$" | xargs -I {} python explore_codebase.py {} --symbols | xargs -I {} python explore_codebase.py . --callers "{}"
```

## Schema
`--json` 出力の一般形式:
```json
{
  "file": "src/main.cxx",
  "symbols": ["vmmio_read", "HAL_Init", ...],
  "keywords": ["Requirement_HAL_01", ...]
}
```

## Recovery
- **Command Not Found**: `clang` や `cflow` がインストールされていない環境では動作しません。WSL2 または指定されたビルドコンテナ内で実行してください。
- **Include Error**: AST 解析で型が見つからない場合、`-- extra_args -I/path/to/inc` を使用してください。
