---
trigger: always_on
---

# コーディングスタイル

**原則**: 標準C/C++準拠。メモリ効率最優先。

## 1. 命名・ファイル

- **ファイル**: `inc/path/to/file.hxx` , `src/path/to/file.cxx` 
- **Class/Struct/Enum/Func/Var**: `snake_case` (e.g., `ipc_router`, `send_message`)
- **Interface**: No prefix (`i`, `I`) or postfix (`_interface`, `_if`). Use pure `snake_case`.
- **Const/Macro**: `UPPER_SNAKE_CASE`
- **Class Instance Member**: `variable_` (trailing underscore)
- **Class Static Member**: `variable__` (trailing double underscore)
- **Struct (POD/DTO) Member**: `variable` (No trailing underscore. Only classes use trailing underscores for encapsulated members).
- **Type Postfix**: `_t` postfix is allowed ONLY for `typedef`. Do NOT use it for struct/class names.

## 2 スタイル

### 2.1 書式 (Formatter)

機械的な書式は `.clang-format` に準拠するが、可読性のため主要な項目を以下に記す。

- **インデント**: スペース2つ。タブ使用禁止。
- **波括弧**: `Attach` スタイル (K&R)。 `if (cond) {` のように行末に置く。
- **1行の長さ**: 最大100文字。
- **ポインタ・参照**: 左寄せ (`int* p`, `const Type& obj`)。
- **アクセス修飾子**: クラスの波括弧からインデントさせない (`-2` オフセット)。
- **テンプレート**: 宣言が長い場合は複数行に分ける。
- **三項演算子**: 改行が必要な場合は演算子の前で改行する。

### 2.2 実装規約

- **モダンC++**: C++20以降の文法（Concepts, Coroutines, std::span等）を積極的に使用すること。
- **効率的な引数渡し**: クラス・構造体は原則として `const T&` で渡す。
- **静的確定**: `constexpr` , `consteval` , `static_assert` を積極的に活用し、可能な限りコンパイル時に計算・検証を完結させる。
- **明示的な型指定**: `auto` は型が自明な場合（キャスト、コンストラクタ呼び出し等）に限定し、関数の戻り値やインターフェイス境界では型を明記すること。
- **所有権の明示**: `std::unique_ptr` や `std::shared_ptr` は使わず、ライフサイクルを静的に設計するか、必要に応じて独自の `Ref` 構造体を用いる（詳細は patterns 参照）。
- **RAIIの積極的な活用**: リソース（メモリ、ロック、ファイル、ハードウェア状態等）の解放はデストラクタに任せ、例外安全（本システムではアボート安全）とリソース漏洩防止を徹底すること。

## 3 設計パターン

- `docs/oders/patterns/*.md`を適宜参照すること。
- 特に @docs/orders/patterns/stdlib.md の使用禁止コンテナには注意すること。