# Fireball 組み込み C++23 コーディング標準 (C++ Coding Standards)

本ドキュメントは、極小リソース環境（RAM 32KB - 64KB）で動作する Fireball ランタイムの C23 / C++23 実装規約を定義する。
globs: ["src/**", "inc/**"]
scope: GLOBAL

## 1. コンパイラ要件 (Compiler Prerequisites)

- **Clang 17+ 必須（Strictly Mandatory）**:
  - **GCC および MSVC は非サポート** とする。
  - **必須理由**: Fireball のインタープリタディスパッチおよび JIT ホスト呼び出し規約は、直接末尾呼び出しによるスタック消費ゼロを保証する属性 `[[clang::musttail]]` を前提としている。他のコンパイラでは末尾呼び出しが保証されず、スタックオーバーフローを引き起こすためコンパイルを許可しない。
- **言語標準**: C23（Cコード）および C++23（C++コード）。

---

## 2. 組み込みメモリ・安全制約 (Embedded & Safety Constraints)

- **動的メモリ確保の完全禁止 `{Policy_Memory}`**:
  - ヒープ確保関数（`malloc`, `free`, `realloc`, `calloc`）および演算子（`new`, `delete`）の使用を完全禁止する。
  - 動的サイズ変更を伴う標準コンテナ（`std::vector`, `std::string`, `std::list`, `std::map` 等）を禁止する。
  - すべてのメモリ割り当ては、コンパイル時固定の静的バッファ（`std::array`）、スタック、または事前確保された固定長プール（`bump_allocator`、スロットプール）から行う。
- **例外および RTTI の完全禁止**:
  - 例外機構（`throw`, `try`, `catch`）を禁止する（`-fno-exceptions`）。エラー伝播は `result<T, E>`（`std::expected` 相当）による戻り値ハンドリングを徹底する。
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

libc++ 等の外部ランタイムへのリンクを排除し、コードサイズを最小化するため、原則として**ヘッダのみで完結し、ヒープを使用しない機能**のみを利用可能とする。

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
- **動的コンテナ**: `std::vector`, `std::string`, `std::map`, `std::unordered_map` 等。
- **入出力**: `std::iostream`, `std::format`（コードサイズ肥大化のため）。
- **例外**: `std::exception` 関連。
- **多相関数ラッパー**: `std::function`（ヒープ確保の可能性があるため禁止。後述の独自 `economic_function` を使用）。

---

## 5. 独自ユーティリティ設計規約 (Fireball Custom Utilities)

STL の使用禁止領域（ヒープ確保・例外）を補完するため、以下の独自ユーティリティを設計・実装して使用する。

### 5.1 固定 SBO 多相関数ラッパー (`economic_function<Sig>`)
- **目的**: `std::function` はサイズ超過時に暗黙の動的ヒープ確保（`malloc`）を行うため、極小組み込み環境では使用できない。これを代替するため、**ヒープ確保を完全に排除した固定 SBO (Small Buffer Optimization) 多相関数ラッパー**を独自実装する。
- **SBO インラインストレージ**:
  - ポインタ 2〜4 本分（通常 16〜32 バイト）の固定バッファを構造体内に内包し、呼び出し可能オブジェクト（ラムダ式、関数ポインタ、ファンクタ）をインラインに格納する。
- **容量超過時の安全性保証**:
  - SBO バッファに収まらない過大なキャプチャを持つラムダ式等が渡された場合、ヒープへ退避するのではなく、**コンパイル時（`static_assert`）または実行時アサーション（`assert`）により確実に拒絶・ビルド停止**する。
- **例外フリー & ゼロコスト**:
  - 例外を一切送出せず、未束縛呼び出しは `assert` による異常停止とする。

### 5.2 メモリ管理 & ビュー
- **`bump_allocator`**:
  - モジュールロードや JIT トレース生成時の一括メモリ確保用。解放はスコープ終了時に全破棄（Reset）で行い、断片化をゼロにする。
- **`binary_view` / `mutable_binary_view`**:
  - `void*` を排除した `std::span<const std::byte>` / `std::span<std::byte>` の型安全なエイリアス。

### 5.3 エラー伝播 (`result<T, E>`)
- 例外を禁止した環境で戻り値による型安全なエラー伝播を行う（`std::expected` または同等の軽量独自実装）。
