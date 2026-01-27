# コーディングスタイル

**原則**: 標準C/C++準拠。メモリ効率最優先。

## 1. 命名・ファイル

- **ファイル**: `inc/path/to/file.hxx` , `src/path/to/file.cxx` 
- **Class/Struct/Enum/Func/Var**: `snake_case` (e.g., `ipc_router`, `send_message`)
- **Const/Macro**: `UPPER_SNAKE_CASE`
- **Class Instance Member**: `variable_` (trailing underscore)
- **Class Static Member**: `variable__` (trailing double underscore)

## 2 スタイル

- @.clang-format を参照すること。
- C++20以降の文法を積極的に使用すること。
- クラス、構造体を関数で渡すときは参照渡しとし、極力constを付けること。
- constexprを積極的に使用すること。

## 3 設計パターン

- `docs/oders/patterns/*.md`を適宜参照すること。
- 特に @docs/orders/patterns/stdlib.md の使用禁止コンテナには注意すること。