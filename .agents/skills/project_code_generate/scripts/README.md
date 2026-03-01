# WIT Code Generator Scripts

WIT WebAssembly Interface Types から C++ ヘッダを生成し、一貫性を検証するためのツール群。

## 1. 役割と数学的性質
- **目的**: 設計の唯一の真実 Truth である WIT IDL から、組み込み C++ の制約を遵守したヘッダを自動生成し、手動メンテによる認知ドリフトを排除する。
- **不変条件 Invariants**:
    - `generate_cpp.py`: 生成されたヘッダは常に `wasm-tools` のパース結果と論理的に等価であり、`@constexpr` などの静的仕様が C++ 定数に正確に展開される。
    - `check_naming.py`: Exit 0 は、生成されたシンボルがプロジェクト規定の `snake_case` 等に完全準拠していることを保証する。
- **影響範囲 Side Effects**: `inc/gen/` ディレクトリ配下へのヘッダファイル生成。

## 2. インターフェース

### [run-codegen.sh](.agent/skills/project_code_generate/scripts/run-codegen.sh)
`bash run-codegen.sh <subcommand> [args...]`
- **Subcommands**:
    - `generate`: WIT から C++ ヘッダを生成。
    - `check`: 命名規則・制約違反をチェック。
    - `build`: プロジェクトのビルドテストを実行。
    - `all`: 上記すべてを順に実行。

### [generate_cpp.py](.agent/skills/project_code_generate/scripts/generate_cpp.py)
`python3 generate_cpp.py [wit_dir] [output_dir]`
- **引数 Arguments**:
    - `wit_dir`: WIT パッケージが含まれるディレクトリ 通常 `wit/`。
    - `output_dir`: C++ ヘッダの出力先 通常 `inc/gen/`。

### [check_naming.py](.agent/skills/project_code_generate/scripts/check_naming.py)
`python3 check_naming.py [path...]`

## 3. 使用方法

### パターンA: 標準的な全パッケージ生成
```bash
python3 .agent/skills/project_code_generate/scripts/generate_cpp.py wit/ inc/gen/
```

### パターンB: 生成されたヘッダの命名規則チェック
```bash
find inc/gen -name "*.hxx" | python3 .agent/skills/project_code_generate/scripts/check_naming.py --stdin-paths
```

### パターンC: 特定の違反のみを抽出
```bash
python3 .agent/skills/project_code_generate/scripts/check_violations.py inc/gen/ | grep "void\*"
```

## 4. データ構造・アノテーション

### @bitfield
```wit
/// @bitfield type_scope:u8:0-7, key:u24:8-31
record kv-pair { raw: u64 }
```
C++ 側では `uint64_t : 8` 等のビットフィールド構造体に展開されます。

### @constexpr
```wit
/// @constexpr: BASE_ADDR = 0x40000000
resource vmmio-manager { ... }
```
C++ 側では `static constexpr auto BASE_ADDR = 0x40000000;` として展開されます。

## 5. エラーリカバリ
- **wasm-tools not found**: ホスト環境にツールがありません。`docker-generate-code.sh` を使用してください。
- **WIT Naming Error**: `wasm-tools` はケバブケースを強制します。`device_id` ではなく `device-id` を使用してください。
