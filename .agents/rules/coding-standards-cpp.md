---
name: coding-standards-cpp
description: 極小組み込み環境向け C23/C++23 コーディング標準（Clang 17+ 必須、[[clang::musttail]]、メモリ確保 API/例外/RTTI/STL ポリシー、独自ユーティリティ）
globs: ["src/**", "inc/**", "tests/**/*.cxx", "tests/**/*.hxx"]
scope: GLOBAL
---

# Fireball C23 / C++23 コーディング標準

本ドキュメントは Fireball ランタイムの C23 / C++23 実装規約を定める。容量要件は `docs/requires/requirement_list.md`、物理予算の評価状況は `docs/architecture/resource_budget_estimation.md` に従う。

## 1. コンパイラ要件 (Compiler Prerequisites)

- **Clang 17+ 必須（Strictly Mandatory）**:
  - **GCC および MSVC は非サポート** とする。
  - **必須理由**: Fireball のインタープリタディスパッチおよび JIT ホスト呼び出し規約は、直接末尾呼び出しによるスタック消費ゼロを保証する属性 `[[clang::musttail]]` を前提としている。他のコンパイラでは末尾呼び出しが保証されず、スタックオーバーフローを引き起こすためコンパイルを許可しない。
- **言語標準**: C23（Cコード）および C++23（C++コード）。

---

## 2. 組み込みメモリ・安全制約 (Embedded & Safety Constraints)

- **メモリ確保 API とコンテナの制約 `{GLOBAL_Policy_Memory}`**:
  - ヒープ確保関数（`malloc`, `free`, `realloc`, `calloc`）および通常の演算子（`new`, `new[]`, `delete`, `delete[]`）の使用を禁止する。
  - placement/in-place `new`（例: `::new (storage) T(...)`）は許可する。使用時は保存領域のサイズ、アラインメント、オブジェクト寿命、および破棄手順を保証する。
  - プロジェクトで提供する独自ヒープ API および独自コンテナは許可する。それらは各コンポーネントの仕様、所有権、容量、寿命、および失敗時挙動に従う。
  - 標準ライブラリの動的コンテナ（`std::vector`, `std::string`, `std::list`, `std::map`, `std::unordered_map` 等）は、システム提供アロケータを使用していても禁止する。
- **例外および RTTI の完全禁止**:
  - 例外機構（`throw`, `try`, `catch`）を禁止する（`-fno-exceptions`）。エラーは戻り値で伝播する。
  - 実行時型情報（RTTI: `typeid`, `dynamic_cast`）を禁止する（`-fno-rtti`）。
- **型安全性とポインタ規約**:
  - 生の `void*` によるメモリ操作を禁止し、型付き非所有ビュー（`std::span`, `std::string_view`）を使用する。
  - ポインタ間接参照の多重化を避け、メモリ境界チェックを徹底する。
- **公開名前空間**:
  - Fireball のすべての公開 API・型・定数は `fireball` 名前空間に配置する。

---

## 3. コードスタイル & 命名規則

- **命名規則**:
  - 関数名、変数名、ファイル名は `snake_case` を基本とする。
  - テンプレートパラメータおよびコンセプト名は `CamelCase` または `PascalCase` とする。
  - 定数およびマクロ名は `UPPER_SNAKE_CASE` とする（マクロ定義は最小限に留める）。
- **フォーマット規則**:
  - Clang-Format 準拠（インデント幅 2 スペース、最大行長 100 桁）。
- **拡張子規約**:
  - C++ ヘッダ: `.hxx`
  - C++ 実装: `.cxx`
  - C 実装: `.c`

---

## 4. C++ 標準ライブラリ (STL) 利用規約

標準ライブラリは、以下の許可一覧と禁止一覧に従って利用する。標準ライブラリの動的コンテナは、システム提供アロケータを使用していても利用してはならない。

### 4.1 利用可能ライブラリ (Allowed)
- `<array>`: 固定長配列（`std::array`）。
- `<string_view>`: 非所有文字列参照。
- `<span>`: バイナリ・配列の型安全な非所有ビュー。
- `<optional>`: 無効値の型安全な表現。
- `<variant>`: 型安全な共用体（タグ付き直和型）。
- `<expected>`: 例外を使わないエラー伝播（C++23）。
- `<concepts>`: コンパイル時型制約・静的インターフェース検証（C++23）。
- `<type_traits>`: コンパイル時メタプログラミング補助。
- `<bit>`: 高速ビット操作（`std::bit_cast`, `std::countl_zero` 等）。
- `<coroutine>`: コルーチン制御・対称遷移（Symmetric Transfer）。

### 4.2 禁止ライブラリ (Prohibited)
- **動的コンテナ**: `std::vector`, `std::string`, `std::list`, `std::map`, `std::unordered_map` 等。
- **入出力**: `std::iostream`, `std::format`（コードサイズ肥大化のため）。
- **例外**: `std::exception` 関連。
- **多相関数ラッパー**: `std::function`（ヒープ確保の可能性があるため禁止）。

---

## 5. 独自ユーティリティの条件

- 関数ラッパーを使う場合は、格納容量とアラインメントを固定し、超過を拒否する。暗黙のヒープ確保を認めない。
- アロケータとコンテナは、容量・所有権・寿命・失敗時の動作を定義してから使用する。
- バイナリデータの参照には、型付きの非所有ビューを使う。
- エラー伝播には `std::expected` または同等の軽量な戻り値型を使う。具体的なユーティリティ名と容量はコンポーネント仕様を正本とする。
