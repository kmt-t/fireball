# Fireball Coding Style

**原則**: 標準C/C++準拠。独自拡張禁止。メモリ効率最優先。

## 1. C++ (Core/Subsystems)

### 1.1 命名・ファイル
- **ファイル**: `inc/path/to/file.hxx` (IF), `src/path/to/file.cxx` (Impl)
- **Class/Func/Var**: `snake_case` (e.g., `ipc_router`, `send_message`)
- **Struct**: `typedef struct { ... } name_t;` (`_t` suffix)
- **Const/Macro**: `UPPER_SNAKE_CASE`
- **Member**: `variable_` (trailing underscore)

### 1.2 スタイル
- **Indent**: 2 spaces (No tabs)
- **Brace**: Allman style (New line)
- **Limit**: 100 chars/line
- **Pointer**: `int* ptr` (Left aligned)

### 1.3 制限 (Embedded Constraints)
- **Prohibited**: `std::map`, `std::unordered_map`, `std::function`, `std::exception`, RTTI
- **Allowed**: `std::array`, `std::string_view`, `std::optional`, `<algorithm>`, `<cstring>`
- **String**: `std::string` 使用禁止。`std::string_view` か `char[]` を使用。
- **Lookup**: `sorted std::array` + `std::lower_bound` (Binary Search)
- **Flags**: `-fno-exceptions -fno-rtti`

### 1.4 設計パターン
- **Smart Ptr**: `std::unique_ptr` 推奨。裸の `new/delete` 禁止。
- **Error**: `int` return code (POSIX `errno` style)。例外禁止。
- **Callback**: 関数ポインタを使用 (`typedef int (*handler_t)(u32, u8);`)。`void*` コンテキスト回避。

## 2. C (Drivers/Platform)
- **Style**: Zephyr/Linux kernel style
- **Struct**: `snake_case_t`
- **Func**: `module_action()`

## 3. Checklist
- [ ] `clang-format` 適用済みか
- [ ] STL 禁止コンテナを使っていないか
- [ ] `.text` サイズ肥大化していないか
