# C++標準ライブラリ利用規約 & ユーティリティ設計書

本ドキュメントは、極小リソース環境（RAM 32KB - 64KB）における C++23 標準ライブラリ (STL) の利用制限、および不足機能を補う独自のユーティリティについて定義する。 `{NoStdVector}` `{Policy_Memory}` `{ZeroOverhead}`

## 1. C++標準ライブラリ (STL) 利用ポリシー

libc++ 等のランタイムライブラリへのリンクを排除し、コードサイズを最小化するため、原則として**ヘッダのみで完結し、かつヒープ（動的メモリ確保）を使用しない機能**のみを利用可能とする。

### 1.1 推奨されるライブラリ (Allowed)
以下のライブラリは、ヒープを使用せず、かつリンクターゲットを増やさないため積極的に利用する。

| ライブラリ | 用途 | 備考 |
| :--- | :--- | :--- |
| `<array>` | 固定長配列 | `std::array` をスタックまたは静的領域で使用。 |
| `<string_view>` | 文字列参照 | 文字列操作の基本。所有権を持たず、参照のみを行う。 |
| `<span>` | バイナリ・配列参照 | メモリ領域、バイナリデータへの型安全なアクセス。 |
| `<flat_map>` | 検索・索引 | C++23。静的バッファを基盤として使用。 `{FlatMapIndexed}` |
| `<optional>` | 無効値の表現 | ポインタを使わない「値の不在」の表現。 |
| `<variant>` | 型安全な共用体 | 状態やメッセージの定義。 |
| `<expected>` | エラー処理 | C++23。戻り値によるエラー伝播。 `{RecoveryStrategy}` |
| `<concepts>` | 静的DI・制約 | コンパイル時の型検証。 `{ConceptHarnessDI}` |
| `<type_traits>` | メタプログラミング | コンパイル時計算の補助。 |
| `<bit>` | ビット操作 | `std::bit_cast` やビットカウント等。 |
| `<coroutine>` | タスク管理 | COOSの基盤機能。 `{UseCpp20Coroutine}` |

### 1.2 使用禁止・非推奨のライブラリ (Prohibited)
以下のライブラリは、隠れたヒープ確保、コードサイズの肥大化、またはランタイムへのリンクを発生させるため原則禁止とする。

- **動的コンテナ**: `std::vector`, `std::string`, `std::list`, `std::map`, `std::set`, `std::unordered_map` 等。
- **入出力**: `std::iostream`, `std::format` (ランタイム依存が強い場合)。
- **例外**: `std::exception` 関連。コンパイルオプション `-fno-exceptions` を前提とする。
- **多相ラッパー**: `std::function` (ヒープ確保の可能性があるため `economic_function` を使用)。

---

## 2. Fireball 独自ユーティリティ

### 2.1 メモリ管理

#### `bump_allocator`
- **目的**: Loader や JIT メタデータ構築時の高速なメモリ割り当て。 `{BumpAllocator}`
- **特性**: 破棄時に全領域を一度に解放する。断片化が発生しない。
- **配置**: `inc/common/bump_allocator.hxx`

#### `binary_view` / `mutable_binary_view`
- **目的**: `void*` を排除した型安全なメモリ参照。
- **基盤**: `std::span<std::byte>` または `std::span<const std::byte>`。 `{PhysicalPassthrough}`

### 2.2 文字列・バイナリ操作

- **文字列の保持**: 原則として `std::array<char, N>` を使用し、アクセスには `std::string_view` を用いる。
- **可変長データの扱い**: `std::span` を介して、スタックまたは `bump_allocator` で確保された領域を操作する。

### 2.3 制御・エラー処理

#### `result<T, E>`
- **目的**: 例外を禁止した環境でのエラー伝播。 `{RecoveryStrategy}`
- **実装**: `std::expected` のエイリアス、または同等の軽量実装。

#### `economic_function<Sig>`
- **目的**: `std::function` の代用。 `{ZeroOverhead}`
- **特性**: 
    - ヒープ割り当てを完全に排除した多相関数ラッパー。
    - **SBO (Small Buffer Optimization)** 領域（通常ポインタ2本分程度）を内蔵する。
    - 領域に収まらないサイズのラムダ式等が代入された場合は、コンパイル時または実行時（`static_assert` または `assert`）にエラーとして落とす仕様とする。

---

## 3. 検索・データ構造

### `std::flat_map` / `std::flat_set` (C++23)
- **目的**: 連続したメモリ領域上での $O(\log N)$ の高速検索。 `{FlatMapIndexed}`
- **特性**: 
    - 基盤コンテナとして `std::array` または `std::span` を使用するようにカスタマイズする。
    - ROM/静的領域上のデータに対する二分探索に使用。 `{BinarySearch}` `{AccessDictionary}`
