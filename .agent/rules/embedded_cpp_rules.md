---
trigger: always_on
---

# 組み込みC++ルール (Embedded C++ Rules)

本ドキュメントは、組み込み環境（例：制限されたRAM、OSへの非依存）に特化した、C++実装のための厳格な制約とコーディングスタイルを定義する。

## 1. 厳格な禁止事項 (ハードウェア制約)

*   **ヒープ割り当ての禁止 (NO Heap Allocation)**:
    *   **禁止**: `malloc`, `free`, `new`, `delete`.
    *   **禁止**: `std::vector`, `std::map`, `std::string`, `std::unique_ptr`, `std::shared_ptr`.
    *   **戦略**: 静的割り当て、スタック割り当て、または固定サイズのアリーナを使用する。
*   **例外の禁止 (NO Exceptions)**: `try`, `catch`, `throw` は禁止。`result<T, E>` パターンを使用する。
*   **RTTIの禁止**: 実行時型情報 (Run-Time Type Information) は無効化される。
*   **`void*` の禁止**: 型安全使用のため `std::span<std::byte>` や `binary_view` などの型付きラッパーを使用する。
*   **リソース管理**:
    *   **RAII**: すべてのリソースに対して厳格に要求される。
    *   **手動解放**: デストラクタ以外での明示的な `close()` や `unlock()` 呼び出しは禁止。

## 2. メモリと型の語彙 (Memory & Type Vocabulary)

仕様の型を実装の型にマッピングするために、以下のエイリアスを使用する。

### プリミティブ型
| 仕様型名 (日/英) | C++ エイリアス | 基本型 | 説明 |
| :--- | :--- | :--- | :--- |
| アドレス / Address | `address` | `uint32_t` | メモリアドレス |
| オフセット / Offset | `byte_offset` | `uint32_t` | 相対位置 |
| バイト数 / Count | `byte_count` | `uint32_t` | サイズ/長さ |
| エントリ数 / Entry Count | `entry_count` | `uint32_t` | 要素数 |
| インデックス / Index | `function_index` | `uint32_t` | テーブルインデックス |
| フラグ / Flags | `interrupt_flags` | `uint32_t` | ビットセット |

### 複合型・ビュー型
| 仕様型名 (日/英) | C++ エイリアス | 実装 | 説明 |
| :--- | :--- | :--- | :--- |
| バイナリビュー / Binary View | `binary_view` | `std::span<const uint8_t>` | 読み取り専用ROMビュー |
| 可変ビュー / Mutable View | `mutable_binary_view` | `std::span<uint8_t>` | 書き込み可能RAMビュー |
| データ範囲 / Data Range | `data_range<T>` | `std::span<T>` | 型付きメモリ範囲 |
| 結果 / Result | `result<T, E>` | Custom | Expectedライクな結果型 |
| オプショナル / Optional | `optional<T>` | `std::optional` (or custom) | 値の有無 |

## 3. スタイルとフォーマット (Style & Formatting)

*   **フォーマット**: Clang-Format 準拠。
    *   インデント: スペース2つ (タブ禁止)。
    *   行幅: 100文字。
    *   波括弧: K&R (Attach) スタイル (`if (cond) {`)。
*   **命名規則**:
    *   **一般**: `snake_case` (変数, 関数, メソッド, 名前空間)。
    *   **定数/列挙**: `UPPER_SNAKE_CASE`.
    *   **メンバ変数**: `variable_` (末尾にアンダースコア1つ)。
    *   **静的メンバ**: `variable__` (末尾にアンダースコア2つ)。
    *   **POD/構造体**: `variable` (パブリックメンバにアンダースコアなし)。
    *   **インターフェース**: `snake_case` (`I` プレフィックス禁止)。
*   **モダンイディオム**:
    *   **`constexpr` / `consteval`**: コンパイル時計算を最大化する（ルックアップ、不変条件など）。
    *   **`const`**: 不変な変数にはデフォルトで使用する。

## 4. コード関連ワークフロー
*   **生成ヘッダ**: WITから生成されたヘッダを手動で編集しないこと。
*   **不変条件**: WIT内の `@inv` は、可能な限り `static_assert` または `constexpr` チェックに変換すること。
