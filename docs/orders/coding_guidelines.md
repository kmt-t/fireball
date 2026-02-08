# Fireball 統合コーディング規約

本ドキュメントは、Fireballプロジェクトのコーディングスタイル、ドキュメンテーション、型語彙に関するルールを統合し、整理したものである。一貫性、可読性、保守性、および仕様からの正確な実装導出を保証するための、明確で階層的、かつ包括的なガイドラインを提供することを目的とする。

## 1. 一般原則

*   **標準C/C++準拠**: 標準的なC/C++の原則に準拠すること。
*   **メモリ効率最優先**: 設計および実装において、メモリ効率を最優先すること。
*   **設計駆動開発**: 設計ドキュメントに準拠し、仕様から実装を導出すること。
*   **契約優先 (Contract-First)**: 外部仕様は振る舞いの契約として記述し、実装詳細（具体的なデータ型など）への依存を排除すること。
*   **トレーサビリティ**: 全ての設計判断は要求キーワード `{Keyword}` から導出され、逆向きの追跡が可能であること。
*   **厳密なロジック**: 曖昧な自然言語を避け、状態、条件、副作用を明確に分離して記述すること。
*   **再現性**: シェルスクリプトや設定ファイルを含むソースコードの生成は再現性があること。

## 2. ファイル構造とインクルードガード

*   **ヘッダファイル**: `inc/path/to/file.hxx`
*   **ソースファイル**: `src/path/to/file.cxx`
*   **インクルードガード**: `#pragma once` を使用すること。従来の `#ifndef` / `#define` / `#endif` は使用しない。

## 3. 命名規則

*   **一般原則**: 特に指定がない限り、ほとんどの識別子に `snake_case` を使用すること。
*   **クラス、構造体、列挙型、関数、変数**: `snake_case` (例: `ipc_router`, `send_message`, `execution_context`)。
*   **インターフェース**: 純粋な `snake_case`。`i` や `I` のプレフィックス/ポストフィックスは使用しない (例: `loader`, `interpreter`)。
*   **定数、マクロ**: `UPPER_SNAKE_CASE` (例: `MAX_BUFFER_SIZE`, `FB_SYSCALL_WASI_FD_WRITE`)。
*   **列挙型定数**: `UPPER_SNAKE_CASE` (例: `RESERVED`, `UART0_RX_READY`)。
*   **クラスのインスタンスメンバー**: `variable_` (末尾にアンダースコア1つ)。
*   **クラスの静的メンバー**: `variable__` (末尾にアンダースコア2つ)。
*   **構造体 (POD/DTO) メンバー**: `variable` (末尾にアンダースコアなし。カプセル化されたメンバーにのみアンダースコアを使用)。
*   **型接尾辞**: `_t` 接尾辞は `typedef` 宣言にのみ許可される。`struct` または `class` 名には使用しないこと。`using` エイリアスの場合は、接尾辞なしで `snake_case` ルールに従うこと。

## 4. 型語彙とエイリアス

このセクションでは、仕様のための推奨される型語彙と、`fireball_vocabulary/SKILL.md`から導出された実装のための対応するC++ `using` エイリアスを定義する。これらのエイリアスは `inc/core/types.hxx` で定義される。

### 4.1. プリミティブ型エイリアス

| 仕様書名 (日) | 仕様書名 (英) | C++ エイリアス | 基本型     | 説明                                     |
| :------------ | :------------ | :------------- | :--------- | :--------------------------------------- |
| アドレス値    | Address       | `address`      | `uint32_t` | メモリアドレス                           |
| オフセット    | Offset        | `offset`       | `uint32_t` | 基点からの相対バイト位置                 |
| バイト数      | Byte Count    | `byte_count`   | `uint32_t` | メモリサイズまたは長さ（バイト単位）     |
| エントリ数    | Entry Count   | `entry_count`  | `uint32_t` | 配列・テーブルの要素数                   |
| 命令カウント  | Instruction Count | `instruction_count` | `uint32_t` | 実行された命令の回数                     |
| 関数インデックス | Function Index | `function_index` | `uint32_t` | 関数テーブル内の位置                     |
| シフト量      | Shift Amount  | `shift_amount` | `uint8_t`  | ビットシフト演算の量                     |
| ビットフラグ  | Interrupt Flags | `interrupt_flags` | `uint32_t` | 割り込みフラグの状態ビットセット         |
| ブール値      | Boolean       | `bool`         | `bool`     | 真偽値（`bool`のエイリアスは不要）      |

### 4.2. 複合型エイリアス

| 仕様書名 (日) | 仕様書名 (英) | C++ エイリアス           | 基本型                     | 説明                                     |
| :------------ | :------------ | :----------------------- | :------------------------- | :--------------------------------------- |
| バイナリビュー | Binary View   | `binary_view`            | `std::span<const uint8_t>` | ROM上のバイト列への読取専用参照          |
| バイナリビュー（可変）| Mutable Binary View | `mutable_binary_view`    | `std::span<uint8_t>`       | RAM上のバイト列への書込可能参照          |
| データ範囲    | Data Range    | `data_range<T>`          | `std::span<T>`             | 特定のメモリ範囲への参照（テンプレート） |
| 結果型        | Result Type   | `result<T, E>`           | カスタム実装               | 成功値またはエラーコードを返す           |
| オプショナル値 | Optional Value | `optional<T>`            | カスタムまたは`std::optional` | 値の有無を表す                           |
| 経済的な関数  | Economic Function | `economic_function<Sig>` | カスタム実装               | 型消去されたヒープレスな関数オブジェクト |

### 4.3. コンポーネント固有エイリアス

| 仕様書名 (日) | 仕様書名 (英) | C++ エイリアス | 基本型   | コンポーネント   |
| :------------ | :------------ | :------------- | :------- | :--------------- |
| WASMプログラムカウンタ | WASM Program Counter | `wasm_pc`      | `offset` | Interpreter, JIT |
| WASM命令コード | WASM Opcode   | `wasm_opcode`  | `uint8_t`| Interpreter, Loader |
| モジュール識別子 | Module ID     | `module_id`    | `uint32_t`| Loader, vSoC     |
| タスク識別子  | Task ID       | `task_id`      | `uint16_t`| Scheduler, COOS  |
| JITコードオフセット | JIT Code Offset | `code_offset`  | `uint16_t`| JIT Compiler     |
| カードインデックス | Card Index    | `card_index`   | `uint16_t`| JIT Hotspot Detector |

## 5. フォーマット

*   **インデント**: スペース2つ。タブの使用は禁止。
*   **波括弧**: `Attach` スタイル (K&R)。文の同じ行に開始波括弧を置く (`if (cond) {`)。
*   **行の長さ**: 最大100文字。
*   **ポインタ・参照**: 左寄せ (`int* p`, `const Type& obj`)。
*   **アクセス修飾子**: クラスの波括弧からインデントさせない（例: `public:`, `private:`, `protected:` はクラススコープ内で0列目）。
*   **テンプレート**: 宣言が長い場合は複数行に分ける。
*   **Clang-Format**: 機械的なフォーマットは、主として `.clang-format` に準拠すること。

## 6. コメントとドキュメンテーション

*   **言語**: **シェルスクリプトや設定ファイルを含むソースコード内の全てのコメントは英語で記述すること。** ドキュメント（本ガイドラインを含む）は原則日本語で記述する。
*   **目的**: 複雑なロジックについては、*何をしているか* ではなく、*なぜそれをするのか* を記述すること。
*   **契約 (Contracts)**: インターフェース定義のコメントには、自然言語（英語）で契約を記述すること。
*   **TODOs**: 仕様が未決定の項目には `TODO` コメントを使用すること。

## 7. モダンC++とベストプラクティス

*   **C++標準**: C++20以降の機能（Concepts, Coroutines, `std::span` など）を積極的に使用すること。
*   **効率的な引数渡し**: クラスや構造体は主に `const T&` で渡すこと。
*   **静的確定**: `constexpr`、`consteval`、`static_assert` を積極的に使用し、可能な限りコンパイル時に計算および検証を完了させること。
*   **所有権管理**: `std::unique_ptr` や `std::shared_ptr` は使用しないこと。
*   **RAII**: リソース（メモリ、ロック、ファイル、ハードウェア状態など）の解放をデストラクタに任せるRAII（Resource Acquisition Is Initialization）を積極的に活用すること。

### 7.1. 特定の記述制限 (Specific Coding Restrictions)

*   **`void*` の使用禁止**: 型安全性を確保するため、`void*` の使用は禁止する（`docs/orders/patterns/ioc.md` 準拠）。型が確定できない場合や、生のメモリへのアクセスが必要な場合は、型安全な代替手段（例: `std::byte*`, `std::span<std::byte>`, `binary_view`, `mutable_binary_view`）を使用すること。

## 8. 禁止されるコンテナとテクニック

*   使用が許可されない標準ライブラリコンテナおよび代替アプローチについては、`docs/orders/patterns/stdlib.md` を参照すること（例: `std::map`, `std::unordered_map` は避ける）。

## 9. インターフェース定義のワークフロー (`development_cycle.md` - セクション2から)

1.  **OOP設計**: オブジェクト指向プログラミングの原則を用いてインターフェースを設計すること。
2.  **命名**: 自然で設計レベルの名称を使用すること。
3.  **定義**: エージェントは、`docs/orders/patterns/interface.md`の原則と本統合ガイドラインに従い、コンポーネントごとにヘッダファイルにインターフェースを定義すること。
4.  **契約**: コメントに自然言語（英語）で契約を記述すること。
5.  **承認**: ユーザーの承認により、インターフェース定義タスクを完了すること。
