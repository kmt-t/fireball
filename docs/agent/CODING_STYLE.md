# コーディングスタイル

**原則**: 標準C/C++準拠。メモリ効率最優先。

## 1 命名・ファイル

- **ファイル**: `inc/path/to/file.hxx` , `src/path/to/file.cxx` 
- **Class/Func/Var**: `snake_case` (e.g., `ipc_router`, `send_message`)
- **Struct**: `typedef struct { ... } name_t;` (`_t` suffix)
- **Const/Macro**: `UPPER_SNAKE_CASE`
- **Member**: `variable_` (trailing underscore)

## 2 スタイル

@.clang-format を参照すること。

## 3 設計パターン

特に @docs/agent/patterns/stdlib.md の内容は守ること。 

その他も`docs/agent/patterns/*.md`を適宜参照すること。
  